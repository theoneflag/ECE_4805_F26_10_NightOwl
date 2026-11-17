import argparse
import json
import socket
import time
from pathlib import Path

from protocol import JsonStream, SOCKET_TIMEOUT, decode_json, send_json
from receiver import validate_packet
from radio import REPLY_TIMEOUT, RadioStream, add_radio_arguments, list_ports, open_radio

HOST = "127.0.0.1"
PORT = 5000


def check_response(response, packet):
    if not isinstance(response, dict) or response.get("status") not in ("accepted", "rejected"):
        raise ValueError("Response must be an object with accepted or rejected status")
    if response["status"] == "accepted" and (
        type(response.get("packet_id")) is not int
        or response["packet_id"] != packet["packet_id"]
    ):
        raise ValueError("Response packet ID does not match the sent packet")


def send_radio_packet(stream, packet, timeout=REPLY_TIMEOUT):
    """Send once, waiting for this packet's reply rather than a stale ACK."""
    validate_packet(packet)
    stream.send(packet)
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("No matching radio acknowledgment before timeout")
        try:
            response = stream.receive(timeout=remaining)
        except ValueError:
            # A corrupt frame has been consumed; the next frame may be valid.
            continue
        if not isinstance(response, dict):
            continue
        if response.get("status") not in ("accepted", "rejected"):
            continue
        if "packet_id" in response:
            if type(response["packet_id"]) is not int or response["packet_id"] != packet["packet_id"]:
                continue
        elif response["status"] != "rejected":
            continue
        check_response(response, packet)
        return response


def main(argv=None):
    parser = argparse.ArgumentParser(description="Send a sample Night Owl detection")
    add_radio_arguments(parser)
    options = parser.parse_args(argv)
    if options.baud <= 0:
        parser.error("--baud must be positive")
    if options.list_ports:
        try:
            list_ports()
        except OSError as error:
            parser.exit(1, f"{error}\n")
        return
    try:
        sample_path = Path(__file__).with_name("sample_packet.json")
        packet = decode_json(sample_path.read_bytes())
        validate_packet(packet)
    except OSError as error:
        print(f"Error reading sample_packet.json: {error}")
        return
    except ValueError as error:
        print(f"Error: sample_packet.json is invalid: {error}")
        return

    try:
        if options.serial_port:
            with open_radio(options.serial_port, options.baud) as device:
                stream = RadioStream(device)
                response = send_radio_packet(stream, packet)
        else:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                client.settimeout(SOCKET_TIMEOUT)
                client.connect((HOST, PORT))
                send_json(client, packet)
                response = JsonStream(client).receive()
        check_response(response, packet)
    except ConnectionRefusedError:
        print("Error: No receiver is listening. Start receiver.py or gui.py first.")
        return
    except EOFError:
        print("Error: Receiver closed the connection or timed out without responding.")
        return
    except ValueError as error:
        print(f"Error: Invalid JSON packet or response: {error}")
        return
    except OSError as error:
        print(f"Communication error: {error}")
        return

    print("\n===== PACKET SENT =====")
    print(json.dumps(packet, indent=4, allow_nan=False))
    print("=======================")
    print("\n===== RECEIVER RESPONSE =====")
    print(json.dumps(response, indent=4, allow_nan=False))
    print("=============================\n")


if __name__ == "__main__":
    main()
