import contextlib
import copy
import io
import json
import queue
import socket
import sys
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import patch

BASE_STATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_STATION))

import packet_receiver
import protocol
import receiver
import sender
from detection import Detection

SAMPLE = json.loads((BASE_STATION / "sample_packet.json").read_text())


def frame(packet):
    return json.dumps(packet, ensure_ascii=False).encode("utf-8") + b"\n"


class MemorySocket:
    """Script TCP reads, including fragmentation, EOF and network errors."""

    def __init__(self, chunks=(), send_error=None):
        self.chunks = deque(chunks)
        self.sent = bytearray()
        self.send_error = send_error
        self.timeout = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def settimeout(self, timeout):
        self.timeout = timeout

    def connect(self, address):
        pass

    def recv(self, size):
        if not self.chunks:
            return b""
        chunk = self.chunks.popleft()
        if isinstance(chunk, Exception):
            raise chunk
        if len(chunk) > size:
            self.chunks.appendleft(chunk[size:])
        return chunk[:size]

    def sendall(self, data):
        if self.send_error:
            raise self.send_error
        self.sent.extend(data)

    def replies(self):
        return [protocol.decode_json(line) for line in bytes(self.sent).splitlines()]


class StopServer(Exception):
    pass


class Listener(MemorySocket):
    def __init__(self, connections):
        super().__init__()
        self.connections = iter(connections)

    def setsockopt(self, *args):
        pass

    def bind(self, address):
        pass

    def listen(self):
        pass

    def accept(self):
        try:
            return next(self.connections), ("test", 5001)
        except StopIteration:
            raise StopServer from None


class ProtocolTests(unittest.TestCase):
    def test_fragmented_utf8_and_large_message(self):
        packet = {**SAMPLE, "thumbnail": "é" + "x" * 6000}
        data = frame(packet)
        cut = data.index("é".encode()) + 1
        conn = MemorySocket([data[:cut], data[cut:]])
        self.assertEqual(protocol.JsonStream(conn).receive(), packet)

    def test_every_byte_can_arrive_separately(self):
        data = frame(SAMPLE)
        conn = MemorySocket(bytes([byte]) for byte in data)
        self.assertEqual(protocol.JsonStream(conn).receive(), SAMPLE)

    def test_coalesced_messages_and_escaped_newline(self):
        packet = {**SAMPLE, "thumbnail": "line1\nline2.jpg"}
        conn = MemorySocket([frame(packet) + frame(SAMPLE)])
        stream = protocol.JsonStream(conn)
        self.assertEqual(stream.receive(), packet)
        self.assertEqual(stream.receive(), SAMPLE)
        with self.assertRaises(EOFError):
            stream.receive()

    def test_encoding_is_strict_and_framed(self):
        conn = MemorySocket()
        protocol.send_json(conn, SAMPLE)
        self.assertTrue(conn.sent.endswith(b"\n"))
        self.assertEqual(conn.replies(), [SAMPLE])
        for number in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(number=number):
                conn = MemorySocket()
                with self.assertRaises(ValueError):
                    protocol.send_json(conn, {"packet_id": number})
                self.assertFalse(conn.sent)

    def test_nonfinite_numbers_are_rejected_anywhere(self):
        for number in (b"NaN", b"Infinity", b"-Infinity", b"1e309", b"-1e309"):
            with self.subTest(number=number), self.assertRaises(ValueError):
                protocol.JsonStream(MemorySocket([b'{"extra":' + number + b'}\n'])).receive()

    def test_invalid_json_and_utf8_report_immediately(self):
        for data in (b'{"broken":}\n', b'{"name":"\xff"}\n', b"\n"):
            with self.subTest(data=data):
                conn = MemorySocket([data, AssertionError("Unexpected extra read")])
                with self.assertRaisesRegex(ValueError, "Invalid JSON"):
                    protocol.JsonStream(conn).receive()

    def test_missing_delimiter_and_partial_disconnect(self):
        for data in (b'{"unfinished":', frame(SAMPLE)[:-1]):
            with self.subTest(data=data), self.assertRaisesRegex(ValueError, "terminating newline"):
                protocol.JsonStream(MemorySocket([data])).receive()

    def test_idle_timeout_and_clean_disconnect(self):
        for chunks in ([], [socket.timeout("timed out")]):
            with self.subTest(chunks=chunks), self.assertRaises(EOFError):
                protocol.JsonStream(MemorySocket(chunks)).receive()

    def test_partial_timeout(self):
        conn = MemorySocket([b'{"unfinished":', socket.timeout("timed out")])
        with self.assertRaisesRegex(ValueError, "Timed out"):
            protocol.JsonStream(conn).receive()

    def test_size_limits_apply_to_each_message(self):
        with patch.object(protocol, "MAX_PACKET_BYTES", 10):
            stream = protocol.JsonStream(MemorySocket([b'"12345678"\n' * 3]))
            self.assertEqual([stream.receive() for _ in range(3)], ["12345678"] * 3)
            for data in (b'"123456789"\n', b'"123456789"'):
                with self.subTest(data=data), self.assertRaisesRegex(ValueError, "too large"):
                    protocol.JsonStream(MemorySocket([data])).receive()
            conn = MemorySocket()
            with self.assertRaisesRegex(ValueError, "too large"):
                protocol.send_json(conn, "123456789")
            self.assertFalse(conn.sent)

