"""Small JPEG thumbnails over the dedicated JSON radio, with stop-and-wait ACKs."""

import base64
import binascii
from collections import OrderedDict
from dataclasses import dataclass, field
import hashlib
import io
from pathlib import Path
import re
import tempfile
import time
import uuid
import zlib

CHUNK_BYTES = 192
MIN_CHUNK_BYTES = 48
MAX_CHUNK_BYTES = 384
MAX_IMAGE_BYTES = 32_768
THUMBNAIL_BYTES = 12_000
MAX_IMAGE_EDGE = 640
TRANSFER_TIMEOUT = 120
ACK_TIMEOUT = 10
ATTEMPTS = 3
MAX_PENDING = 4


class RetryableChunkError(ValueError):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def _chunk_size(manifest):
    size = manifest.get("chunk_bytes")
    if type(size) is not int or not MIN_CHUNK_BYTES <= size <= MAX_CHUNK_BYTES:
        raise ValueError("Unsupported image chunk size")
    return size


def _pillow():
    try:
        from PIL import Image
    except ImportError as error:
        raise ValueError("Thumbnail support requires Pillow: python3 -m pip install pillow") from error
    return Image


def make_thumbnail(source, bbox=None, label=""):
    """Keep the full scene, annotate the detection, and bound radio bandwidth."""
    Image = _pillow()
    from PIL import ImageDraw
    with Image.open(source) as original:
        image = original.convert("RGB")
    width, height = image.size
    image.thumbnail((320, 320))
    draw = ImageDraw.Draw(image)
    if bbox:
        x = bbox["x"] * image.width / width
        y = bbox["y"] * image.height / height
        right = (bbox["x"] + bbox["width"]) * image.width / width
        bottom = (bbox["y"] + bbox["height"]) * image.height / height
        x, right = [max(0, min(image.width - 1, value)) for value in (x, right)]
        y, bottom = [max(0, min(image.height - 1, value)) for value in (y, bottom)]
        if right >= x and bottom >= y:
            draw.rectangle((x, y, right, bottom), outline="lime", width=2)
    if label:
        draw.text((4, 4), label[:60], fill="lime", stroke_width=1, stroke_fill="black")
    for edge, quality in ((320, 50), (256, 45), (192, 40), (128, 35)):
        thumbnail = image.copy()
        thumbnail.thumbnail((edge, edge))
        output = io.BytesIO()
        thumbnail.save(output, format="JPEG", quality=quality, optimize=True)
        data = output.getvalue()
        if len(data) <= THUMBNAIL_BYTES:
            return data
    raise ValueError("Could not compress thumbnail within the radio image budget")


