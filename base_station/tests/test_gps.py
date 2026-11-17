import contextlib
import io
import json
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

BASE_STATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_STATION))
sys.path.insert(0, str(BASE_STATION.parent))

import mavlink_gps as gps
import Night_Owl_Live_Radio as live
from detection import Detection
from receiver import validate_packet


class Clock:
    def __init__(self):
        self.now = 100

    def __call__(self):
        return self.now


class Message(SimpleNamespace):
    def get_type(self):
        return self.kind

    def get_srcSystem(self):
        return getattr(self, "system", 7)

    def get_srcComponent(self):
        return getattr(self, "component", 1)


def heartbeat(**fields):
    return Message(kind="HEARTBEAT", autopilot=3, **fields)


def raw(**fields):
    defaults = dict(kind="GPS_RAW_INT", lat=372296000, lon=-804243000,
                    alt=620500, time_usec=1000000, fix_type=3, satellites_visible=12)
    return Message(**{**defaults, **fields})


def filtered(**fields):
    defaults = dict(kind="GLOBAL_POSITION_INT", lat=372296123, lon=-804243456,
                    alt=621250, relative_alt=10000, time_boot_ms=1000)
    return Message(**{**defaults, **fields})


class GPSStateTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.state = gps.GPSState(clock=self.clock)

    def connect(self):
        self.state.update(heartbeat())

    def test_position_requires_autopilot_heartbeat_and_valid_fix(self):
        self.state.update(raw())
        self.assertIsNone(self.state.snapshot()["position"])
        self.connect()
        self.assertEqual(self.state.snapshot()["gps"]["status"], "waiting_for_fix")
        self.state.update(raw(fix_type=2))
        self.assertIsNone(self.state.snapshot()["position"])
        self.state.update(raw())
        position = self.state.snapshot()["position"]
        self.assertEqual(position, {"latitude": 37.2296, "longitude": -80.4243,
                                    "altitude_m": 620.5, "altitude_reference": "AMSL"})

    def test_filtered_position_is_used_only_with_fresh_raw_gps_fix(self):
        self.connect()
        self.state.update(filtered())
        self.assertIsNone(self.state.snapshot()["position"])
        self.state.update(raw())
        self.state.update(Message(kind="EKF_STATUS_REPORT", flags=48))
        sample = self.state.snapshot()
        self.assertEqual(sample["gps"]["source"], "GLOBAL_POSITION_INT")
        self.assertAlmostEqual(sample["position"]["latitude"], 37.2296123)
        self.assertEqual(sample["position"]["altitude_m"], 621.25)
        self.assertNotEqual(sample["position"]["altitude_m"], 10)

    def test_uninitialized_filtered_position_does_not_override_valid_gps(self):
        self.connect()
        self.state.update(raw())
        self.state.update(filtered(lat=0, lon=0, alt=0))
        for flags in (None, 1024, 128, 16):
            if flags is not None:
                self.state.update(Message(kind="EKF_STATUS_REPORT", flags=flags))
            with self.subTest(flags=flags):
                sample = self.state.snapshot()
                self.assertEqual(sample["gps"]["source"], "GPS_RAW_INT")
                self.assertEqual(sample["position"]["latitude"], 37.2296)

    def test_reported_gps_glitch_marks_coordinates_unavailable(self):
        self.connect()
        self.state.update(raw())
        self.state.update(Message(kind="EKF_STATUS_REPORT", flags=48 | 32768))
        self.assertIsNone(self.state.snapshot()["position"])
        self.assertEqual(self.state.snapshot()["gps"]["status"], "gps_glitch")

    def test_no_fix_clears_previous_coordinates_immediately(self):
        self.connect()
        self.state.update(raw())
        self.state.update(filtered())
        self.state.update(raw(fix_type=1))
        sample = self.state.snapshot()
        self.assertIsNone(sample["position"])
        self.assertEqual(sample["gps"]["status"], "no_3d_fix")

    def test_stale_gps_is_unavailable_even_if_filtered_position_keeps_arriving(self):
        self.connect()
        self.state.update(raw())
        self.clock.now += 5.1
        self.state.update(heartbeat())
        self.state.update(filtered())
        self.assertIsNone(self.state.snapshot()["position"])
        self.assertEqual(self.state.snapshot()["gps"]["status"], "stale")

    def test_cached_sensor_timestamp_does_not_keep_coordinates_fresh(self):
        self.connect()
        self.state.update(raw())
        self.clock.now += 5.1
        self.state.update(heartbeat())
        self.state.update(raw())
        self.assertIsNone(self.state.snapshot()["position"])
        self.state.update(raw(time_usec=2000000))
        self.assertIsNotNone(self.state.snapshot()["position"])

    def test_stale_heartbeat_invalidates_fresh_coordinates(self):
        self.connect()
        self.clock.now += 10.1
        self.state.update(raw())
        self.assertIsNone(self.state.snapshot()["position"])
        self.assertEqual(self.state.snapshot()["gps"]["status"], "disconnected")

    def test_other_vehicles_and_components_do_not_supply_gps(self):
        self.connect()
        self.state.update(raw(system=8))
        self.state.update(raw(component=2))
        self.state.update(heartbeat(system=8))
        self.assertIsNone(self.state.snapshot()["position"])
        self.assertEqual(self.state.target, (7, 1))

    def test_ground_station_heartbeat_is_not_an_autopilot(self):
        self.state.update(Message(kind="HEARTBEAT", autopilot=8, system=255))
        self.assertIsNone(self.state.target)
        self.state.update(heartbeat(component=191))
        self.assertIsNone(self.state.target)

    def test_invalid_coordinates_or_fix_type_are_not_forwarded(self):
        for changes in ({"lat": 910000000}, {"lon": -1810000000}, {"alt": float("nan")},
                        {"lat": True}, {"lat": 37.2}, {"fix_type": 255}, {"fix_type": True}):
            self.state.reset()
            self.connect()
            self.state.update(raw(**changes))
            with self.subTest(changes=changes):
                self.assertIsNone(self.state.snapshot()["position"])

    def test_zero_coordinates_with_a_real_fix_are_valid(self):
        self.connect()
        self.state.update(raw(lat=0, lon=0))
        self.assertEqual(self.state.snapshot()["position"]["latitude"], 0)

    def test_unknown_satellite_count_is_not_displayed_as_255_satellites(self):
        self.connect()
        self.state.update(raw(satellites_visible=255))
        self.assertIsNone(self.state.snapshot()["gps"]["satellites_visible"])

    def test_snapshot_is_independent_and_reconnect_clears_old_fix(self):
        self.connect()
        self.state.update(raw())
        self.state.snapshot()["position"]["latitude"] = 999
        self.assertEqual(self.state.snapshot()["position"]["latitude"], 37.2296)
        self.state.reset("connection_error", "USB unplugged")
        self.assertIsNone(self.state.snapshot()["position"])
        self.connect()
        self.assertIsNone(self.state.snapshot()["position"])

    def test_detection_captures_gps_before_waiting_in_radio_queue(self):
        packet = json.loads((BASE_STATION / "sample_packet.json").read_text())
        worker = live.RadioWorker.__new__(live.RadioWorker)
        worker.pending = queue.Queue(maxsize=10)
        worker.gps = SimpleNamespace(snapshot=self.state.snapshot)
        self.connect()
        self.state.update(raw())
        with contextlib.redirect_stdout(io.StringIO()):
            worker.enqueue(packet)
        self.state.update(raw(lat=400000000, time_usec=2000000))
        captured, _ = worker.pending.get_nowait()
        validate_packet(captured)
        self.assertEqual(captured["position"]["latitude"], 37.2296)
        self.assertEqual(captured["gps"]["status"], "fix")
        detection = Detection.from_packet(captured)
        self.assertEqual(detection.formatted_latitude, "37.229600")
        self.assertIn("AMSL", detection.formatted_altitude)


