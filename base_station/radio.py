"""Newline-delimited JSON over a dedicated serial radio (for example SiK)."""

import time

from protocol import MAX_PACKET_BYTES, decode_json, encode_json

DEFAULT_BAUD = 57600
REPLY_TIMEOUT = 10


def add_radio_arguments(parser):
    parser.add_argument("--serial-port", help="JSON radio device, preferably /dev/serial/by-id/...")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD,
                        help="local radio serial baud rate (default: 57600)")
    parser.add_argument("--list-ports", action="store_true", help="list serial devices and exit")


def list_ports():
    try:
        from serial.tools import list_ports as ports
    except ImportError as error:
        raise OSError("Install pyserial first: python3 -m pip install pyserial") from error
    for port in ports.comports():
        if port.vid is not None:
            print(f"{port.device}: {port.description} (serial: {port.serial_number})")


def open_radio(port, baud=DEFAULT_BAUD):
    if baud <= 0:
        raise ValueError("Baud rate must be positive")
    try:
        import serial
    except ImportError as error:
        raise OSError("Install pyserial first: python3 -m pip install pyserial") from error
    return serial.Serial(port, baudrate=baud, timeout=0.2, write_timeout=5, exclusive=True)


class RadioStream:
    def __init__(self, device):
        self.device = device
        self.buffer = bytearray()
        self.discarding = False

    def receive(self, timeout=None):
        """Preserve partial lines across idle serial reads; recover at newlines.

        Serial read timeouts mean no data yet, rather than TCP-style EOF.
        With no overall timeout, a receiver can wait for detections indefinitely.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("No complete JSON reply from radio before timeout")
            limit = 4096 if self.discarding else MAX_PACKET_BYTES + 1 - len(self.buffer)
            chunk = self.device.read_until(b"\n", size=limit)
            if not chunk:
                continue
            if self.discarding:
                if chunk.endswith(b"\n"):
                    self.discarding = False
                continue
            self.buffer.extend(chunk)
            if self.buffer.endswith(b"\n"):
                frame = bytes(self.buffer[:-1])
                self.buffer.clear()
                return decode_json(frame)
            if len(self.buffer) > MAX_PACKET_BYTES:
                self.buffer.clear()
                self.discarding = True
                raise ValueError("Packet is too large")

    def send(self, packet):
        payload = encode_json(packet)
        offset = 0
        while offset < len(payload):
            written = self.device.write(payload[offset:])
            if not written:
                raise OSError("Radio stopped accepting data")
            offset += written
        self.device.flush()
