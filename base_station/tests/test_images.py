import ast
import base64
import copy
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
from unittest.mock import Mock, patch
import zlib

BASE_STATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_STATION))
sys.path.insert(0, str(BASE_STATION.parent))

from PIL import Image
import image_transfer as images
import Night_Owl_Live_Radio as live
import packet_receiver
from protocol import encode_json
from radio import RadioStream
from sender import send_radio_packet

SAMPLE = json.loads((BASE_STATION / "sample_packet.json").read_text())


def jpeg(size=(320, 240)):
    image = Image.effect_noise(size, 50).convert("RGB")
    output = io.BytesIO()
    image.save(output, "JPEG", quality=40)
    return output.getvalue()


def chunk_message(packet_id, manifest, data, sequence=0):
    chunk_size = manifest["chunk_bytes"]
    chunk = data[sequence * chunk_size:(sequence + 1) * chunk_size]
    return {"kind": "image_chunk", "packet_id": packet_id, "transfer_id": manifest["transfer_id"],
            "sequence": sequence, "data": base64.b64encode(chunk).decode(),
            "crc32": f"{zlib.crc32(chunk):08x}"}


class DuplexSerial:
    """Two queued serial endpoints exercise actual framing and receiver loops."""
    def __init__(self):
        self.incoming = queue.Queue()
        self.buffer = bytearray()
        self.peer = None
        self.drop = None
        self.mutate = None
        self.messages = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def write(self, data):
        written = len(data)
        message = json.loads(data)
        self.messages.append(message)
        if self.mutate is not None:
            message = self.mutate(copy.deepcopy(message))
            data = encode_json(message)
        if self.drop is None or not self.drop(message):
            # Fragment every response and data packet, including its newline.
            self.peer.incoming.put(data[:7])
            self.peer.incoming.put(data[7:-1])
            self.peer.incoming.put(data[-1:])
        return written

    def flush(self):
        pass

    def close(self):
        pass

    def read_until(self, delimiter, size):
        deadline = time.monotonic() + 0.01
        while True:
            end = self.buffer.find(delimiter)
            if end >= 0 or len(self.buffer) >= size:
                length = min(size, end + 1 if end >= 0 else len(self.buffer))
                result = bytes(self.buffer[:length])
                del self.buffer[:length]
                return result
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                result = bytes(self.buffer)
                self.buffer.clear()
                return result
            try:
                part = self.incoming.get(timeout=remaining)
            except queue.Empty:
                continue
            if isinstance(part, Exception):
                raise part
            self.buffer.extend(part)


class ImageTransferTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def receiver(self, data):
        manifest = images.image_manifest(data)
        receiver = images.ImageReceiver(self.root)
        receiver.begin({**SAMPLE, "image": manifest})
        return receiver, manifest

    def test_compressed_thumbnail_has_bounded_dimensions_and_bytes(self):
        source = self.root / "source.jpg"
        Image.effect_noise((1456, 1088), 100).convert("RGB").save(source, "JPEG", quality=95)
        result = images.make_thumbnail(source, {"x": 120, "y": 100, "width": 300, "height": 600}, "person 91%")
        self.assertLessEqual(len(result), images.THUMBNAIL_BYTES)
        with Image.open(io.BytesIO(result)) as thumbnail:
            self.assertEqual(thumbnail.format, "JPEG")
            self.assertLessEqual(max(thumbnail.size), 320)
            thumbnail.load()

    def test_corrupt_chunk_preserves_verified_bytes_for_retransmission(self):
        data = jpeg()
        receiver, manifest = self.receiver(data)
        receiver.handle(chunk_message(27, manifest, data, 0))
        message = chunk_message(27, manifest, data, 1)
        message["crc32"] = "00000000"
        with self.assertRaisesRegex(images.RetryableChunkError, "checksum") as failure:
            receiver.handle(message)
        response = images.image_ack(message, "rejected", failure.exception)
        self.assertTrue(response["retryable"])
        self.assertEqual(response["code"], "checksum")
        self.assertFalse(list(self.root.iterdir()))
        self.assertEqual(receiver.pending[manifest["transfer_id"]].data, data[:images.CHUNK_BYTES])
        response, completed = receiver.handle(chunk_message(27, manifest, data, 1))
        self.assertEqual(response["status"], "accepted")
        self.assertIsNone(completed)

    def test_duplicate_chunk_is_idempotent(self):
        data = jpeg()
        receiver, manifest = self.receiver(data)
        message = chunk_message(27, manifest, data)
        first, _ = receiver.handle(message)
        second, _ = receiver.handle(message)
        self.assertEqual(first, second)
        self.assertEqual(len(receiver.pending[manifest["transfer_id"]].data), images.CHUNK_BYTES)

    def test_out_of_order_and_unmatched_chunks_are_rejected(self):
        data = jpeg()
        for change in ({"sequence": 1}, {"packet_id": 99}, {"transfer_id": "0" * 32}):
            receiver, manifest = self.receiver(data)
            message = chunk_message(27, manifest, data, sequence=change.get("sequence", 0))
            message.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                receiver.handle(message)
        self.assertFalse(list(self.root.iterdir()))

    def test_incomplete_image_cannot_be_committed(self):
        data = jpeg()
        receiver, manifest = self.receiver(data)
        receiver.handle(chunk_message(27, manifest, data))
        end = {"kind": "image_end", "packet_id": 27, "transfer_id": manifest["transfer_id"],
               "sequence": (len(data) + images.CHUNK_BYTES - 1) // images.CHUNK_BYTES}
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            receiver.handle(end)
        self.assertFalse(list(self.root.iterdir()))

    def test_manifest_rejects_unbounded_sizes_and_path_names(self):
        good = images.image_manifest(jpeg())
        for change in ({"transfer_id": "../../escape"}, {"size": images.MAX_IMAGE_BYTES + 1},
                       {"size": True}, {"size": 0}, {"format": "png"}, {"chunk_bytes": 9999},
                       {"sha256": "invalid"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                images.ImageReceiver(self.root).begin({**SAMPLE, "image": {**good, **change}})

    def test_hash_matching_data_must_also_be_a_valid_small_jpeg(self):
        for data in (b"not an image", jpeg((641, 1))):
            receiver, manifest = self.receiver(data)
            chunks = (len(data) + images.CHUNK_BYTES - 1) // images.CHUNK_BYTES
            for sequence in range(chunks):
                receiver.handle(chunk_message(27, manifest, data, sequence))
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                receiver.handle({"kind": "image_end", "packet_id": 27,
                                 "transfer_id": manifest["transfer_id"], "sequence": chunks})
        self.assertFalse(list(self.root.iterdir()))

    def test_total_image_checksum_is_verified(self):
        data = jpeg()
        manifest = images.image_manifest(data)
        manifest["sha256"] = "0" * 64
        receiver = images.ImageReceiver(self.root)
        receiver.begin({**SAMPLE, "image": manifest})
        chunks = (len(data) + images.CHUNK_BYTES - 1) // images.CHUNK_BYTES
        for sequence in range(chunks):
            receiver.handle(chunk_message(27, manifest, data, sequence))
        with self.assertRaisesRegex(ValueError, "checksum"):
            receiver.handle({"kind": "image_end", "packet_id": 27,
                             "transfer_id": manifest["transfer_id"], "sequence": chunks})
        self.assertFalse(list(self.root.iterdir()))

    def transfer(self, dropped_sequences=(), corruption=None, chunk_size=None):
        data = jpeg((160, 120))
        manifest = images.image_manifest(data)
        if chunk_size is not None:
            manifest["chunk_bytes"] = chunk_size
        chunks = (len(data) + manifest["chunk_bytes"] - 1) // manifest["chunk_bytes"]
        tx, rx = DuplexSerial(), DuplexSerial()
        tx.peer, rx.peer = rx, tx
        corrupted = False
        def mutate(message):
            nonlocal corrupted
            if corruption is not None and message.get("kind") == "image_chunk" and message["sequence"] == 1 and not corrupted:
                corrupted = True
                corruption(message)
            return message
        tx.mutate = mutate
        dropped = set()
        def drop(message):
            sequence = message.get("sequence")
            selected = sequence in dropped_sequences or ("end" in dropped_sequences and sequence == chunks)
            if message.get("kind") == "image_ack" and selected and sequence not in dropped:
                dropped.add(sequence)
                return True
            return False
        rx.drop = drop
        incoming = queue.Queue()
        with patch.object(packet_receiver, "open_radio", return_value=rx), \
                patch.object(images, "ACK_TIMEOUT", 0.04):
            worker = threading.Thread(target=packet_receiver.receive_radio_packets,
                                      args=(incoming, "TEST", 115200, self.root), daemon=True)
            worker.start()
            try:
                stream = RadioStream(tx)
                packet = {**SAMPLE, "thumbnail": None, "image": manifest}
                self.assertEqual(send_radio_packet(stream, packet)["status"], "accepted")
                images.send_image(stream, 27, manifest, data)
            finally:
                rx.incoming.put(OSError("test finished"))
                worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        messages = list(incoming.queue)
        detections = [value for kind, value in messages if kind == "detection"]
        completed = [value for kind, value in messages if kind == "image"]
        self.assertEqual(len(detections), 1)
        self.assertEqual(len(completed), 1)
        saved = Path(completed[0]["image_path"])
        self.assertEqual(saved.read_bytes(), data)
        self.assertEqual(completed[0]["packet_id"], detections[0].packet_id)
        self.assertEqual(len(list(self.root.glob("*.jpg"))), 1)
        self.assertFalse(list(self.root.glob("*.part")))
        self.assertTrue(all(len(encode_json(message)) < 1024 for message in tx.messages))
        if corruption is not None:
            self.assertTrue(corrupted)
            self.assertGreaterEqual(sum(message.get("kind") == "image_chunk" and message.get("sequence") == 1
                                        for message in tx.messages), 2)
            self.assertEqual(sum(message.get("kind") == "image_chunk" and message.get("sequence") == 0
                                 for message in tx.messages), 1)
        return tx.messages, chunks

    def test_complete_image_transfers_over_fragmented_serial_stream(self):
        self.transfer()

    def test_crc_corruption_recovers_without_restarting_image(self):
        def corrupt(message):
            chunk = bytearray(base64.b64decode(message["data"]))
            chunk[0] ^= 1
            message["data"] = base64.b64encode(chunk).decode()
        self.transfer(corruption=corrupt)

    def test_truncated_base64_chunk_recovers_without_restarting_image(self):
        def corrupt(message):
            message["data"] = message["data"][4:]
        self.transfer(corruption=corrupt)

    def test_invalid_base64_chunk_recovers_without_restarting_image(self):
        def corrupt(message):
            message["data"] = "!" + message["data"][1:]
        self.transfer(corruption=corrupt)

    def test_corrupt_sequence_header_does_not_delete_partial_image(self):
        def corrupt(message):
            message["sequence"] = 2
        self.transfer(corruption=corrupt)

    def test_corrupt_duplicate_header_does_not_delete_partial_image(self):
        def corrupt(message):
            message["sequence"] = 0
        self.transfer(corruption=corrupt)

    def test_receiver_uses_manifest_chunk_size_instead_of_local_default(self):
        self.transfer(chunk_size=384)

    def test_retryable_negative_acknowledgments_have_a_bounded_retry_count(self):
        stream = Mock()
        def reject(**kwargs):
            return images.image_ack(stream.send.call_args.args[0], "rejected",
                                    images.RetryableChunkError("Bad CRC", "checksum"))
        stream.receive.side_effect = reject
        data = jpeg((20, 20))
        with self.assertRaisesRegex(ValueError, "failed after 3 attempts"):
            images.send_image(stream, 27, images.image_manifest(data), data)
        sent = [call.args[0] for call in stream.send.call_args_list]
        self.assertEqual(sum(message["kind"] == "image_chunk" for message in sent), images.ATTEMPTS)
        self.assertTrue(all(message["sequence"] == 0 for message in sent))
        self.assertEqual(sent[-1]["kind"], "image_abort")

    def test_fatal_negative_acknowledgment_is_not_retried(self):
        stream = Mock()
        stream.receive.side_effect = lambda **kwargs: images.image_ack(
            stream.send.call_args.args[0], "rejected", "Image has no matching detection")
        data = jpeg((20, 20))
        with self.assertRaisesRegex(ValueError, "no matching detection"):
            images.send_image(stream, 27, images.image_manifest(data), data)
        self.assertEqual(sum(call.args[0]["kind"] == "image_chunk" for call in stream.send.call_args_list), 1)
        self.assertEqual(stream.send.call_args.args[0]["kind"], "image_abort")

    def test_live_camera_worker_sends_snapshot_and_metadata_together(self):
        source = self.root / SAMPLE["thumbnail"]
        source.write_bytes(jpeg((320, 240)))
        tx, rx = DuplexSerial(), DuplexSerial()
        tx.peer, rx.peer = rx, tx
        incoming = queue.Queue()
        completed = threading.Event()
        original_send = images.send_image
        def send_and_notify(*args, **kwargs):
            original_send(*args, **kwargs)
            completed.set()
        with patch.object(packet_receiver, "open_radio", return_value=rx), \
                patch.object(images, "send_image", side_effect=send_and_notify):
            receiver = threading.Thread(target=packet_receiver.receive_radio_packets,
                                        args=(incoming, "TEST", 115200, self.root / "received"), daemon=True)
            receiver.start()
            worker = live.RadioWorker(tx, send_radio_packet, RadioStream(tx), self.root)
            try:
                worker.enqueue(SAMPLE)
                self.assertTrue(completed.wait(timeout=3), "Live camera thumbnail did not finish")
            finally:
                worker.close()
                rx.incoming.put(OSError("test finished"))
                receiver.join(timeout=2)
        messages = list(incoming.queue)
        detections = [value for kind, value in messages if kind == "detection"]
        attached = [value for kind, value in messages if kind == "image"]
        self.assertEqual(len(detections), 1)
        self.assertTrue(detections[0].expects_image)
        self.assertEqual(len(attached), 1)
        with Image.open(attached[0]["image_path"]) as saved:
            self.assertLessEqual(max(saved.size), 320)
        self.assertTrue(source.exists(), "The full-resolution Pi snapshot must remain available")

    def test_lost_chunk_and_completion_acknowledgments_are_retried_safely(self):
        messages, _ = self.transfer({0, "end"})
        self.assertGreaterEqual(sum(message.get("kind") == "image_chunk" and message.get("sequence") == 0
                                    for message in messages), 2)
        self.assertEqual(sum(message.get("kind") == "image_end" for message in messages), 2)

    def test_unanswered_image_chunks_have_a_bounded_retry_count(self):
        stream = Mock()
        stream.receive.side_effect = TimeoutError("lost acknowledgment")
        data = jpeg((20, 20))
        with patch.object(images, "ACK_TIMEOUT", 0.001):
            with self.assertRaisesRegex(TimeoutError, "acknowledgment"):
                images.send_image(stream, 27, images.image_manifest(data), data)
        self.assertEqual(sum(call.args[0]["kind"] == "image_chunk" for call in stream.send.call_args_list), images.ATTEMPTS)
        self.assertEqual(stream.send.call_args.args[0]["kind"], "image_abort")

    def test_exhausted_retries_release_partial_receiver_state(self):
        data = jpeg((80, 60))
        receiver, manifest = self.receiver(data)
        stream = Mock()
        responses = queue.Queue()
        def send(message):
            corrupted = dict(message)
            if message["kind"] == "image_chunk":
                corrupted["crc32"] = "invalid"
            try:
                response, _ = receiver.handle(corrupted)
            except images.RetryableChunkError as error:
                response = images.image_ack(corrupted, "rejected", error)
            responses.put(response)
        stream.send.side_effect = send
        stream.receive.side_effect = lambda **kwargs: responses.get_nowait()
        with self.assertRaisesRegex(ValueError, "failed after 3 attempts"):
            images.send_image(stream, 27, manifest, data)
        self.assertFalse(receiver.pending)
        self.assertFalse(list(self.root.iterdir()))

    def test_unknown_and_oversized_chunks_cannot_allocate_image_buffers(self):
        data = jpeg()
        receiver, manifest = self.receiver(data)
        message = chunk_message(27, manifest, data)
        message["data"] = "A" * 10000
        with self.assertRaisesRegex(ValueError, "chunk size"):
            receiver.handle(message)
        self.assertFalse(receiver.pending[manifest["transfer_id"]].data)
        self.assertFalse(list(self.root.iterdir()))

    def test_completion_retry_does_not_emit_another_image_event(self):
        data = jpeg((80, 60))
        receiver, manifest = self.receiver(data)
        chunks = (len(data) + images.CHUNK_BYTES - 1) // images.CHUNK_BYTES
        for sequence in range(chunks):
            receiver.handle(chunk_message(27, manifest, data, sequence))
        end = {"kind": "image_end", "packet_id": 27, "transfer_id": manifest["transfer_id"], "sequence": chunks}
        first, event = receiver.handle(end)
        second, repeated = receiver.handle(end)
        self.assertEqual(first, second)
        self.assertIsNotNone(event)
        self.assertIsNone(repeated)
        abort = {**end, "kind": "image_abort", "sequence": 0}
        receiver.handle(abort)
        self.assertTrue(Path(event["image_path"]).exists())

    def test_stop_request_cancels_image_without_transmitting(self):
        stop = threading.Event()
        stop.set()
        stream = Mock()
        data = jpeg()
        with self.assertRaises(InterruptedError):
            images.send_image(stream, 27, images.image_manifest(data), data, stop)
        stream.send.assert_not_called()

    def test_gui_refreshes_selected_detection_when_thumbnail_arrives(self):
        from detection import Detection
        detection = Detection.from_packet(SAMPLE)
        detection.image_path = None
        incoming = queue.Queue()
        incoming.put(("image", {"packet_id": 27, "image_path": "/tmp/received.jpg"}))
        details = SimpleNamespace(current_detection=detection, show_detection=Mock())
        context = {"incoming": incoming, "queue": queue, "detections": [detection],
                   "details": details, "status_text": Mock(), "root": SimpleNamespace(after=Mock())}
        tree = ast.parse((BASE_STATION / "gui.py").read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "check_incoming")
        exec(compile(ast.Module(body=[function], type_ignores=[]), "gui.py", "exec"), context)
        context["check_incoming"]()
        self.assertEqual(detection.image_path, "/tmp/received.jpg")
        details.show_detection.assert_called_once_with(detection)

    def test_camera_queue_retains_snapshot_path_without_sending_pi_filename(self):
        worker = live.RadioWorker.__new__(live.RadioWorker)
        worker.pending = queue.Queue(maxsize=10)
        worker.images = True
        worker.snapshot_dir = self.root
        packet = copy.deepcopy(SAMPLE)
        worker.enqueue(packet)
        metadata, source = worker.pending.get_nowait()
        self.assertEqual(source, self.root / "det_27.jpg")
        self.assertIsNone(metadata["thumbnail"])
        self.assertEqual(packet["thumbnail"], "det_27.jpg")


if __name__ == "__main__":
    unittest.main()
