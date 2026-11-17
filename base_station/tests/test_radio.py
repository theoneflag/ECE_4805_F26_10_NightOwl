import contextlib
import io
import json
import queue
import sys
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import Mock, patch

BASE_STATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_STATION))

import packet_receiver
import radio
import sender
from protocol import encode_json

SAMPLE = json.loads((BASE_STATION / "sample_packet.json").read_text())


class MemorySerial:
    def __init__(self, chunks=(), max_write=None):
        self.chunks = deque(chunks)
        self.sent = bytearray()
        self.max_write = max_write

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read_until(self, expected, size):
        if not self.chunks:
            return b""
        chunk = self.chunks.popleft()
        if isinstance(chunk, Exception):
            raise chunk
        newline = chunk.find(expected)
        length = min(size, newline + len(expected) if newline >= 0 else len(chunk))
        if length < len(chunk):
            self.chunks.appendleft(chunk[length:])
        return chunk[:length]

    def write(self, data):
        count = len(data) if self.max_write is None else min(self.max_write, len(data))
        self.sent.extend(data[:count])
        return count

    def flush(self):
        pass

    def replies(self):
        return [json.loads(line) for line in self.sent.splitlines()]


class RadioTests(unittest.TestCase):
    def test_partial_utf8_survives_idle_reads(self):
        packet = {**SAMPLE, "thumbnail": "é.jpg"}
        data = json.dumps(packet, ensure_ascii=False).encode() + b"\n"
        cut = data.index("é".encode()) + 1
        device = MemorySerial([b"", data[:cut], b"", b"", data[cut:]])
        self.assertEqual(radio.RadioStream(device).receive(), packet)

    def test_multiple_packets_in_one_radio_read(self):
        device = MemorySerial([encode_json(SAMPLE) * 2])
        stream = radio.RadioStream(device)
        self.assertEqual(stream.receive(), SAMPLE)
        self.assertEqual(stream.receive(), SAMPLE)

    def test_bad_json_does_not_destroy_next_frame(self):
        for bad in (b'{"invalid":}\n', b'"\xff"\n', b"NaN\n"):
            with self.subTest(bad=bad):
                stream = radio.RadioStream(MemorySerial([bad + encode_json(SAMPLE)]))
                with self.assertRaises(ValueError):
                    stream.receive()
                self.assertEqual(stream.receive(), SAMPLE)

    def test_oversized_message_recovers_at_next_newline(self):
        with patch.object(radio, "MAX_PACKET_BYTES", 10):
            stream = radio.RadioStream(MemorySerial([b"x" * 30 + b'\n{"ok":1}\n']))
            with self.assertRaisesRegex(ValueError, "too large"):
                stream.receive()
            self.assertEqual(stream.receive(), {"ok": 1})

    def test_exact_size_boundary(self):
        with patch.object(radio, "MAX_PACKET_BYTES", 10):
            stream = radio.RadioStream(MemorySerial([b'"12345678"\n']))
            self.assertEqual(stream.receive(), "12345678")

    def test_response_wait_is_bounded(self):
        stream = radio.RadioStream(MemorySerial([b'{"partial":']))
        with patch.object(radio.time, "monotonic", side_effect=[0, 0, 11]):
            with self.assertRaisesRegex(TimeoutError, "No complete JSON reply"):
                stream.receive(timeout=10)

    def test_serial_writes_are_completed(self):
        device = MemorySerial(max_write=7)
        radio.RadioStream(device).send(SAMPLE)
        self.assertEqual(device.replies(), [SAMPLE])

    def test_zero_length_write_is_reported(self):
        with self.assertRaisesRegex(OSError, "stopped accepting"):
            radio.RadioStream(MemorySerial(max_write=0)).send(SAMPLE)

    def test_receiver_recovers_after_bad_packet_and_waits_when_idle(self):
        device = MemorySerial([b"", b"null\n", encode_json(SAMPLE), OSError("unplugged")])
        incoming = queue.Queue()
        with patch.object(packet_receiver, "open_radio", return_value=device) as opener:
            packet_receiver.receive_packets(incoming, serial_port="JSON-RADIO", baud=57600)
        opener.assert_called_once_with("JSON-RADIO", 57600)
        messages = list(incoming.queue)
        detections = [data for kind, data in messages if kind == "detection"]
        self.assertEqual(len(detections), 1)
        self.assertTrue(detections[0].formatted_time)
        self.assertEqual([reply["status"] for reply in device.replies()], ["rejected", "accepted"])
        self.assertIn("unplugged", messages[-1][1])

    def test_radio_permission_error_reaches_gui_status(self):
        incoming = queue.Queue()
        with patch.object(packet_receiver, "open_radio", side_effect=PermissionError("denied")):
            packet_receiver.receive_packets(incoming, serial_port="JSON-RADIO")
        self.assertIn("Radio receiver error", incoming.get_nowait()[1])

    def test_sender_uses_selected_serial_port_and_baud(self):
        device = MemorySerial([encode_json({"packet_id": 27, "status": "accepted"})])
        output = io.StringIO()
        with patch.object(sender, "open_radio", return_value=device) as opener, \
                contextlib.redirect_stdout(output):
            sender.main(["--serial-port", "PI-JSON-RADIO", "--baud", "115200"])
        opener.assert_called_once_with("PI-JSON-RADIO", 115200)
        self.assertEqual(device.replies(), [SAMPLE])
        self.assertIn("RECEIVER RESPONSE", output.getvalue())

    def test_sender_reports_radio_timeout(self):
        output = io.StringIO()
        with patch.object(sender, "open_radio", return_value=MemorySerial()), \
                patch.object(sender.RadioStream, "receive", side_effect=TimeoutError("No reply")), \
                contextlib.redirect_stdout(output):
            sender.main(["--serial-port", "PI-JSON-RADIO"])
        self.assertIn("Communication error: No reply", output.getvalue())

    def test_sender_ignores_stale_and_corrupt_radio_acknowledgments(self):
        stream = Mock()
        accepted = {"packet_id": 27, "status": "accepted"}
        stream.receive.side_effect = [
            {"packet_id": 26, "status": "accepted"},
            {"packet_id": True, "status": "accepted"},
            {"status": "accepted"},
            [],
            ValueError("Corrupt JSON"),
            accepted,
        ]
        self.assertEqual(sender.send_radio_packet(stream, SAMPLE), accepted)
        stream.send.assert_called_once_with(SAMPLE)

    def test_radio_rejection_is_reported_without_retransmission(self):
        stream = Mock()
        rejection = {"status": "rejected", "error": "Invalid packet"}
        stream.receive.return_value = rejection
        self.assertEqual(sender.send_radio_packet(stream, SAMPLE), rejection)
        stream.send.assert_called_once_with(SAMPLE)

    def test_corrupt_radio_acknowledgments_cannot_extend_timeout(self):
        stream = Mock()
        stream.receive.side_effect = ValueError("Corrupt JSON")
        with patch.object(sender.time, "monotonic", side_effect=[0, 0, 11]):
            with self.assertRaisesRegex(TimeoutError, "No matching radio"):
                sender.send_radio_packet(stream, SAMPLE, timeout=10)
        stream.send.assert_called_once_with(SAMPLE)

    def test_pyserial_loopback(self):
        try:
            import serial
        except ImportError:
            self.skipTest("pyserial not installed")
        with serial.serial_for_url("loop://", timeout=0.01) as device:
            stream = radio.RadioStream(device)
            stream.send(SAMPLE)
            self.assertEqual(stream.receive(timeout=1), SAMPLE)


if __name__ == "__main__":
    unittest.main()
