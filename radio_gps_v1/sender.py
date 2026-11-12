"""Send one sample packet, or simulate a timed stream without a camera."""
from contextlib import ExitStack
import argparse
from gps_provider import add_gps_arguments, make_provider
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


def simulated_packets(template, count, start_id, seed, interval=0):
    rng = random.Random(seed)
    for index in range(count):
        if index:
            time.sleep(interval)
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
    parser.add_argument('--serial-port', help='Serial radio port, e.g. COM7')
    parser.add_argument('--baud', type=int, default=57600)
    parser.add_argument('--ack-timeout', type=float, default=10)
    parser.add_argument('--simulate', action='store_true')
    parser.add_argument('--count', type=int, default=30)
    parser.add_argument('--interval', type=float, default=0.5)
    parser.add_argument('--start-id', type=int, default=1000)
    parser.add_argument('--seed', type=int, default=42)
    add_gps_arguments(parser, default=None)
    args = parser.parse_args()
    if args.baud <= 0 or args.ack_timeout <= 0:
        parser.error('baud and ack-timeout must be positive')
    if args.count < 1 or args.interval < 0:
        parser.error('count must be positive and interval must be nonnegative')
    try:
        template = json.loads(args.packet.read_text(encoding='utf-8'))
        packets = (simulated_packets(template, args.count, args.start_id, args.seed, args.interval)
                   if args.simulate else iter([template]))
        with ExitStack() as stack:
            provider = make_provider(args, args.serial_port) if args.gps is not None else None
            if provider is not None:
                provider.start()
                stack.callback(provider.close)
                if args.gps == 'simulate':
                    print('[GPS] SIMULATED TEST DATA; coordinates are not real GPS.')
            if args.serial_port:
                try:
                    import serial
                except ImportError:
                    raise ValueError('Install serial support: py -m pip install pyserial') from None
                from serial_link import send_serial_packet, LineReader
                radio = stack.enter_context(serial.Serial(args.serial_port, baudrate=args.baud,
                                                         timeout=.2, write_timeout=5))
                reader = LineReader()
            failed = False
            for packet in packets:
                if provider is not None:
                    packet = provider.attach(packet)
                response = (send_serial_packet(radio, packet, reader, args.ack_timeout)
                            if args.serial_port else send_packet(packet, args.host, args.port))
                print(f"Packet {packet['packet_id']}: {json.dumps(response)}")
                failed |= response.get('status') != 'accepted'
            return int(failed)
    except (OSError, ValueError, KeyError, TypeError, ImportError) as error:
        print(f'Send failed: {error}')
        return 1
    except KeyboardInterrupt:
        print('\nSimulation stopped.')
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
