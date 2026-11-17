import ast
import contextlib
import io
import json
import os
import queue
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "base_station"))

import Night_Owl_Live_Radio as live
import packet_receiver
from detection import Detection
from protocol import encode_json
from receiver import validate_packet


class CameraIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.source_path = ROOT / "night_owl_final.py"
        self.source = self.source_path.read_text()

    def test_real_source_adapts_without_running_camera_or_changing_file(self):
        before = self.source_path.read_bytes()
        live.adapt(self.source, "/tmp/test-snapshots", str(self.source_path))
        self.assertEqual(self.source_path.read_bytes(), before)

    def test_changed_source_fails_before_camera_start(self):
        changed = self.source.replace("def get_gps():", "def different_gps():")
        with self.assertRaisesRegex(ValueError, "Camera source differs"):
            live.adapt(changed, "/tmp/test-snapshots", "changed.py")

    def camera_packet(self):
        # Run only the actual detector's packet-building functions. Camera,
        # accelerator and HTTP startup code are deliberately not executed.
        transformer = live.AdaptCamera("/tmp/test-snapshots")
        tree = transformer.visit(ast.parse(self.source))
        nodes = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in {"get_gps", "send_packet", "trigger_packet"}:
                nodes.append(node)
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id in {"packet_id", "SNAPSHOT_DIR"}
                for target in node.targets
            ):
                nodes.append(node)
        worker = live.RadioWorker.__new__(live.RadioWorker)
        worker.pending = queue.Queue(maxsize=10)
        context = {
            "time": SimpleNamespace(time=lambda: 1791918000),
            "os": os,
            "cv2": SimpleNamespace(imwrite=lambda *args: True),
            "COOLDOWN_SECONDS": 10,
            "last_sent": {},
            "last_sent_lock": threading.Lock(),
            "_radio": worker,
            "_gps": None,
        }
        module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
        exec(compile(module, str(self.source_path), "exec"), context)
        with contextlib.redirect_stdout(io.StringIO()):
            context["trigger_packet"]("person", 0.91, (10, 20, 30, 40), object())
            context["trigger_packet"]("person", 0.91, (10, 20, 30, 40), object())
        self.assertEqual(worker.pending.qsize(), 1, "The per-label cooldown should remain active")
        return worker.pending.get_nowait()[0]

    def test_camera_packet_has_real_detection_and_explicitly_missing_gps(self):
        packet = self.camera_packet()
        validate_packet(packet)
        self.assertEqual(packet["object"], {"type": "person", "confidence": 0.91})
        self.assertEqual(packet["bbox"], {"x": 10, "y": 20, "width": 30, "height": 40})
        self.assertIsNone(packet["position"])
        self.assertIsNone(packet["thumbnail"])
        detection = Detection.from_packet(packet)
        self.assertEqual(detection.formatted_latitude, "Unavailable")
        self.assertEqual(detection.formatted_longitude, "Unavailable")
        self.assertTrue(detection.formatted_time)

    def test_real_camera_packet_reaches_gui_queue_and_gets_acknowledged(self):
        packet = self.camera_packet()
        device = MagicMock()
        device.__enter__.return_value = device
        device.read_until.side_effect = [encode_json(packet), OSError("test finished")]
        device.write.side_effect = len
        incoming = queue.Queue()
        with patch.object(packet_receiver, "open_radio", return_value=device):
            packet_receiver.receive_packets(incoming, serial_port="JSON-RADIO", baud=115200)
        detections = [value for kind, value in list(incoming.queue) if kind == "detection"]
        self.assertEqual(len(detections), 1)
        self.assertEqual(detections[0].object_type, "person")
        reply = json.loads(device.write.call_args.args[0])
        self.assertEqual(reply, {"packet_id": packet["packet_id"], "status": "accepted"})


if __name__ == "__main__":
    unittest.main()