class USBDiscoveryTests(unittest.TestCase):
    def test_auto_discovery_selects_pixhawk_and_ignores_ftdi_radios(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "ttyACM0"
            target.touch()
            (root / "usb-Holybro_Pixhawk6C-id-if00").symlink_to(target)
            (root / "usb-FTDI_FT231X-radio").touch()
            self.assertIn("Pixhawk6C", gps.find_pixhawk(directory=root))

    def test_auto_discovery_does_not_guess_between_two_pixhawks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "usb-Pixhawk-one").touch()
            (root / "usb-Pixhawk-two").touch()
            with self.assertRaisesRegex(OSError, "--gps-device"):
                gps.find_pixhawk(directory=root)

    def test_explicit_missing_device_is_reported(self):
        with self.assertRaises(FileNotFoundError):
            gps.find_pixhawk("/nonexistent/nightowl-pixhawk")


class Connection:
    def __init__(self, messages=()):
        self.messages = queue.Queue()
        for message in messages:
            self.messages.put(message)
        self.mav = Mock()
        self.closed = False

    def recv_match(self, **kwargs):
        try:
            message = self.messages.get(timeout=0.01)
        except queue.Empty:
            return None
        if isinstance(message, Exception):
            raise message
        return message

    def close(self):
        self.closed = True