def image_manifest(data):
    if not 0 < len(data) <= MAX_IMAGE_BYTES:
        raise ValueError("Invalid thumbnail size")
    return {"transfer_id": uuid.uuid4().hex, "format": "jpeg", "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "chunk_bytes": CHUNK_BYTES}


def _valid_id(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) is not None


def image_ack(message, status="accepted", error=None):
    response = {"kind": "image_ack", "status": status}
    if type(message.get("packet_id")) is int:
        response["packet_id"] = message["packet_id"]
    if _valid_id(message.get("transfer_id")):
        response["transfer_id"] = message["transfer_id"]
    if type(message.get("sequence")) is int:
        response["sequence"] = message["sequence"]
    if error is not None:
        response["error"] = str(error)
        if isinstance(error, RetryableChunkError):
            response["retryable"] = True
            response["code"] = error.code
    return response


def _exchange(stream, message, deadline, stop_event):
    last_error = "No matching acknowledgment"
    rejected = False
    sent = 0
    for attempt in range(ATTEMPTS):
        if stop_event is not None and stop_event.is_set():
            raise InterruptedError("Thumbnail transfer stopped")
        if time.monotonic() >= deadline:
            break
        stream.send(message)
        sent += 1
        ack_deadline = min(deadline, time.monotonic() + ACK_TIMEOUT)
        while time.monotonic() < ack_deadline:
            if stop_event is not None and stop_event.is_set():
                raise InterruptedError("Thumbnail transfer stopped")
            try:
                response = stream.receive(timeout=min(0.5, ack_deadline - time.monotonic()))
            except (TimeoutError, ValueError):
                continue
            if not isinstance(response, dict) or response.get("kind") != "image_ack":
                continue
            if any(type(response.get(key)) is not type(message[key]) or response[key] != message[key]
                   for key in ("packet_id", "transfer_id", "sequence")):
                continue
            if response.get("status") == "accepted":
                return
            if response.get("status") == "rejected":
                last_error = str(response.get("error", "unknown reason"))
                if response.get("retryable") is not True:
                    raise ValueError(f"Thumbnail rejected at chunk {message['sequence']}: {last_error}")
                rejected = True
                if attempt + 1 < ATTEMPTS:
                    print(f"[IMAGE] Retrying chunk {message['sequence']} after rejection: {last_error}", flush=True)
                break
    detail = f"Thumbnail chunk {message['sequence']} failed after {sent} attempts: {last_error}"
    if rejected:
        raise ValueError(detail)
    raise TimeoutError(detail)


def send_image(stream, packet_id, manifest, data, stop_event=None):
    if stop_event is not None and stop_event.is_set():
        raise InterruptedError("Thumbnail transfer stopped")
    try:
        _send_image(stream, packet_id, manifest, data, stop_event)
    except (OSError, ValueError):
        # Release partial state if retries fail. An abort never deletes an
        # already verified image, including when the completion ACK was lost.
        transfer_id = manifest.get("transfer_id")
        if _valid_id(transfer_id):
            try:
                stream.send({"kind": "image_abort", "packet_id": packet_id,
                             "transfer_id": transfer_id, "sequence": 0})
            except (OSError, ValueError):
                pass
        raise


def _send_image(stream, packet_id, manifest, data, stop_event=None):
    chunk_size = _chunk_size(manifest)
    if len(data) != manifest["size"] or hashlib.sha256(data).hexdigest() != manifest["sha256"]:
        raise ValueError("Thumbnail does not match its manifest")
    deadline = time.monotonic() + TRANSFER_TIMEOUT
    common = {"packet_id": packet_id, "transfer_id": manifest["transfer_id"]}
    chunks = (len(data) + chunk_size - 1) // chunk_size
    for sequence in range(chunks):
        chunk = data[sequence * chunk_size:(sequence + 1) * chunk_size]
        message = {**common, "kind": "image_chunk", "sequence": sequence,
                   "data": base64.b64encode(chunk).decode("ascii"),
                   "crc32": f"{zlib.crc32(chunk):08x}"}
        _exchange(stream, message, deadline, stop_event)
    _exchange(stream, {**common, "kind": "image_end", "sequence": chunks}, deadline, stop_event)


@dataclass
class _Transfer:
    packet_id: int
    manifest: dict
    data: bytearray = field(default_factory=bytearray)
    updated: float = field(default_factory=time.monotonic)


class ImageReceiver:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.pending = {}
        self.completed = OrderedDict()

    def begin(self, packet):
        manifest = packet.get("image")
        if manifest is None:
            return
        if not isinstance(manifest, dict) or not _valid_id(manifest.get("transfer_id")):
            raise ValueError("Invalid image transfer ID")
        if manifest.get("format") != "jpeg":
            raise ValueError("Unsupported image format")
        _chunk_size(manifest)
        if type(manifest.get("size")) is not int or not 0 < manifest["size"] <= MAX_IMAGE_BYTES:
            raise ValueError("Invalid image size")
        if not isinstance(manifest.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", manifest["sha256"]):
            raise ValueError("Invalid image checksum")
        _pillow()
        now = time.monotonic()
        self.pending = {key: transfer for key, transfer in self.pending.items()
                        if now - transfer.updated < TRANSFER_TIMEOUT}
        transfer_id = manifest["transfer_id"]
        if transfer_id in self.pending or transfer_id in self.completed:
            raise ValueError("Image transfer ID is already in use")
        if len(self.pending) >= MAX_PENDING:
            raise ValueError("Too many unfinished image transfers")
        self.pending[transfer_id] = _Transfer(packet["packet_id"], dict(manifest))

    def handle(self, message):
        """Return an ACK and, once only on completion, a GUI image event."""
        try:
            return self._handle(message)
        except RetryableChunkError:
            # Preserve already verified bytes so retransmission can recover.
            raise
        except (ValueError, KeyError, TypeError, OSError):
            transfer_id = message.get("transfer_id")
            if _valid_id(transfer_id):
                transfer = self.pending.get(transfer_id)
                if transfer is not None and transfer.packet_id == message.get("packet_id"):
                    del self.pending[transfer_id]
            raise

    def _handle(self, message):
        transfer_id = message.get("transfer_id")
        if not _valid_id(transfer_id) or type(message.get("packet_id")) is not int:
            raise ValueError("Invalid image transfer identity")
        if message.get("kind") == "image_abort":
            transfer = self.pending.get(transfer_id)
            completed = self.completed.get(transfer_id)
            if (transfer is not None and transfer.packet_id != message["packet_id"]) or (
                completed is not None and completed[0] != message["packet_id"]
            ):
                raise ValueError("Image abort has no matching detection")
            self.pending.pop(transfer_id, None)
            return image_ack(message), None
        if type(message.get("sequence")) is not int or message["sequence"] < 0:
            raise RetryableChunkError("Invalid image chunk sequence", "sequence")
        if transfer_id in self.completed:
            packet_id, sequence = self.completed[transfer_id]
            if message.get("kind") == "image_end" and message["packet_id"] == packet_id and message["sequence"] == sequence:
                return image_ack(message), None
            raise ValueError("Image transfer is already complete")
        transfer = self.pending.get(transfer_id)
        if transfer is None or transfer.packet_id != message["packet_id"]:
            raise ValueError("Image has no matching detection")
        if time.monotonic() - transfer.updated >= TRANSFER_TIMEOUT:
            del self.pending[transfer_id]
            raise ValueError("Image transfer expired")
        transfer.updated = time.monotonic()
        size = transfer.manifest["size"]
        chunk_size = transfer.manifest["chunk_bytes"]
        chunks = (size + chunk_size - 1) // chunk_size
        sequence = message["sequence"]
        if message["kind"] == "image_chunk":
            if sequence >= chunks:
                raise RetryableChunkError(f"Image chunk {sequence} is out of range", "sequence")
            encoded = message.get("data")
            if not isinstance(encoded, str) or len(encoded) > 4 * ((chunk_size + 2) // 3):
                raise RetryableChunkError(f"Invalid image chunk size at chunk {sequence}", "size")
            try:
                chunk = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as error:
                raise RetryableChunkError(f"Invalid image chunk encoding at chunk {sequence}", "encoding") from error
            expected_size = min(chunk_size, size - sequence * chunk_size)
            if len(chunk) != expected_size:
                raise RetryableChunkError(
                    f"Chunk {sequence} size mismatch: expected {expected_size}, got {len(chunk)}", "size")
            expected_crc = f"{zlib.crc32(chunk):08x}"
            if message.get("crc32") != expected_crc:
                actual_crc = str(message.get("crc32"))[:16]
                raise RetryableChunkError(
                    f"Chunk {sequence} checksum mismatch: computed {expected_crc}, sender reported {actual_crc}", "checksum")
            offset = sequence * chunk_size
            if offset < len(transfer.data):
                if transfer.data[offset:offset + len(chunk)] != chunk:
                    raise RetryableChunkError(f"Conflicting duplicate image chunk {sequence}", "duplicate")
            elif offset == len(transfer.data):
                transfer.data.extend(chunk)
            else:
                raise RetryableChunkError(f"Image chunk {sequence} arrived out of order", "sequence")
            return image_ack(message), None
        if message["kind"] != "image_end":
            raise ValueError("Invalid image completion message")
        if sequence != chunks:
            raise RetryableChunkError(f"Invalid image completion sequence: expected {chunks}, got {sequence}", "sequence")
        if len(transfer.data) != size or hashlib.sha256(transfer.data).hexdigest() != transfer.manifest["sha256"]:
            raise ValueError("Incomplete image or image checksum mismatch")
        data = bytes(transfer.data)
        Image = _pillow()
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.format != "JPEG" or max(image.size) > MAX_IMAGE_EDGE:
                    raise ValueError("Thumbnail must be a JPEG no larger than 640 pixels per side")
                image.load()
        except (OSError, Image.DecompressionBombError) as error:
            raise ValueError("Invalid thumbnail JPEG") from error
        self.directory.mkdir(parents=True, exist_ok=True)
        destination = self.directory / f"image_{transfer_id}.jpg"
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory, suffix=".part", delete=False) as output:
                temporary = Path(output.name)
                output.write(data)
            temporary.replace(destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        del self.pending[transfer_id]
        self.completed[transfer_id] = (transfer.packet_id, chunks)
        if len(self.completed) > 64:
            self.completed.popitem(last=False)
        return image_ack(message), {"packet_id": transfer.packet_id, "image_path": str(destination.resolve())}
