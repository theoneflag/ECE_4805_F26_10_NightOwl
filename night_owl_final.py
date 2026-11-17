import subprocess
import threading
import time
import cv2
import numpy as np
import json
import socket
import os
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib
import hailo
from http.server import BaseHTTPRequestHandler, HTTPServer

Gst.init(None)

# --- Config ---
HEF_PATH = '/home/nightowl/yolov11m.hef'
SNAPSHOT_DIR = '/home/nightowl/detections'
BASE_STATION_HOST = '127.0.0.1'
BASE_STATION_PORT = 5000
CONFIDENCE_THRESHOLD = 0.85
COOLDOWN_SECONDS = 10
STREAM_PORT = 8080

TARGET_CLASSES = {
    'person', 'car', 'truck', 'bus', 'motorcycle',
    'dog', 'cat', 'bird', 'horse', 'sheep', 'cow'
}

os.makedirs(SNAPSHOT_DIR, exist_ok=True)

# --- Shared State ---
latest_frame = None
frame_lock = threading.Lock()

sample_frame = None
sample_lock = threading.Lock()

latest_detections = []
det_lock = threading.Lock()

fps = 0
frame_count = 0
fps_time = time.time()

packet_id = 0
last_sent = {}
last_sent_lock = threading.Lock()

def get_gps():
    return {"latitude": 37.2211, "longitude": -80.4234, "altitude_m": 24.8}

def send_packet(packet):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            s.connect((BASE_STATION_HOST, BASE_STATION_PORT))
            s.sendall(json.dumps(packet).encode('utf-8'))
        print(f"[TX] Packet {packet['packet_id']}: {packet['object']['type']} {packet['object']['confidence']:.2f}")
    except Exception as e:
        print(f"[TX] Failed: {e}")

def trigger_packet(label, confidence, bbox, frame):
    global packet_id

    now = time.time()

    with last_sent_lock:
        if label in last_sent and now - last_sent[label] < COOLDOWN_SECONDS:
            return
        last_sent[label] = now

    packet_id += 1

    thumb_name = f"det_{packet_id}.jpg"
    cv2.imwrite(os.path.join(SNAPSHOT_DIR, thumb_name), frame)

    x, y, w, h = bbox

    packet = {
        "packet_id": packet_id,
        "timestamp": int(now),
        "object": {"type": label, "confidence": round(float(confidence), 2)},
        "position": get_gps(),
        "bbox": {"x": int(x), "y": int(y), "width": int(w), "height": int(h)},
        "thumbnail": thumb_name
    }

    threading.Thread(target=send_packet, args=(packet,), daemon=True).start()

# ---------------- STREAM CAPTURE ----------------

