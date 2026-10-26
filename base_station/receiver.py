import json
import socket
import math
from datetime import datetime
from detection import Detection

HOST = "127.0.0.1"
PORT = 5000

def finite_number(value, name):
    # JSON booleans are not measurements, although bool subclasses int.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f"{name} must be finite")
    return value


def validate_packet(packet):
    """Validate measurements; absent GPS is allowed, malformed GPS is not."""
    if not isinstance(packet, dict):
        raise ValueError("Packet must be a JSON object")
    packet_id = packet.get("packet_id")
    if isinstance(packet_id, bool) or not isinstance(packet_id, int) or packet_id < 0:
        raise ValueError("packet_id must be a nonnegative integer")

    timestamp = finite_number(packet.get("timestamp"), "timestamp")
    if timestamp < 0:
        raise ValueError("timestamp must be Unix seconds at or after 1970")
    try:
        # Check the same conversion used by the GUI on this computer.
        datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OverflowError, OSError):
        raise ValueError("timestamp is outside the supported date range") from None

    obj = packet.get("object")
    if not isinstance(obj, dict):
        raise ValueError("object must be a JSON object")
    label = obj.get("type")
    if not isinstance(label, str) or not label.strip():
        raise ValueError("Object type must be a nonempty string")
    confidence = finite_number(obj.get("confidence"), "Confidence")
    if not 0 <= confidence <= 1:
        raise ValueError("Confidence must be between 0 and 1")

    position = packet.get("position")
    if position is not None and not isinstance(position, dict):
        raise ValueError("position must be a JSON object or null")
    position = position or {}
    latitude, longitude = position.get("latitude"), position.get("longitude")
    if (latitude is None) != (longitude is None):
        raise ValueError("Provide both latitude and longitude, or neither")
    if latitude is not None:
        latitude = finite_number(latitude, "Latitude")
        longitude = finite_number(longitude, "Longitude")
        if not -90 <= latitude <= 90:
            raise ValueError("Invalid latitude")
        if not -180 <= longitude <= 180:
            raise ValueError("Invalid longitude")
    altitude = position.get("altitude_m")
    if altitude is not None:
        finite_number(altitude, "Altitude")

    bbox = packet.get("bbox")
    if bbox is not None:
        if not isinstance(bbox, dict):
            raise ValueError("bbox must be a JSON object or null")
        for name in ("x", "y", "width", "height"):
            value = finite_number(bbox.get(name), f"bbox.{name}")
            if name in ("x", "y") and value < 0:
                raise ValueError(f"bbox.{name} must be nonnegative")
            if name in ("width", "height") and value <= 0:
                raise ValueError(f"bbox.{name} must be positive")

    thumbnail = packet.get("thumbnail")
    if thumbnail is not None and (
        not isinstance(thumbnail, str) or not thumbnail.strip() or "\x00" in thumbnail
    ):
        raise ValueError("thumbnail must be a nonempty filename string or null")


def send_response(conn, response):
    payload = json.dumps(response)
    conn.sendall(payload.encode("utf-8"))


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

                conn.settimeout(5)
                try:
                    raw_data = bytearray()
                    while True:
                        chunk = conn.recv(4096)
                        if not chunk:
                            raise ValueError("Connection ended before valid JSON")
                        raw_data.extend(chunk)
                        if len(raw_data) > 1_000_000:
                            raise ValueError("Packet is too large")
                        try:
                            packet = json.loads(raw_data)
                            break
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            continue

                    print("\n===== RAW PACKET RECEIVED =====")
                    print(json.dumps(packet, indent=4))
                    print("================================")

                    validate_packet(packet)
                    detection = Detection.from_packet(packet)

                    response = {
                        "packet_id": packet["packet_id"],
                        "status": "accepted"
                    }

                    send_response(conn, response)

                except json.JSONDecodeError as error:
                    print(f"\nPACKET REJECTED: Invalid JSON — {error}")

                    response = {
                        "status": "rejected",
                        "error": "Invalid JSON"
                    }

                    send_response(conn, response)
                    continue

                except (ValueError, KeyError, TypeError, OSError) as error:
                    print(f"\nPACKET REJECTED: {error}")

                    response = {
                        "status": "rejected",
                        "error": str(error)
                    }

                    send_response(conn, response)
                    continue

                print("\n===== PACKET ACCEPTED =====")
                print("\n===== PARSED DATA =====")
                print(f"Packet ID     : {packet['packet_id']}")
                print(f"Timestamp     : {packet['timestamp']}")
                print(f"Object Type   : {packet['object']['type']}")
                print(f"Confidence    : {packet['object']['confidence']:.2f}")
                print(f"Latitude      : {detection.formatted_latitude}")
                print(f"Longitude     : {detection.formatted_longitude}")
                print(f"Altitude (m)  : {detection.altitude_m if detection.altitude_m is not None else 'Unavailable'}")
                print(f"BBox          : {packet.get('bbox', 'Unavailable')}")
                print(f"Thumbnail     : {packet.get('thumbnail', 'Unavailable')}")
                print("=========================")
                print("\nWaiting for another packet...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nReceiver stopped.")
