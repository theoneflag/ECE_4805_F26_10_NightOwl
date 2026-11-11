"""GPS freshness, provenance, camera and transport regression checks."""
import copy
import importlib.util
import json
from pathlib import Path
import queue
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace as NS
from gps_provider import GPSCache, SimulatedGPS, MavlinkGPS, make_provider
from detection import Detection
from details_panel import selected_data
from receiver import validate_packet
from serial_link import receive_serial_packets, send_serial_packet
from test_serial_link import Port

class Clock:
    value=100.0
    def __call__(self):return self.value

def packet():return json.loads(Path(__file__).with_name('sample_packet_no_gps.json').read_text())
def raw(stamp=1,fix=3,lat=372211000,lon=-804234000,alt=24800,system=1):
    return NS(get_type=lambda:'GPS_RAW_INT',get_srcSystem=lambda:system,get_srcComponent=lambda:1,
              time_usec=stamp,fix_type=fix,lat=lat,lon=lon,alt=alt)

class GPSTests(unittest.TestCase):
    def test_sender_waits_before_timestamp_and_gps_snapshot(self):
        from unittest.mock import patch
        from sender import simulated_packets
        clock=Clock();gps=SimulatedGPS(clock=clock,wall=clock)
        def advance(seconds):clock.value+=seconds
        with patch('sender.time.time',side_effect=clock), patch('sender.time.sleep',side_effect=advance):
            stream=simulated_packets(packet(),2,100,42,interval=2)
            first=gps.attach(next(stream));second=gps.attach(next(stream))
        self.assertEqual(first['timestamp'],100)
        self.assertEqual(second['timestamp'],102)
        self.assertEqual(second['gps']['received_at'],second['timestamp'])

    def test_zero_coordinates_staleness_and_frozen_detection_snapshot(self):
        clock=Clock();gps=GPSCache(max_age=3,clock=clock,wall=clock)
        gps.update({'latitude':0,'longitude':0,'altitude_m':-2})
        p=gps.attach(packet());validate_packet(p)
        clock.value+=4
        self.assertEqual(p['position']['latitude'],0)
        self.assertEqual(p['gps']['age_s'],0)
        self.assertEqual(gps.snapshot()['gps']['status'],'stale')
        self.assertIsNone(gps.snapshot()['position'])
        p['position']['latitude']=99;clock.value-=4
        self.assertEqual(gps.snapshot()['position']['latitude'],0)

    def test_invalid_readings_clear_old_fix_and_default_is_disabled(self):
        for bad in [float('nan'),float('inf'),True,91,10**400]:
            gps=GPSCache();gps.update({'latitude':10,'longitude':20,'altitude_m':3})
            self.assertFalse(gps.update({'latitude':bad,'longitude':20,'altitude_m':3}))
            self.assertEqual(gps.snapshot()['gps']['status'],'invalid')
            self.assertIsNone(gps.snapshot()['position'])
        p=packet();p['position']={'latitude':1,'longitude':2,'altitude_m':3}
        out=GPSCache('disabled').attach(p);validate_packet(out)
        self.assertIsNone(out['position']);self.assertFalse(out['gps']['simulated'])

    def test_simulation_missing_stale_disconnected_and_recovery(self):
        clock=Clock();gps=SimulatedGPS(clock=clock,wall=clock)
        for elapsed,status in [(0,'valid'),(11,'no_fix'),(16,'valid'),(25,'stale'),
                               (31,'disconnected'),(36,'valid'),(46,'valid')]:
            clock.value=100+elapsed;p=gps.attach(packet());validate_packet(p)
            self.assertEqual(p['gps']['status'],status)
            self.assertTrue(p['gps']['simulated'])
            self.assertEqual(p['position'] is not None,status=='valid')

    def test_display_export_and_receiver_keep_simulation_provenance(self):
        good=SimulatedGPS().attach(packet());d=Detection.from_packet(good)
        self.assertIn('(SIM)',d.formatted_latitude)
        self.assertIn('SIMULATED TEST DATA',d.gps_summary)
        for choices in [{'GPS location'},{'Altitude'}]:
            self.assertTrue(selected_data(d,choices)['gps']['simulated'])
            self.assertEqual(selected_data(d,choices)['gps']['altitude_reference'],'MSL')
        self.assertNotIn('gps',selected_data(d,{'Packet ID'}))
        for key,value in [('simulated',False),('status','stale'),('age_s',-1),
                          ('age_s',float('nan')),('age_s',10**400),('received_at',True),
                          ('altitude_reference','relative'),('source','unknown')]:
            bad=copy.deepcopy(good);bad['gps'][key]=value
            with self.assertRaises(ValueError):validate_packet(bad)
        validate_packet(packet())

    def test_mavlink_units_source_frozen_time_fix_loss_and_restart(self):
        clock=Clock();gps=MavlinkGPS('TEST',clock=clock,wall=clock)
        gps.ingest(raw(system=2));self.assertEqual(gps.snapshot()['gps']['status'],'waiting')
        gps.ingest(raw());self.assertEqual(gps.snapshot()['position'],
            {'latitude':37.2211,'longitude':-80.4234,'altitude_m':24.8})
        clock.value+=4;gps.ingest(raw())
        self.assertEqual(gps.snapshot()['gps']['status'],'stale')
        gps.ingest(raw(stamp=2,lat=0,lon=0,alt=0))
        self.assertEqual(gps.snapshot()['position']['latitude'],0)
        gps.ingest(raw(stamp=3,fix=2));self.assertEqual(gps.snapshot()['gps']['status'],'no_fix')
        gps.ingest(raw(stamp=4));self.assertIsNotNone(gps.snapshot()['position'])
        gps.ingest(raw(stamp=1));self.assertIsNone(gps.snapshot()['position'])
        gps.ingest(raw(stamp=2));self.assertIsNotNone(gps.snapshot()['position'])

    def test_invalid_payload_and_configuration(self):
        gps=MavlinkGPS('TEST');gps.ingest(raw(lat=1000000000))
        self.assertEqual(gps.snapshot()['gps']['status'],'invalid')
        args=NS(gps='mavlink',gps_max_age=3,gps_device='COM8',gps_baud=57600,
                gps_system_id=1,gps_component_id=1)
        with self.assertRaises(ValueError):make_provider(args,'com8')
        for age in [0,-1,float('nan'),float('inf')]:
            args.gps_max_age=age
            with self.assertRaises(ValueError):make_provider(args)

    def test_serial_image_gps_log_and_rejection(self):
        from test_radio_images import image_packet
        a,b=Port(),Port();a.peer=b;b.peer=a;incoming=queue.Queue();stop=threading.Event()
        with tempfile.TemporaryDirectory() as tmp:
            thread=threading.Thread(target=receive_serial_packets,args=(incoming,'TEST'),
                kwargs={'log_directory':Path(tmp)/'logs','image_directory':tmp,'stop_event':stop,
                        'port_factory':lambda *a,**kw:b},daemon=True)
            thread.start();incoming.get(timeout=2)
            try:
                p=SimulatedGPS().attach(image_packet())
                self.assertEqual(send_serial_packet(a,p,timeout=3)['status'],'accepted')
                kind,d=incoming.get(timeout=2)
                self.assertEqual(kind,'detection');self.assertTrue(d.gps['simulated'])
                self.assertTrue((Path(tmp)/d.image_path).is_file())
                saved=json.loads(next((Path(tmp)/'logs').glob('*.jsonl')).read_text())['packet']
                self.assertEqual(saved['gps'],p['gps']);self.assertNotIn('thumbnail_jpeg_b64',saved)
                bad=copy.deepcopy(p);bad['gps']['simulated']=False
                self.assertEqual(send_serial_packet(a,bad,timeout=3)['status'],'rejected')
            finally:stop.set();thread.join(2)

    def test_camera_adapter_attaches_once_at_packet_creation(self):
        from Night_Owl_Live_Radio import adapt
        source='''import time
import threading
import subprocess
SNAPSHOT_DIR = 'old'
packet_id = 0
def get_gps():
    return {'latitude': 1, 'longitude': 2}
def send_packet(packet):
    pass
def trigger_packet(label, confidence, bbox, frame):
    global packet_id
    packet_id += 1
    packet = {'packet_id': packet_id, 'timestamp': time.time(),
              'object': {'type': label, 'confidence': confidence},
              'position': get_gps(), 'thumbnail': None}
    threading.Thread(target=send_packet, args=(packet,), daemon=True).start()
def stream_capture():
    subprocess.Popen(['unused'])
'''
        sent=[];clock=Clock();gps=SimulatedGPS(clock=clock,wall=clock)
        env={'_gps':gps,'_radio':NS(enqueue=sent.append)}
        exec(adapt(source,'/tmp/test','camera_fixture.py'),env)
        env['trigger_packet']('person',.9,(1,2,3,4),None)
        clock.value+=12
        self.assertTrue(sent[0]['gps']['simulated'])
        self.assertEqual(sent[0]['gps']['status'],'valid');validate_packet(sent[0])
        env['trigger_packet']('person',.9,(1,2,3,4),None)
        self.assertEqual(sent[1]['gps']['status'],'no_fix')

    def test_reader_disconnect_reconnect(self):
        messages=queue.Queue();attempts=[]
        class Connection:
            def recv_match(self,**kw):
                try:return messages.get(timeout=.02)
                except queue.Empty:return None
            def close(self):pass
        def factory(*a,**kw):
            attempts.append(1)
            if len(attempts)==1:raise OSError('test disconnect')
            return Connection()
        gps=MavlinkGPS('TEST',connection_factory=factory).start()
        try:
            deadline=time.monotonic()+3
            while len(attempts)<2 and time.monotonic()<deadline:time.sleep(.01)
            self.assertGreaterEqual(len(attempts),2);messages.put(raw())
            while gps.snapshot()['gps']['status']!='valid' and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(gps.snapshot()['gps']['status'],'valid')
        finally:gps.close()
        self.assertEqual(gps.snapshot()['gps']['status'],'disconnected')

    @unittest.skipUnless(importlib.util.find_spec('pymavlink'),'Optional pymavlink not installed')
    def test_actual_pymavlink_frame(self):
        from pymavlink.dialects.v20 import common
        encoder=common.MAVLink(None,srcSystem=1,srcComponent=1)
        msg=common.MAVLink_gps_raw_int_message(123456,3,372211000,-804234000,24800,100,100,0,0,10)
        decoded=common.MAVLink(None).parse_char(msg.pack(encoder))
        gps=MavlinkGPS('TEST');gps.ingest(decoded)
        self.assertEqual(gps.snapshot()['position']['altitude_m'],24.8)
        self.assertEqual(gps.snapshot()['gps']['sensor_time_usec'],123456)

    @unittest.skipUnless(importlib.util.find_spec('pymavlink'),'Optional pymavlink not installed')
    def test_actual_reader_over_local_udp(self):
        import socket
        from pymavlink import mavutil
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(('127.0.0.1',0)); port=probe.getsockname()[1]
        gps=MavlinkGPS(f'udpin:127.0.0.1:{port}').start()
        transmitter=mavutil.mavlink_connection(f'udpout:127.0.0.1:{port}',
                                               source_system=1,source_component=1)
        try:
            deadline=time.monotonic()+3
            while gps.snapshot()['gps']['status']!='valid' and time.monotonic()<deadline:
                transmitter.mav.gps_raw_int_send(123456,3,372211000,-804234000,24800,
                                                 100,100,0,0,10)
                time.sleep(.03)
            snapshot=gps.snapshot()
            self.assertEqual(snapshot['gps']['status'],'valid')
            self.assertFalse(snapshot['gps']['simulated'])
            self.assertEqual(snapshot['position']['altitude_m'],24.8)
        finally:
            transmitter.close();gps.close()

if __name__=='__main__':unittest.main()
