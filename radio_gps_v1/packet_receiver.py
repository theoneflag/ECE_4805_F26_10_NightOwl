import json
import socket
import os
from pathlib import Path
from datetime import datetime, timezone

from detection import Detection
from receiver import validate_packet

def receive_packets(incoming, host="127.0.0.1", port=5000, log_directory=None):
    """One JSON object per connection; accepted means saved and queued."""
    directory = Path(log_directory) if log_directory is not None else Path(__file__).resolve().parent / "logs"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        session = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        log_path = directory / f"detections_{session}.jsonl"
        # Fail visibly before listening if logging cannot be initialized.
        log_path.touch(exist_ok=False)
    except OSError as error:
        incoming.put(("status", f"Cannot start detection log: {error}"))
        return
    incoming.put(("status", f"Logging detections to {log_path}"))
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

                        detection = Detection.from_packet(packet)
                        record = {
                            "received_at": datetime.now(timezone.utc).isoformat(),
                            "packet": packet,
                        }
                        # Save the complete packet, including bbox and thumbnail.
                        line = json.dumps(record, allow_nan=False) + "\n"
                        with log_path.open("a", encoding="utf-8") as log:
                            log.write(line)
                            log.flush()
                            os.fsync(log.fileno())
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


