"""Run the existing Night Owl camera program through the serial radio.
The source camera file is read, adapted in memory, and never overwritten.
"""
import argparse
import ast
import copy
from contextlib import ExitStack
from datetime import datetime, timezone
import importlib.util
import math
from pathlib import Path
import queue
import sys
import threading
import time


class AdaptCamera(ast.NodeTransformer):
    def __init__(self, snapshot_dir):
        self.snapshot_dir = str(snapshot_dir)
        self.changed = {'get_gps': 0, 'send_packet': 0, 'dispatch': 0,
                        'SNAPSHOT_DIR': 0, 'packet_id': 0, 'capture': 0}

    def visit_FunctionDef(self, node):
        if node.name == 'get_gps':
            self.changed['get_gps'] += 1
            node.body = ast.parse('return _gps.position() if _gps is not None else None').body
            return node
        if node.name == 'send_packet':
            self.changed['send_packet'] += 1
            node.body = ast.parse('_radio.enqueue(packet)').body
            return node
        if node.name == 'trigger_packet':
            for i, stmt in enumerate(node.body):
                if isinstance(stmt, ast.Expr) and ast.unparse(stmt.value) == 'threading.Thread(target=send_packet, args=(packet,), daemon=True).start()':
                    node.body[i] = ast.parse('send_packet(packet)').body[0]
                    self.changed['dispatch'] += 1
        return self.generic_visit(node)

    def visit_Assign(self, node):
        if any(isinstance(t, ast.Name) and t.id == 'SNAPSHOT_DIR' for t in node.targets):
            self.changed['SNAPSHOT_DIR'] += 1
            node.value = ast.Constant(self.snapshot_dir)
        if any(isinstance(t, ast.Name) and t.id == 'packet_id' for t in node.targets):
            self.changed['packet_id'] += 1
            node.value = ast.Constant(time.time_ns())
        return self.generic_visit(node)

    def visit_Call(self, node):
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == 'subprocess' and node.func.attr == 'Popen':
            node.func = ast.Name(id='_start_capture', ctx=ast.Load())
            self.changed['capture'] += 1
        return self.generic_visit(node)


def adapt(source, snapshot_dir, filename):
    tree = ast.parse(source, filename)
    transformer = AdaptCamera(snapshot_dir)
    tree = transformer.visit(tree)
    if any(count != 1 for count in transformer.changed.values()):
        raise ValueError('Camera source differs from the reviewed script; refusing to launch: '
                         + repr(transformer.changed))
    return compile(ast.fix_missing_locations(tree), filename, 'exec')


class RadioWorker:
    """One serial writer, bounded queue, no blind retry of uncertain deliveries."""
    def __init__(self, port, send, reader, snapshot_dir=None, images=True, gps=None):
        self.port, self.send, self.reader = port, send, reader
        self.snapshot_dir = Path(snapshot_dir) if snapshot_dir is not None else None
        self.images = images
        self.gps = gps
        self.pending = queue.Queue(maxsize=10)
        self.stop = threading.Event()
        self.worker = threading.Thread(target=self.run, daemon=True)
        self.worker.start()

    def enqueue(self, packet):
        packet = copy.deepcopy(packet)
        gps = getattr(self, 'gps', None)
        if gps is not None:
            sample = gps.snapshot()
            packet['position'] = sample['position']
            packet['gps'] = sample['gps']
        name = packet.get('thumbnail')
        snapshot_dir = getattr(self, 'snapshot_dir', None)
        image_path = None
        if getattr(self, 'images', False) and snapshot_dir is not None and isinstance(name, str) and Path(name).name == name:
            image_path = snapshot_dir / name
        # Receiver gets its own local filename after verified image completion.
        packet['thumbnail'] = None
        try:
            self.pending.put_nowait((packet, image_path))
            print(f"[RADIO] Queued {packet['packet_id']}", flush=True)
        except queue.Full:
            print('[RADIO] Queue full; detection not transmitted', flush=True)

    def run(self):
        while not self.stop.is_set():
            try:
                packet, image_path = self.pending.get(timeout=.2)
            except queue.Empty:
                continue
            metadata_accepted = False
            try:
                thumbnail = None
                if image_path is not None:
                    try:
                        from image_transfer import make_thumbnail, image_manifest
                        label = f"{packet['object']['type']} {packet['object']['confidence']:.0%}"
                        thumbnail = make_thumbnail(image_path, packet.get('bbox'), label)
                        packet['image'] = image_manifest(thumbnail)
                    except (OSError, ValueError, TypeError, KeyError) as error:
                        print(f"[IMAGE] {packet['packet_id']}: {error}; sending detection metadata", flush=True)
                reply = self.send(self.reader, packet, timeout=10)
                print(f"[RADIO] {packet['packet_id']}: {reply}", flush=True)
                metadata_accepted = reply.get('status') == 'accepted'
                if thumbnail is not None and reply.get('status') == 'accepted':
                    from image_transfer import send_image
                    print(f"[IMAGE] Sending thumbnail for {packet['packet_id']} ({len(thumbnail)} bytes)", flush=True)
                    send_image(self.reader, packet['packet_id'], packet['image'], thumbnail, self.stop)
                    print(f"[IMAGE] Thumbnail received for {packet['packet_id']}", flush=True)
            except (OSError, ValueError, TypeError) as error:
                if metadata_accepted:
                    print(f"[IMAGE] {packet['packet_id']}: {error}; detection metadata was accepted", flush=True)
                else:
                    print(f"[RADIO] {packet['packet_id']}: {error}; delivery uncertain, no retry", flush=True)
            finally:
                self.pending.task_done()

    def close(self):
        self.stop.set()
        self.worker.join(timeout=11)
        self.port.close()


