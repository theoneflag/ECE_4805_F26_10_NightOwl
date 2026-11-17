import socket
from pathlib import Path

from detection import Detection
from receiver import handle_connection, validate_packet
from radio import DEFAULT_BAUD, RadioStream, open_radio
from image_transfer import ImageReceiver, image_ack


def receive_packets(incoming, host="127.0.0.1", port=5000, serial_port=None, baud=DEFAULT_BAUD,
                    image_directory=None):
    if serial_port is not None:
        receive_radio_packets(incoming, serial_port, baud, image_directory)
        return
    def on_packet(packet):
        incoming.put(("detection", Detection.from_packet(packet)))

    def on_error(message):
        incoming.put(("status", message))

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((host, port))
            server.listen()
            incoming.put(("status", f"Listening on {host}:{port}"))

            while True:
                conn, address = server.accept()
                with conn:
                    handle_connection(conn, on_packet, on_error)
    except OSError as error:
        on_error(f"Receiver error: {error}")


def receive_radio_packets(incoming, serial_port, baud, image_directory=None):
    try:
        with open_radio(serial_port, baud) as device:
            stream = RadioStream(device)
            images = ImageReceiver(image_directory or Path(__file__).resolve().parent / "received_images")
            incoming.put(("status", f"Listening on JSON radio {serial_port} at {baud} baud"))
            while True:
                packet = None
                try:
                    packet = stream.receive()
                    if isinstance(packet, dict) and packet.get("kind") in ("image_chunk", "image_end", "image_abort"):
                        try:
                            response, completed = images.handle(packet)
                        except OSError as error:
                            raise ValueError(f"Cannot save thumbnail: {error}") from error
                        if completed is not None:
                            incoming.put(("image", completed))
                    else:
                        validate_packet(packet)
                        images.begin(packet)
                        incoming.put(("detection", Detection.from_packet(packet)))
                        response = {"packet_id": packet["packet_id"], "status": "accepted"}
                except (ValueError, KeyError, TypeError) as error:
                    incoming.put(("status", f"Radio packet rejected: {error}"))
                    if isinstance(packet, dict) and packet.get("kind") in ("image_chunk", "image_end", "image_abort"):
                        response = image_ack(packet, "rejected", error)
                    else:
                        response = {"status": "rejected", "error": str(error)}
                stream.send(response)
    except (OSError, ValueError) as error:
        incoming.put(("status", f"Radio receiver error: {error}"))
