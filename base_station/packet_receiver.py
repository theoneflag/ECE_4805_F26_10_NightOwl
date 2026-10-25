import json
import socket

from detection import Detection
from receiver import validate_packet

def receive_packets(incoming, host="127.0.0.1", port=5000):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((host, port))
            server.listen()

            incoming.put(("status", f"Listening on {host}:{port}"))

            while True:
                conn, address = server.accept()

                with conn:
                    conn.settimeout(5)
                    raw_data = bytearray()

                    try:
                        # TCP may deliver one packet in several chunks.
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

                        if not isinstance(packet, dict):
                            raise ValueError("Packet must be a JSON object")

                        validate_packet(packet)

                        object_type = packet["object"].get("type")
                        if not isinstance(object_type, str) or not object_type.strip():
                            raise ValueError("Object type must be a nonempty string")

                        detection = Detection.from_packet(packet)
                        incoming.put(("detection", detection))

                        response = {
                            "packet_id": packet["packet_id"],
                            "status": "accepted",
                        }

                    except (ValueError, KeyError, TypeError, OSError) as error:
                        incoming.put(("status", f"Packet rejected: {error}"))
                        response = {
                            "status": "rejected",
                            "error": str(error),
                        }

                    try:
                        conn.sendall(json.dumps(response).encode("utf-8"))
                    except OSError:
                        incoming.put(("status", "Sender disconnected before reply"))

    except OSError as error:
        incoming.put(("status", f"Receiver error: {error}"))


