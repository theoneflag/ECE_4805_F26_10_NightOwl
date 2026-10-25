import json
import socket

HOST = "127.0.0.1"
PORT = 5000

def main():
    try:
        with open("sample_packet.json", "r", encoding="utf-8") as file:
            packet = json.load(file)

    except FileNotFoundError:
        print("Error: sample_packet.json not found.")
        return

    except json.JSONDecodeError as error:
        print(f"Error: sample_packet.json is invalid: {error}")
        return

    payload = json.dumps(packet)

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.connect((HOST, PORT))
            client.sendall(payload.encode("utf-8"))

            response_data = client.recv(4096).decode("utf-8")

            if not response_data:
                print("Error: Receiver closed the connection without responding.")
                return

            response = json.loads(response_data)

    except ConnectionRefusedError:
        print("Error: receiver.py is not running yet. Start the receiver first.")
        return

    except json.JSONDecodeError:
        print("Error: Receiver returned an invalid JSON response.")
        return

    except OSError as error:
        print(f"Network error: {error}")
        return

    print("\n===== PACKET SENT =====")
    print(json.dumps(packet, indent=4))
    print("=======================")

    print("\n===== RECEIVER RESPONSE =====")
    print(json.dumps(response, indent=4))
    print("=============================\n")


if __name__ == "__main__":
    main()
