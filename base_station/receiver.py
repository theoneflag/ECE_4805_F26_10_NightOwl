import json
import math
import socket
from datetime import datetime

from protocol import JsonStream, SOCKET_TIMEOUT, send_json

HOST = "127.0.0.1"
PORT = 5000


def _require_number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f"{name} must be finite")


def validate_packet(packet):
    if not isinstance(packet, dict):
        raise ValueError("Packet must be a JSON object")
    for field in ("packet_id", "timestamp", "object", "position"):
        if field not in packet:
            raise ValueError(f"Missing {field}")

    if type(packet["packet_id"]) is not int:
        raise ValueError("Packet ID must be an integer")
    _require_number(packet["timestamp"], "Timestamp")
    try:
        datetime.fromtimestamp(packet["timestamp"])
    except (ValueError, OverflowError, OSError) as error:
        raise ValueError("Timestamp must be supported Unix time in seconds") from error

    if not isinstance(packet["object"], dict):
        raise ValueError("Object field must contain JSON object data")
    position = packet["position"]
    if position is not None and not isinstance(position, dict):
        raise ValueError("Position field must contain JSON object data or null")

    object_type = packet["object"].get("type")
    if not isinstance(object_type, str) or not object_type.strip():
        raise ValueError("Object type must be a nonempty string")

    confidence = packet["object"].get("confidence")
    _require_number(confidence, "Confidence")
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("Confidence must be between 0 and 1")

    if position is not None:
        latitude = position.get("latitude")
        longitude = position.get("longitude")
        _require_number(latitude, "Latitude")
        _require_number(longitude, "Longitude")
        if not -90.0 <= latitude <= 90.0:
            raise ValueError("Invalid latitude")
        if not -180.0 <= longitude <= 180.0:
            raise ValueError("Invalid longitude")
        altitude = position.get("altitude_m")
        if altitude is not None:
            _require_number(altitude, "Altitude")
    thumbnail = packet.get("thumbnail")
    if thumbnail is not None and not isinstance(thumbnail, str):
        raise ValueError("Thumbnail must be a string or null")


def send_response(conn, response):
    send_json(conn, response)


def handle_connection(conn, on_packet, on_error):
    """Use the same framing, validation and replies in both receivers."""
    conn.settimeout(SOCKET_TIMEOUT)
    stream = JsonStream(conn)
    while True:
        rejected = False
        try:
            packet = stream.receive()
            validate_packet(packet)
            on_packet(packet)
            response = {"packet_id": packet["packet_id"], "status": "accepted"}
        except EOFError:
            return
        except (ValueError, KeyError, TypeError, OSError) as error:
            on_error(f"Packet rejected: {error}")
            response = {"status": "rejected", "error": str(error)}
            rejected = True

        try:
            send_response(conn, response)
        except (OSError, ValueError) as error:
            on_error(f"Could not send reply: {error}")
            return
        if rejected:
            return


def print_packet(packet):
    position = packet["position"] or {}
    print("\n===== PACKET ACCEPTED =====")
    print(json.dumps(packet, indent=4, allow_nan=False))
    print("\n===== PARSED DATA =====")
    print(f"Packet ID     : {packet['packet_id']}")
    print(f"Timestamp     : {packet['timestamp']}")
    print(f"Object Type   : {packet['object']['type']}")
    print(f"Confidence    : {packet['object']['confidence']:.2f}")
    print(f"Latitude      : {position.get('latitude', 'Unavailable')}")
    print(f"Longitude     : {position.get('longitude', 'Unavailable')}")
    print(f"Altitude (m)  : {position.get('altitude_m')}")
    print(f"BBox          : {packet.get('bbox')}")
    print(f"Thumbnail     : {packet.get('thumbnail')}")
    print("=========================")


def main():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((HOST, PORT))
        server.listen()
        print("Night Owl Base Station Listening...")
        print(f"Listening on {HOST}:{PORT}...")
        print("Press Ctrl+C to stop.\n")

        while True:
            conn, addr = server.accept()
            with conn:
                print(f"\nConnected by {addr}")
                handle_connection(conn, print_packet, print)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nReceiver stopped.")