class GPSReaderTests(unittest.TestCase):
    def test_reader_requests_its_own_telemetry_without_a_ground_station(self):
        connection = Connection([heartbeat(), raw(), filtered(), Message(kind="EKF_STATUS_REPORT", flags=48)])
        requested = threading.Event()
        connection.mav.command_long_send.side_effect = lambda *args: requested.set()
        with contextlib.redirect_stdout(io.StringIO()):
            reader = gps.MavlinkGPS(connection_factory=lambda *args: connection,
                                    device_finder=lambda device: "/dev/test-pixhawk")
            try:
                self.assertTrue(requested.wait(timeout=2))
                deadline = time.monotonic() + 2
                while reader.snapshot()["gps"]["source"] != "GLOBAL_POSITION_INT" and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(reader.snapshot()["gps"]["source"], "GLOBAL_POSITION_INT")
                requests = [call.args for call in connection.mav.command_long_send.call_args_list]
                self.assertEqual([request[4] for request in requests], [24, 33, 193])
                self.assertTrue(all(request[:4] == (7, 1, 511, 0) and request[5] == 500000 for request in requests))
                self.assertEqual({call[0] for call in connection.mav.method_calls}, {"heartbeat_send", "command_long_send"})
            finally:
                reader.close()
        self.assertTrue(connection.closed)
        self.assertFalse(reader.worker.is_alive())

    def test_unsupported_interval_command_requests_legacy_streams(self):
        connection = Connection([heartbeat(), Message(kind="COMMAND_ACK", command=511, result=3)])
        fallback = threading.Event()
        connection.mav.request_data_stream_send.side_effect = lambda *args: fallback.set()
        with contextlib.redirect_stdout(io.StringIO()):
            reader = gps.MavlinkGPS(connection_factory=lambda *args: connection,
                                    device_finder=lambda device: "/dev/test-pixhawk")
            try:
                self.assertTrue(fallback.wait(timeout=2))
                requests = [call.args for call in connection.mav.request_data_stream_send.call_args_list]
                self.assertEqual(requests, [(7, 1, 2, 2, 1), (7, 1, 6, 2, 1)])
            finally:
                reader.close()

    def test_usb_failure_discards_fix_and_closes_connection(self):
        connection = Connection([heartbeat(), raw(), OSError("USB unplugged")])
        with contextlib.redirect_stdout(io.StringIO()):
            reader = gps.MavlinkGPS(connection_factory=lambda *args: connection,
                                    device_finder=lambda device: "/dev/test-pixhawk")
            try:
                deadline = time.monotonic() + 2
                while reader.snapshot()["gps"]["status"] != "connection_error" and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertIsNone(reader.position())
                self.assertIn("USB unplugged", reader.snapshot()["gps"]["error"])
            finally:
                reader.close()
        self.assertTrue(connection.closed)


    def test_usb_reconnect_waits_for_new_heartbeat_and_new_fix(self):
        first = Connection([heartbeat(), raw(), OSError("USB unplugged")])
        second = Connection([heartbeat(system=8), raw(system=8, lat=400000000)])
        factory = Mock(side_effect=[first, second])
        with contextlib.redirect_stdout(io.StringIO()):
            reader = gps.MavlinkGPS(connection_factory=factory,
                                    device_finder=lambda device: "/dev/test-pixhawk")
            try:
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    position = reader.position()
                    if position is not None and position["latitude"] == 40:
                        break
                    time.sleep(0.01)
                self.assertEqual(reader.position()["latitude"], 40)
                self.assertEqual(reader.state.target, (8, 1))
                self.assertTrue(first.closed)
                self.assertEqual(factory.call_count, 2)
            finally:
                reader.close()
        self.assertTrue(second.closed)


class RealMAVLinkTests(unittest.TestCase):
    def setUp(self):
        try:
            from pymavlink.dialects.v20 import ardupilotmega as common
        except ImportError:
            self.skipTest("pymavlink not installed")
        self.mavlink = common

    def test_real_mavlink_packets_decode_to_valid_gui_position(self):
        writer = self.mavlink.MAVLink(None, srcSystem=7, srcComponent=1)
        parser = self.mavlink.MAVLink(None)
        messages = [writer.heartbeat_encode(2, 3, 0, 0, 4),
                    writer.gps_raw_int_encode(1000000, 3, 372296000, -804243000, 620500, 100, 100, 0, 0, 12),
                    writer.global_position_int_encode(1000, 372296123, -804243456, 621250, 10000, 0, 0, 0, 0),
                    writer.ekf_status_report_encode(48, 0, 0, 0, 0, 0)]
        wire = b"".join(message.pack(writer) for message in messages)
        state = gps.GPSState()
        for message in parser.parse_buffer(wire):
            state.update(message)
        sample = state.snapshot()
        packet = json.loads((BASE_STATION / "sample_packet.json").read_text())
        packet["position"] = sample["position"]
        validate_packet(packet)
        self.assertAlmostEqual(sample["position"]["longitude"], -80.4243456)
        self.assertEqual(sample["gps"]["status"], "fix")

    def test_stream_requests_use_actual_mavlink_command_encoding(self):
        output = io.BytesIO()
        writer = self.mavlink.MAVLink(output, srcSystem=245, srcComponent=191)
        gps.request_position_messages(SimpleNamespace(mav=writer), (7, 1))
        decoded = self.mavlink.MAVLink(None).parse_buffer(output.getvalue())
        self.assertEqual([message.get_type() for message in decoded], ["COMMAND_LONG"] * 3)
        self.assertEqual([message.param1 for message in decoded], [24, 33, 193])
        self.assertTrue(all(message.command == 511 and message.param2 == 500000 for message in decoded))
        self.assertTrue(all(message.target_system == 7 and message.target_component == 1 for message in decoded))


if __name__ == "__main__":
    unittest.main()