class ValidationTests(unittest.TestCase):
    def test_sample_is_valid_and_displayable(self):
        receiver.validate_packet(SAMPLE)
        self.assertTrue(Detection.from_packet(SAMPLE).formatted_time)

    def test_optional_fields_can_be_absent(self):
        packet = copy.deepcopy(SAMPLE)
        for key in ("bbox", "thumbnail"):
            del packet[key]
        del packet["position"]["altitude_m"]
        receiver.validate_packet(packet)
        with contextlib.redirect_stdout(io.StringIO()):
            receiver.print_packet(packet)

    def test_explicitly_unavailable_gps_is_valid(self):
        packet = {**SAMPLE, "position": None}
        receiver.validate_packet(packet)
        detection = Detection.from_packet(packet)
        self.assertIsNone(detection.latitude)
        self.assertIsNone(detection.longitude)
        self.assertIsNone(detection.altitude_m)
        self.assertEqual(detection.formatted_latitude, "Unavailable")
        self.assertEqual(detection.formatted_longitude, "Unavailable")
        with contextlib.redirect_stdout(io.StringIO()):
            receiver.print_packet(packet)

    def test_available_gps_keeps_its_precision(self):
        detection = Detection.from_packet(SAMPLE)
        self.assertEqual(detection.formatted_latitude, "37.229600")
        self.assertEqual(detection.formatted_longitude, "-80.424300")

    def test_required_structure(self):
        cases = [None, [], "packet", 1, True]
        for field in ("packet_id", "timestamp", "object", "position"):
            packet = copy.deepcopy(SAMPLE)
            del packet[field]
            cases.append(packet)
        for field in ("object", "position"):
            cases.append({**SAMPLE, field: []})
        for packet in cases:
            with self.subTest(packet=packet), self.assertRaises(ValueError):
                receiver.validate_packet(packet)

    def test_invalid_ids_and_timestamps(self):
        cases = {
            "packet_id": [True, False, None, "27", 27.0, {}, float("nan"), float("inf")],
            "timestamp": [True, None, "2026-11-16T12:00:00Z", 1791918000000,
                          float("nan"), float("inf"), 10 ** 1000],
        }
        for field, values in cases.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    receiver.validate_packet({**SAMPLE, field: value})

    def test_invalid_detection_values(self):
        cases = [
            ("object", "type", [None, "", "  ", True, 42]),
            ("object", "confidence", [None, True, "0.8", -0.1, 1.1, float("nan"), float("inf")]),
            ("position", "latitude", [None, True, "37", -91, 91, float("nan")]),
            ("position", "longitude", [None, False, "-80", -181, 181, float("inf")]),
            ("position", "altitude_m", [True, "10", float("inf"), 10 ** 1000]),
        ]
        for section, field, values in cases:
            for value in values:
                packet = copy.deepcopy(SAMPLE)
                packet[section][field] = value
                with self.subTest(section=section, field=field, value=value), self.assertRaises(ValueError):
                    receiver.validate_packet(packet)
        for value in (True, [], 123):
            with self.subTest(thumbnail=value), self.assertRaises(ValueError):
                receiver.validate_packet({**SAMPLE, "thumbnail": value})