def main():
    code_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=code_dir / 'night_owl_final.py',
                        help='Camera source file (default: night_owl_final.py beside this launcher)')
    parser.add_argument('--bench-dir', type=Path, default=code_dir,
                        help='Directory containing the shared radio.py and sender.py modules')
    parser.add_argument('--serial-port', default='/dev/ttyUSB0')
    parser.add_argument('--baud', type=int, default=115200)
    parser.add_argument('--no-images', action='store_true', help='Send detection metadata without thumbnails')
    parser.add_argument('--gps', choices=('mavlink', 'none'), default='mavlink',
                        help='Read Pixhawk GPS over USB (default: mavlink)')
    parser.add_argument('--gps-device', default='auto', help='Pixhawk USB serial device, or auto')
    parser.add_argument('--gps-baud', type=int, default=115200)
    parser.add_argument('--gps-max-age', type=float, default=5,
                        help='Maximum accepted age of GPS data in seconds (default: 5)')
    parser.add_argument('--check', action='store_true', help='Check adaptation and dependencies without starting camera or radio')
    args = parser.parse_args()
    if args.baud <= 0:
        parser.error('--baud must be positive')
    if args.gps_baud <= 0 or not math.isfinite(args.gps_max_age) or args.gps_max_age <= 0:
        parser.error('--gps-baud and --gps-max-age must be positive and finite')
    snapshots = Path.home() / 'detections_radio' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    try:
        camera = adapt(args.source.read_text(), snapshots, str(args.source))
        sys.path.insert(0, str(args.bench_dir.resolve()))
        required = ['cv2', 'numpy', 'gi', 'hailo', 'serial']
        if not args.no_images:
            required.append('PIL')
        if args.gps == 'mavlink':
            required.append('pymavlink')
        missing = [name for name in required
                   if importlib.util.find_spec(name) is None]
        if missing:
            raise RuntimeError('Missing Python modules: ' + ', '.join(missing)
                               + '. Use the camera environment with system packages plus pyserial.')
        from radio import RadioStream, open_radio
        from sender import send_radio_packet
        from mavlink_gps import MavlinkGPS
        if args.check:
            print('Adaptation and Python module discovery passed. Camera/Hailo pipeline and radio are not started.')
            return 0
        import subprocess
        with ExitStack() as cleanup:
            port = open_radio(args.serial_port, args.baud)
            cleanup.callback(port.close)
            gps = MavlinkGPS(args.gps_device, args.gps_baud, args.gps_max_age) if args.gps == 'mavlink' else None
            if gps is not None:
                cleanup.callback(gps.close)
            worker = RadioWorker(port, send_radio_packet, RadioStream(port), snapshots, not args.no_images, gps)
            cleanup.callback(worker.close)
            processes = []
            def stop_captures():
                for process in processes:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=3)
            cleanup.callback(stop_captures)
            def start_capture(*a, **kw):
                process = subprocess.Popen(*a, **kw)
                processes.append(process)
                return process
            print(f'[RADIO] {args.serial_port} at {args.baud}; original snapshots: {snapshots}', flush=True)
            print('[GPS] ' + (f'USB MAVLink enabled: {args.gps_device}' if gps else 'Disabled'), flush=True)
            print('[IMAGE] ' + ('Metadata only' if args.no_images else 'Compact JPEG thumbnails enabled'), flush=True)
            print('[CAMERA] Original threshold 0.85 and per-label cooldown 10 seconds retained. Ctrl+C stops.', flush=True)
            exec(camera, {'__name__': '__main__', '__file__': str(args.source),
                          '_radio': worker, '_gps': gps, '_start_capture': start_capture})
        return 0
    except KeyboardInterrupt:
        print('\nStopped live radio camera test.')
        return 130
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        print(f'Cannot launch: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
