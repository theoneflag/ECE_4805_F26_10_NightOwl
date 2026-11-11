"""Bounded, independently verifiable JPEG previews for serial detection packets."""
import base64
import binascii
import hashlib
import io
import os
from pathlib import Path
import tempfile

MAX_JPEG_BYTES = 4096
MAX_WIDTH = 160
MAX_HEIGHT = 120


def attach_preview(packet, snapshots):
    """Read a local snapshot on Pi and add a compact JPEG. Never sends a path."""
    import cv2
    outgoing = dict(packet)
    outgoing['thumbnail'] = None
    reference = packet.get('thumbnail')
    if not reference:
        return outgoing
    # Original callback creates basename-only filenames in this session folder.
    if not isinstance(reference, str) or Path(reference).name != reference:
        raise ValueError('Snapshot reference must be a local basename')
    frame = cv2.imread(str(Path(snapshots) / reference))
    if frame is None:
        raise ValueError('Snapshot could not be read')
    for max_w, max_h in ((160, 120), (128, 96), (96, 72), (64, 48)):
        scale = min(max_w / frame.shape[1], max_h / frame.shape[0], 1.0)
        small = cv2.resize(frame, (max(1, int(frame.shape[1]*scale)),
                                   max(1, int(frame.shape[0]*scale))), interpolation=cv2.INTER_AREA)
        ok, encoded = cv2.imencode('.jpg', small, [cv2.IMWRITE_JPEG_QUALITY, 35])
        if ok and len(encoded) <= MAX_JPEG_BYTES:
            outgoing['thumbnail_jpeg_b64'] = base64.b64encode(encoded.tobytes()).decode('ascii')
            return outgoing
    raise ValueError('Preview cannot fit 4096-byte limit')


def save_preview(packet, application_dir):
    """Receiver: validate image, store atomically, return metadata with local path."""
    from PIL import Image, UnidentifiedImageError
    outgoing = dict(packet)
    encoded = outgoing.pop('thumbnail_jpeg_b64', None)
    if encoded is None:
        return outgoing
    if not isinstance(encoded, str) or len(encoded) > 4*((MAX_JPEG_BYTES+2)//3):
        raise ValueError('Preview exceeds size limit or is not base64 text')
    try:
        data = base64.b64decode(encoded, validate=True)
        if not 0 < len(data) <= MAX_JPEG_BYTES:
            raise ValueError('Preview exceeds decoded size limit')
        with Image.open(io.BytesIO(data)) as preview:
            if preview.format != 'JPEG' or preview.width > MAX_WIDTH or preview.height > MAX_HEIGHT:
                raise ValueError('Preview must be a JPEG no larger than 160 by 120')
            preview.verify()
        # Decode as well: verify alone does not validate the entire JPEG pixel stream.
        with Image.open(io.BytesIO(data)) as preview:
            preview.load()
    except (binascii.Error, UnicodeError, OSError, UnidentifiedImageError) as error:
        raise ValueError(f'Invalid JPEG preview: {error}') from None
    directory = Path(application_dir) / 'radio_thumbnails'
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"det_{packet['packet_id']}_{hashlib.sha256(data).hexdigest()[:16]}.jpg"
    destination = directory / filename
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, suffix='.tmp', delete=False) as f:
            temporary = Path(f.name)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    outgoing['thumbnail'] = 'radio_thumbnails/' + filename
    return outgoing
