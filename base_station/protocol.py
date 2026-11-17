"""Bounded, UTF-8, newline-delimited JSON messages over TCP."""

import json
import math
import socket

MAX_PACKET_BYTES = 1_000_000
SOCKET_TIMEOUT = 5


def _reject_constant(value):
    raise ValueError(f"Invalid JSON number: {value}")


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"JSON number is out of range: {value}")
    return number


def decode_json(data):
    try:
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        return json.loads(data, parse_constant=_reject_constant, parse_float=_finite_float)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError(f"Invalid JSON: {error}") from error


def encode_json(packet):
    try:
        payload = json.dumps(packet, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, RecursionError, UnicodeError) as error:
        raise ValueError(f"Cannot encode JSON packet: {error}") from error
    if len(payload) > MAX_PACKET_BYTES:
        raise ValueError("Packet is too large")
    return payload + b"\n"


def send_json(conn, packet):
    conn.sendall(encode_json(packet))


class JsonStream:
    def __init__(self, conn):
        self.conn = conn
        self.buffer = bytearray()

    def receive(self):
        """Read one frame, preserving bytes for subsequent frames.

        EOFError means a clean disconnect or an idle connection timeout.
        An unfinished frame is an error, even if its JSON looks complete.
        """
        while True:
            end = self.buffer.find(b"\n")
            if end >= 0:
                if end > MAX_PACKET_BYTES:
                    raise ValueError("Packet is too large")
                frame = bytes(self.buffer[:end])
                del self.buffer[:end + 1]
                return decode_json(frame)
            if len(self.buffer) > MAX_PACKET_BYTES:
                raise ValueError("Packet is too large")
            try:
                chunk = self.conn.recv(4096)
            except socket.timeout as error:
                if self.buffer:
                    raise ValueError("Timed out before JSON packet's terminating newline") from error
                raise EOFError from error
            if not chunk:
                if self.buffer:
                    raise ValueError("Connection ended before JSON packet's terminating newline")
                raise EOFError
            self.buffer.extend(chunk)
