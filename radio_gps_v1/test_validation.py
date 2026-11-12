"""Run with: py -m unittest -v test_validation"""
import copy
import json
import multiprocessing
from pathlib import Path
import socket
import tempfile
import unittest

from detection import Detection
from packet_receiver import receive_packets
from receiver import validate_packet
from sender import send_packet

BASE = Path(__file__).resolve().parent


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.packet = json.loads((BASE / 'sample_packet.json').read_text())

    def test_supplied_packet_and_zero_coordinates(self):
        validate_packet(self.packet)
        self.packet['position'] = {'latitude': 0, 'longitude': 0, 'altitude_m': -5}
        validate_packet(self.packet)
        detection = Detection.from_packet(self.packet)
        self.assertEqual(detection.formatted_latitude, '0.000000')
        self.assertEqual(detection.formatted_longitude, '0.000000')

    def test_absent_gps_forms(self):
        for position in [None, {}, {'latitude': None, 'longitude': None}, {'altitude_m': 10}]:
            with self.subTest(position=position):
                self.packet['position'] = position
                validate_packet(self.packet)
                detection = Detection.from_packet(self.packet)
                self.assertEqual(detection.formatted_latitude, 'Unavailable')
                self.assertEqual(detection.formatted_longitude, 'Unavailable')
                self.assertIsNone(detection.latitude)
        del self.packet['position']
        validate_packet(self.packet)
        self.assertIsNone(Detection.from_packet(self.packet).longitude)

    def test_invalid_values_are_rejected(self):
        cases = [
            ('packet_id', True), ('packet_id', -1), ('packet_id', 1.5),
            ('timestamp', 'bad'), ('timestamp', True), ('timestamp', -1),
            ('timestamp', float('inf')), ('timestamp', float('nan')),
            ('timestamp', 10**400), ('timestamp', 1e100),
            ('object', {'type': ' ', 'confidence': .9}),
            ('object', {'type': 'person', 'confidence': True}),
            ('object', {'type': 'person', 'confidence': 1.1}),
            ('object', {'type': 'person', 'confidence': float('nan')}),
            ('position', []), ('position', {'latitude': 10}),
            ('position', {'latitude': 91, 'longitude': 0}),
            ('position', {'latitude': 0, 'longitude': 181}),
            ('position', {'latitude': False, 'longitude': 0}),
            ('position', {'altitude_m': float('inf')}),
            ('bbox', {'x': 0, 'y': 0, 'width': 0, 'height': 5}),
            ('bbox', {'x': -1, 'y': 0, 'width': 5, 'height': 5}),
            ('bbox', {'x': 0, 'y': 0, 'width': 5}),
            ('thumbnail', 27), ('thumbnail', 'bad\x00.jpg'),
        ]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                packet = copy.deepcopy(self.packet)
                packet[field] = value
                with self.assertRaises(ValueError):
                    validate_packet(packet)
        with self.assertRaises(ValueError):
            validate_packet([])

    def test_socket_rejects_bad_timestamp_then_accepts_no_gps(self):
        with tempfile.TemporaryDirectory() as directory:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            messages = multiprocessing.Queue()
            process = multiprocessing.Process(
                target=receive_packets, args=(messages, '127.0.0.1', port, directory))
            process.start()
            try:
                for _ in range(5):
                    kind, value = messages.get(timeout=5)
                    if kind == 'status' and str(value).startswith('Listening'):
                        break
                else:
                    self.fail('Receiver did not start')
                invalid = json.loads((BASE / 'sample_packet_invalid.json').read_text())
                response = send_packet(invalid, '127.0.0.1', port)
                self.assertEqual(response['status'], 'rejected')
                self.assertIn('timestamp', response['error'])
                no_gps = json.loads((BASE / 'sample_packet_no_gps.json').read_text())
                self.assertEqual(send_packet(no_gps, '127.0.0.1', port)['status'], 'accepted')
                self.assertEqual(send_packet(self.packet, '127.0.0.1', port)['status'], 'accepted')
                logged = [json.loads(line) for line in next(Path(directory).glob('*.jsonl')).read_text().splitlines()]
                self.assertEqual(len(logged), 2)
                self.assertIsNone(logged[0]['packet']['position']['latitude'])
                self.assertEqual(logged[1]['packet']['packet_id'], self.packet['packet_id'])
                received = []
                for _ in range(4):
                    kind, value = messages.get(timeout=5)
                    if kind == 'detection':
                        received.append(value)
                        if len(received) == 2:
                            break
                self.assertEqual(len(received), 2)
                self.assertEqual(received[0].formatted_latitude, 'Unavailable')
            finally:
                process.terminate()
                process.join(timeout=5)
                messages.close()
                messages.cancel_join_thread()


if __name__ == '__main__':
    unittest.main()
