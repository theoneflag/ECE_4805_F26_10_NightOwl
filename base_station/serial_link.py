"""Newline-delimited UTF-8 JSON over a dedicated serial telemetry pair."""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from detection import Detection
from receiver import validate_packet

MAX_FRAME = 65536


class LineReader:
    """Keep partial frames across timeouts; discard overlong frames to newline."""
    def __init__(self):
        self.buffer = bytearray()
        self.discarding = False

    def read(self, port):
        while True:
            byte = port.read(1)
            if not byte:
                return None
            if byte == b'\n':
                if self.discarding:
                    self.discarding = False
                    self.buffer.clear()
                    raise ValueError('Serial frame exceeds 65536 bytes')
                line = bytes(self.buffer)
                self.buffer.clear()
                if line.strip():
                    return line
            elif not self.discarding:
                self.buffer.extend(byte)
                if len(self.buffer) > MAX_FRAME:
                    self.buffer.clear()
                    self.discarding = True


def write_json(port, value):
    payload = json.dumps(value, allow_nan=False, separators=(',', ':')).encode('utf-8')
    if len(payload) > MAX_FRAME:
        raise ValueError('Serial frame exceeds 65536 bytes')
    wire = payload + b'\n'
    # Conservative application pacing: UART speed can exceed SiK air throughput.
    # Only the enlarged image packet needs pacing; legacy metadata is unchanged.
    if 'thumbnail_jpeg_b64' in value:
        for offset in range(0, len(wire), 128):
            chunk = wire[offset:offset+128]
            if port.write(chunk) != len(chunk):
                raise OSError('Incomplete serial write')
            port.flush()
            if offset + len(chunk) < len(wire):
                time.sleep(len(chunk) / 1000.0)
    else:
        if port.write(wire) != len(wire):
            raise OSError('Incomplete serial write')
        port.flush()


def send_serial_packet(port, packet, reader=None, timeout=10):
    """Send once and await the matching acknowledgment; no automatic retry."""
    reader = reader or LineReader()
    write_json(port, packet)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = reader.read(port)
        if line is None:
            continue
        try:
            response = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(response, dict):
            continue
        if response.get('packet_id') != packet['packet_id']:
            continue
        if response.get('status') in ('accepted', 'rejected'):
            return response
    raise TimeoutError('No matching radio acknowledgment; check baud rate and peer receiver')


def receive_serial_packets(incoming, serial_port, baud=57600, log_directory=None,
                           stop_event=None, port_factory=None, image_directory=None):
    """Same queue messages as the TCP GUI receiver; accepted means saved and queued."""
    try:
        if port_factory is None:
            import serial
            port_factory = serial.Serial
        directory = Path(log_directory) if log_directory else Path(__file__).resolve().parent / 'logs'
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
        log_path = directory / f'detections_serial_{stamp}.jsonl'
        log_path.touch(exist_ok=False)
        with port_factory(serial_port, baudrate=baud, timeout=.2, write_timeout=5) as port:
            incoming.put(('status', f'Serial listening on {serial_port} at {baud}; log: {log_path}'))
            reader = LineReader()
            while stop_event is None or not stop_event.is_set():
                packet = None
                try:
                    line = reader.read(port)
                    if line is None:
                        continue
                    packet = json.loads(line)
                    validate_packet(packet)
                    if 'thumbnail_jpeg_b64' in packet:
                        from radio_images import save_preview
                        packet = save_preview(packet, image_directory or Path(__file__).resolve().parent)
                    detection = Detection.from_packet(packet)
                    record = {'received_at': datetime.now(timezone.utc).isoformat(), 'packet': packet}
                    with log_path.open('a', encoding='utf-8') as log:
                        log.write(json.dumps(record, allow_nan=False) + '\n')
                        log.flush()
                        os.fsync(log.fileno())
                    incoming.put(('detection', detection))
                    response = {'packet_id': packet['packet_id'], 'status': 'accepted'}
                except (ValueError, KeyError, TypeError, OSError) as error:
                    incoming.put(('status', f'Packet rejected: {error}'))
                    response = {'status': 'rejected', 'error': str(error)}
                    if isinstance(packet, dict):
                        response['packet_id'] = packet.get('packet_id')
                write_json(port, response)
    except ImportError:
        incoming.put(('status', 'Serial support requires: py -m pip install pyserial'))
    except (OSError, ValueError) as error:
        incoming.put(('status', f'Serial receiver stopped: {error}. Close GUI and restart after fixing connection.'))
