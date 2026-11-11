"""Run the existing Night Owl camera program through the serial radio.
The source camera file is read, adapted in memory, and never overwritten.
"""
from contextlib import ExitStack
import argparse
from gps_provider import add_gps_arguments, make_provider
import ast
import copy
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import queue
import sys
import threading
import time


class AdaptCamera(ast.NodeTransformer):
    def __init__(self, snapshot_dir):
        self.snapshot_dir = str(snapshot_dir)
        self.changed = {'get_gps': 0, 'send_packet': 0, 'dispatch': 0,
                        'SNAPSHOT_DIR': 0, 'packet_id': 0, 'capture': 0, 'gps_attach': 0}

    def visit_FunctionDef(self, node):
        if node.name == 'get_gps':
            self.changed['get_gps'] += 1
            node.body = ast.parse('return None').body
            return node
        if node.name == 'send_packet':
            self.changed['send_packet'] += 1
            node.body = ast.parse('_radio.enqueue(packet)').body
            return node
        if node.name == 'trigger_packet':
            for i, stmt in enumerate(node.body):
                if (isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Dict)
                        and any(isinstance(t, ast.Name) and t.id == 'packet' for t in stmt.targets)):
                    stmt.value = ast.Call(func=ast.Attribute(value=ast.Name(id='_gps', ctx=ast.Load()),
                                          attr='attach', ctx=ast.Load()),
                                          args=[stmt.value], keywords=[])
                    self.changed['gps_attach'] += 1
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
    def __init__(self, port, send, reader, snapshots=None, images=True):
        self.port, self.send, self.reader = port, send, reader
        self.snapshots, self.images = snapshots, images
        self.pending = queue.Queue(maxsize=2)
        self.stop = threading.Event()
        self.worker = threading.Thread(target=self.run, daemon=True)
        self.worker.start()

    def enqueue(self, packet):
        packet = copy.deepcopy(packet)
        try:
            self.pending.put_nowait(packet)
            print(f"[RADIO] Queued {packet['packet_id']}", flush=True)
        except queue.Full:
            print('[RADIO] Queue full; detection not transmitted', flush=True)

    def run(self):
        while not self.stop.is_set():
            try:
                packet = self.pending.get(timeout=.2)
            except queue.Empty:
                continue
            try:
                if self.images and self.snapshots:
                    from radio_images import attach_preview
                    try:
                        packet = attach_preview(packet, self.snapshots)
                    except (OSError, ValueError, ImportError) as error:
                        print(f'[RADIO] Preview unavailable: {error}; sending metadata only', flush=True)
                        packet['thumbnail'] = None
                else:
                    packet['thumbnail'] = None
                reply = self.send(self.port, packet, self.reader, timeout=15)
                print(f"[RADIO] {packet['packet_id']}: {reply}", flush=True)
            except (OSError, ValueError, TypeError) as error:
                print(f"[RADIO] {packet['packet_id']}: {error}; delivery uncertain, no retry", flush=True)
            finally:
                self.pending.task_done()

    def close(self):
        self.stop.set()
        self.worker.join(timeout=16)
        self.port.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path.home() / 'night_owl_final.py')
    parser.add_argument('--bench-dir', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--serial-port', default='/dev/ttyUSB0')
    parser.add_argument('--baud', type=int, default=57600)
    parser.add_argument('--no-images', action='store_true', help='Send metadata only')
    parser.add_argument('--check', action='store_true', help='Check adaptation and dependencies without starting camera or radio')
    add_gps_arguments(parser)
    args = parser.parse_args()
    snapshots = Path.home() / 'detections_radio' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    try:
        provider = make_provider(args, args.serial_port)
        camera = adapt(args.source.read_text(), snapshots, str(args.source))
        sys.path.insert(0, str(args.bench_dir.resolve()))
        missing = [name for name in ('cv2', 'numpy', 'gi', 'hailo', 'serial')
                   if importlib.util.find_spec(name) is None]
        if args.gps == 'mavlink' and importlib.util.find_spec('pymavlink') is None:
            missing.append('pymavlink (optional requirements-gps.txt)')
        if missing:
            raise RuntimeError('Missing Python modules: ' + ', '.join(missing)
                               + '. Use the camera environment with system packages plus pyserial.')
        from serial_link import LineReader, send_serial_packet
        if args.check:
            print('Adaptation and Python module discovery passed. Camera/Hailo pipeline and radio are not started.')
            return 0
        with ExitStack() as resources:
            if args.gps == 'simulate':
                print('[GPS] SIMULATED TEST DATA; these coordinates are not real GPS.', flush=True)
            provider.start()
            resources.callback(provider.close)
            import subprocess
            import serial
            port = serial.Serial(args.serial_port, args.baud, timeout=.2, write_timeout=5)
            worker = RadioWorker(port, send_serial_packet, LineReader(), snapshots, not args.no_images)
            processes = []
            def start_capture(*a, **kw):
                process = subprocess.Popen(*a, **kw)
                processes.append(process)
                return process
            print(f'[RADIO] {args.serial_port} at {args.baud}; GPS={args.gps}; previews={not args.no_images}; originals on Pi: {snapshots}', flush=True)
            print('[CAMERA] Original threshold 0.85 and per-label cooldown 10 seconds retained. Ctrl+C stops.', flush=True)
            try:
                exec(camera, {'__name__': '__main__', '__file__': str(args.source),
                              '_radio': worker, '_start_capture': start_capture, '_gps': provider})
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=3)
                worker.close()
            return 0
    except KeyboardInterrupt:
        print('\nStopped live radio camera test.')
        return 130
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        print(f'Cannot launch: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