class ReceiverTests(unittest.TestCase):
    def handle(self, conn):
        packets, errors = [], []
        receiver.handle_connection(conn, packets.append, errors.append)
        return packets, errors

    def test_large_fragmented_and_coalesced_requests(self):
        packet = {**SAMPLE, "packet_id": 28, "thumbnail": "é" + "x" * 6000}
        data = frame(SAMPLE) + frame(packet)
        conn = MemorySocket([data[:23], data[23:]])
        packets, errors = self.handle(conn)
        self.assertEqual(packets, [SAMPLE, packet])
        self.assertFalse(errors)
        self.assertEqual(conn.replies(), [
            {"packet_id": 27, "status": "accepted"},
            {"packet_id": 28, "status": "accepted"},
        ])

    def test_bad_packets_get_rejection_and_no_detection(self):
        for data in (b'{"bad":}\n', b'{"bad":"\xff"}\n', b"null\n",
                     frame({**SAMPLE, "packet_id": True}),
                     frame({**SAMPLE, "timestamp": "wrong"}),
                     frame(SAMPLE).replace(b'"packet_id": 27', b'"packet_id": 1e309')):
            with self.subTest(data=data):
                conn = MemorySocket([data])
                packets, errors = self.handle(conn)
                self.assertFalse(packets)
                self.assertTrue(errors)
                self.assertEqual(conn.replies()[0]["status"], "rejected")

    def test_deeply_nested_input_does_not_stop_receiver(self):
        # Some Python versions raise RecursionError during parsing; others
        # parse this successfully. Either way, an array is not a packet.
        conn = MemorySocket([b"[" * 2000 + b"0" + b"]" * 2000 + b"\n"])
        packets, errors = self.handle(conn)
        self.assertFalse(packets)
        self.assertTrue(errors)
        self.assertEqual(conn.replies()[0]["status"], "rejected")

    def test_incomplete_and_oversized_frames_are_rejected(self):
        cases = [
            [frame(SAMPLE)[:-1]],
            [frame(SAMPLE)[:-1], socket.timeout("timed out")],
            [b"x" * (protocol.MAX_PACKET_BYTES + 1)],
        ]
        for chunks in cases:
            with self.subTest(size=len(chunks[0])):
                conn = MemorySocket(chunks)
                packets, errors = self.handle(conn)
                self.assertFalse(packets)
                self.assertTrue(errors)
                self.assertEqual(conn.replies()[0]["status"], "rejected")

    def test_disconnect_during_reply_is_contained(self):
        conn = MemorySocket([frame(SAMPLE)], send_error=BrokenPipeError("disconnected"))
        packets, errors = self.handle(conn)
        self.assertEqual(packets, [SAMPLE])
        self.assertIn("Could not send reply", errors[-1])

    def test_console_recovers_after_bad_connection(self):
        bad = MemorySocket([b'{"name":"\xff"}\n'])
        good = MemorySocket([frame(SAMPLE)])
        with patch.object(receiver.socket, "socket", return_value=Listener([bad, good])), \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(StopServer):
            receiver.main()
        self.assertEqual(bad.replies()[0]["status"], "rejected")
        self.assertEqual(good.replies()[0]["status"], "accepted")

    def test_gui_recovers_and_queues_multiple_displayable_detections(self):
        bad = MemorySocket([b"NaN\n"])
        good = MemorySocket([frame(SAMPLE) * 2])
        incoming = queue.Queue()
        with patch.object(packet_receiver.socket, "socket", return_value=Listener([bad, good])), \
                self.assertRaises(StopServer):
            packet_receiver.receive_packets(incoming)
        messages = list(incoming.queue)
        detections = [data for kind, data in messages if kind == "detection"]
        self.assertEqual(len(detections), 2)
        self.assertTrue(all(detection.formatted_time for detection in detections))
        self.assertTrue(any(kind == "status" and "rejected" in data for kind, data in messages))
        self.assertEqual(len(good.replies()), 2)


class SenderTests(unittest.TestCase):
    def run_sender(self, conn):
        output = io.StringIO()
        with patch.object(sender.socket, "socket", return_value=conn), \
                contextlib.redirect_stdout(output):
            sender.main([])
        return output.getvalue()

    def test_fragmented_reply_and_sample_path(self):
        reply = frame({"packet_id": 27, "status": "accepted"})
        conn = MemorySocket([reply[:5], reply[5:]])
        output = self.run_sender(conn)
        self.assertIn("RECEIVER RESPONSE", output)
        self.assertNotIn("Error:", output)
        self.assertEqual(conn.replies(), [SAMPLE])
        self.assertEqual(conn.timeout, protocol.SOCKET_TIMEOUT)

    def test_invalid_or_missing_responses_are_reported(self):
        replies = [b"", b"[]\n", b'{"broken":}\n', b'{"name":"\xff"}\n',
                   frame({"packet_id": 28, "status": "accepted"}),
                   frame({"packet_id": True, "status": "accepted"}),
                   frame({"packet_id": 27, "status": "unknown"}),
                   b'{"packet_id":27,"status":"accepted"}']
        for reply in replies:
            with self.subTest(reply=reply):
                output = self.run_sender(MemorySocket([reply]))
                self.assertIn("Error:", output)
                self.assertNotIn("RECEIVER RESPONSE", output)

    def test_bad_sample_is_not_sent(self):
        for data in (b'{"packet_id": NaN}', frame({**SAMPLE, "timestamp": "invalid"})):
            with self.subTest(data=data), patch.object(Path, "read_bytes", return_value=data):
                conn = MemorySocket()
                self.assertIn("sample_packet.json is invalid", self.run_sender(conn))
                self.assertFalse(conn.sent)

    def test_reply_timeouts_are_reported(self):
        for chunks in ([socket.timeout("timed out")], [b'{"status":', socket.timeout("timed out")]):
            with self.subTest(chunks=chunks):
                output = self.run_sender(MemorySocket(chunks))
                self.assertIn("Error:", output)
                self.assertNotIn("RECEIVER RESPONSE", output)


if __name__ == "__main__":
    unittest.main()
