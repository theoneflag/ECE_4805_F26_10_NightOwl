import json
import socket

HOST = "127.0.0.1"
PORT = 5000

def validate_packet(packet):
    if "packet_id" not in packet:
        raise ValueError("Missing packet_id")

    if "timestamp" not in packet:
        raise ValueError("Missing timestamp")

    if "object" not in packet:
        raise ValueError("Missing object field")

    if "position" not in packet:
        raise ValueError("Missing position field")

    if not isinstance(packet["object"], dict):
        raise ValueError("Object field must contain JSON object data")

    if not isinstance(packet["position"], dict):
        raise ValueError("Position field must contain JSON object data")

    confidence = packet["object"].get("confidence")

    if confidence is None or not isinstance(confidence, (int, float)):
        raise ValueError("Confidence must be numeric")

    if not 0.0 <= confidence <= 1.0:
        raise ValueError("Confidence must be between 0 and 1")

    latitude = packet["position"].get("latitude")
    longitude = packet["position"].get("longitude")

    if latitude is None or not isinstance(latitude, (int, float)):
        raise ValueError("Latitude must be numeric")

    if not -90.0 <= latitude <= 90.0:
        raise ValueError("Invalid latitude")

    if longitude is None or not isinstance(longitude, (int, float)):
        raise ValueError("Longitude must be numeric")

    if not -180.0 <= longitude <= 180.0:
        raise ValueError("Invalid longitude")


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

                raw_data = conn.recv(4096).decode("utf-8")

                try:
                    packet = json.loads(raw_data)

                    print("\n===== RAW PACKET RECEIVED =====")
                    print(json.dumps(packet, indent=4))
                    print("================================")

                    validate_packet(packet)

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

                except (ValueError, KeyError, TypeError) as error:
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
                print(f"Latitude      : {packet['position']['latitude']}")
                print(f"Longitude     : {packet['position']['longitude']}")
                print(f"Altitude (m)  : {packet['position']['altitude_m']}")
                print(f"BBox          : {packet['bbox']}")
                print(f"Thumbnail     : {packet['thumbnail']}")
                print("=========================")
                print("\nWaiting for another packet...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nReceiver stopped.")
