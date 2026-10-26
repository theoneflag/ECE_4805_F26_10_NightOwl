"""Send one sample packet, or simulate a timed stream without a camera."""
import argparse
import copy
import json
from pathlib import Path
import random
import socket
import time


def send_packet(packet, host, port, timeout=5):
    with socket.create_connection((host, port), timeout=timeout) as client:
        client.sendall(json.dumps(packet, allow_nan=False).encode('utf-8'))
        raw = bytearray()
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > 1_000_000:
                raise ValueError('Response too large')
        if not raw:
            raise ValueError('Receiver closed without responding')
        response = json.loads(raw)
        if not isinstance(response, dict):
            raise ValueError('Response must be a JSON object')
        if response.get('status') == 'accepted' and response.get('packet_id') != packet['packet_id']:
            raise ValueError('Acknowledgment packet ID does not match')
        return response


def simulated_packets(template, count, start_id, seed):
    rng = random.Random(seed)
    for index in range(count):
        packet = copy.deepcopy(template)
        packet['packet_id'] = start_id + index
        packet['timestamp'] = time.time()
        packet['object'] = {'type': rng.choice(['person', 'vehicle']),
                            'confidence': round(rng.uniform(0.50, 0.99), 3)}
        # Coordinates, bbox and image reference remain those of the test fixture.
        yield packet


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packet', type=Path, default=Path(__file__).with_name('sample_packet.json'))
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=5000)
    parser.add_argument('--simulate', action='store_true')
    parser.add_argument('--count', type=int, default=30)
    parser.add_argument('--interval', type=float, default=0.5)
    parser.add_argument('--start-id', type=int, default=1000)
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    if args.count < 1 or args.interval < 0:
        parser.error('count must be positive and interval must be nonnegative')
    try:
        template = json.loads(args.packet.read_text(encoding='utf-8'))
        packets = (simulated_packets(template, args.count, args.start_id, args.seed)
                   if args.simulate else iter([template]))
        failed = False
        for index, packet in enumerate(packets):
            if index:
                time.sleep(args.interval)
            response = send_packet(packet, args.host, args.port)
            print(f"Packet {packet['packet_id']}: {json.dumps(response)}")
            failed |= response.get('status') != 'accepted'
        return int(failed)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f'Send failed: {error}')
        return 1
    except KeyboardInterrupt:
        print('\nSimulation stopped.')
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
