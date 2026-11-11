"""Serial protocol tests using paired byte streams without radio hardware."""
import copy
import json
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from serial_link import LineReader, MAX_FRAME, receive_serial_packets, send_serial_packet

class Port:
    def __init__(self):
        self.q = queue.Queue()
    def read(self, n):
        try:
            return self.q.get(timeout=.02)
        except queue.Empty:
            return b''
    def write(self, data):
        for b in data:
            self.peer.q.put(bytes([b]))
        return len(data)
    def flush(self): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass

class SerialTests(unittest.TestCase):
    def test_fragment_timeout_and_oversize_recovery(self):
        a,b=Port(),Port();a.peer=b;b.peer=a;r=LineReader()
        a.write(b'{"x":');self.assertIsNone(r.read(b))
        a.write(b'1}\n');self.assertEqual(r.read(b),b'{"x":1}')
        a.write(b'x'*(MAX_FRAME+1)+b'\nok\n')
        with self.assertRaises(ValueError): r.read(b)
        self.assertEqual(r.read(b), b'ok')

    def test_invalid_then_valid_ack_log_and_gui_queue(self):
        a,b=Port(),Port();a.peer=b;b.peer=a
        incoming=queue.Queue();stop=threading.Event()
        packet=json.loads(Path(__file__).with_name('sample_packet_no_gps.json').read_text())
        with tempfile.TemporaryDirectory() as directory:
            worker=threading.Thread(target=receive_serial_packets,
                args=(incoming,'TEST'),kwargs={'log_directory':directory,
                'stop_event':stop,'port_factory':lambda *a,**kw:b},daemon=True)
            worker.start()
            self.assertIn('Serial listening', incoming.get(timeout=2)[1])
            try:
                bad=copy.deepcopy(packet);bad['timestamp']='bad'
                reply=send_serial_packet(a,bad,timeout=2)
                self.assertEqual(reply['status'],'rejected')
                reply=send_serial_packet(a,packet,timeout=2)
                self.assertEqual(reply,{'packet_id':packet['packet_id'],'status':'accepted'})
                self.assertEqual(incoming.get(timeout=2)[0],'status')
                kind,detection=incoming.get(timeout=2)
                self.assertEqual(kind,'detection')
                self.assertEqual(detection.formatted_latitude,'Unavailable')
                rows=next(Path(directory).glob('*.jsonl')).read_text().splitlines()
                self.assertEqual(len(rows),1)
                self.assertEqual(json.loads(rows[0])['packet'],packet)
            finally:
                stop.set();worker.join(2)
                self.assertFalse(worker.is_alive())

    def test_wrong_ack_is_not_accepted(self):
        a,b=Port(),Port();a.peer=b;b.peer=a
        b.write(b'{"packet_id":999,"status":"accepted"}\n')
        with self.assertRaises(TimeoutError):
            send_serial_packet(a, {'packet_id':1},timeout=.05)

if __name__=='__main__': unittest.main()