def stream_capture():
    global latest_frame, fps, frame_count, fps_time, sample_frame

    cmd = [
        'rpicam-vid', '-t', '0',
        '--width', '1456', '--height', '1088',
        '--nopreview', '-o', '-', '--codec', 'mjpeg',
        '--framerate', '60'
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)

    buf = b''
    fc = 0

    while True:
        chunk = proc.stdout.read(4096)
        if not chunk:
            break

        buf += chunk

        start = buf.find(b'\xff\xd8')
        end = buf.find(b'\xff\xd9')

        if start != -1 and end != -1:
            frame_data = buf[start:end+2]
            buf = buf[end+2:]

            fc += 1

            img = cv2.imdecode(np.frombuffer(frame_data, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                continue

            # sample for inference
            if fc % 30 == 0:
                with sample_lock:
                    sample_frame = img.copy()

            # FPS
            frame_count += 1
            now = time.time()
            if now - fps_time >= 1.0:
                fps = frame_count
                frame_count = 0
                fps_time = now

            # ---- DRAW BOUNDING BOXES ----
            with det_lock:
                dets = latest_detections.copy()

            for label, conf, (x1, y1, x2, y2) in dets:
                cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 2)
                cv2.putText(
                    img,
                    f"{label} {conf:.2f}",
                    (x1, y1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 255, 0),
                    2
                )

            cv2.putText(img, f'FPS: {fps}', (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
            g = globals().get('_gps')
            if g is not None:
                s = g.snapshot()
                p = s['position']
                if p:
                    t = f"GPS {p['latitude']:.6f} {p['longitude']:.6f}  ALT {p['altitude_m']:.1f} m"
                else:
                    t = f"GPS {s['gps']['status']}"
                cv2.putText(img, t, (10, 60),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

            _, encoded = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 70])

            with frame_lock:
                latest_frame = encoded.tobytes()

# ---------------- INFERENCE ----------------

def box_iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - inter
    return inter / union if union > 0 else 0

def inference_loop():
    global latest_detections

    pipeline_str = (
        f'appsrc name=src is-live=true format=time ! '
        f'video/x-raw,width=640,height=640,format=RGB,framerate=0/1 ! '
        f'hailonet hef-path={HEF_PATH} force-writable=true ! '
        f'hailofilter so-path=/usr/lib/aarch64-linux-gnu/hailo/tappas/post_processes/libyolo_hailortpp_post.so ! '
        f'appsink name=sink emit-signals=true sync=false max-buffers=1 drop=true'
    )

    pipeline = Gst.parse_launch(pipeline_str)
    src = pipeline.get_by_name('src')
    sink = pipeline.get_by_name('sink')
    pipeline.set_state(Gst.State.PLAYING)

    while True:
        with sample_lock:
            frame = sample_frame.copy() if sample_frame is not None else None

        if frame is None:
            time.sleep(0.1)
            continue

        H, W = frame.shape[:2]
        raw = []

        for off in (0, W - H):
            crop = frame[:, off:off + H]
            resized = cv2.resize(crop, (640, 640))
            rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)

            buf = Gst.Buffer.new_wrapped(rgb.tobytes())
            src.emit('push-buffer', buf)

            sample = sink.emit('pull-sample')
            if not sample:
                continue

            result_buf = sample.get_buffer()
            roi = hailo.get_roi_from_buffer(result_buf)
            detections = hailo.get_hailo_detections(roi)

            for det in detections:
                confidence = det.get_confidence()
                if det.get_label() == 'person':
                    print(f"[DET] person {confidence:.2f}", flush=True)
                if confidence < CONFIDENCE_THRESHOLD:
                    continue

                label = det.get_label()
                if label not in TARGET_CLASSES:
                    continue

                bbox = det.get_bbox()

                x1 = int(bbox.xmin() * H) + off
                y1 = int(bbox.ymin() * H)
                x2 = int(bbox.xmax() * H) + off
                y2 = int(bbox.ymax() * H)

                raw.append((label, confidence, (x1, y1, x2, y2)))

        raw.sort(key=lambda d: -d[1])
        current_dets = []
        for d in raw:
            if any(d[0] == k[0] and box_iou(d[2], k[2]) > 0.5 for k in current_dets):
                continue
            current_dets.append(d)

        for label, confidence, (x1, y1, x2, y2) in current_dets:
            x1 = max(0, x1)
            y1 = max(0, y1)
            x2 = min(W, x2)
            y2 = min(H, y2)
            trigger_packet(label, confidence, (x1, y1, x2-x1, y2-y1), frame)

        with det_lock:
            latest_detections = current_dets

        time.sleep(1)

# ---------------- STREAM SERVER ----------------

class StreamHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
        self.end_headers()

        while True:
            with frame_lock:
                frame = latest_frame

            if frame:
                try:
                    self.wfile.write(
                        b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' +
                        frame +
                        b'\r\n'
                    )
                except:
                    break

            time.sleep(0.01)

    def log_message(self, format, *args):
        pass

# ---------------- START ----------------

threading.Thread(target=stream_capture, daemon=True).start()
threading.Thread(target=inference_loop, daemon=True).start()

print(f"Night Owl streaming at http://0.0.0.0:{STREAM_PORT}")

HTTPServer(('0.0.0.0', STREAM_PORT), StreamHandler).serve_forever()
