"""Pixhawk USB telemetry without a ground station; only message-rate requests."""

import argparse
import json
import math
from pathlib import Path
import threading
import time

DEFAULT_MAX_AGE = 5.0
HEARTBEAT_TIMEOUT = 10.0
MESSAGE_IDS = (24, 33, 193)  # GPS_RAW_INT, GLOBAL_POSITION_INT, EKF_STATUS_REPORT
SET_MESSAGE_INTERVAL = 511
EKF_POSITION_VALID = 16 | 32  # Absolute horizontal and vertical position are good.
EKF_POSITION_INVALID = 128 | 1024  # Constant-position mode or uninitialized.
EKF_GPS_GLITCHING = 32768


def find_pixhawk(device="auto", directory=Path("/dev/serial/by-id")):
    if device != "auto":
        path = Path(device).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"Pixhawk USB device not found: {path}")
        return str(path)
    matches = sorted(path for path in directory.glob("*") if path.exists() and
                     any(name in path.name.lower() for name in ("pixhawk", "holybro", "ardupilot", "px4")))
    # A device may have several by-id aliases; count distinct serial endpoints.
    unique = {str(path.resolve()): path for path in matches}
    if len(unique) != 1:
        raise OSError("Could not identify one Pixhawk USB port; set --gps-device to its /dev/serial/by-id/... path")
    return str(next(iter(unique.values())))


def _coordinates(message):
    lat, lon, alt = (getattr(message, name, None) for name in ("lat", "lon", "alt"))
    if any(type(value) is not int for value in (lat, lon, alt)):
        return None
    if not -900_000_000 <= lat <= 900_000_000 or not -1_800_000_000 <= lon <= 1_800_000_000:
        return None
    if not -(2 ** 31) <= alt < 2 ** 31:
        return None
    return {"latitude": lat / 1e7, "longitude": lon / 1e7,
            "altitude_m": alt / 1000.0, "altitude_reference": "AMSL"}


class GPSState:
    """Thread-safe samples; freshness is measured locally with monotonic time."""
    def __init__(self, max_age=DEFAULT_MAX_AGE, clock=time.monotonic):
        if not math.isfinite(max_age) or max_age <= 0:
            raise ValueError("GPS maximum age must be positive and finite")
        self.max_age, self.clock = max_age, clock
        self.lock = threading.Lock()
        self.reset()

    def reset(self, status="waiting_for_heartbeat", error=None):
        with self.lock:
            self.target = None
            self.heartbeat_at = None
            self.raw = self.filtered = None
            self.raw_at = self.filtered_at = None
            self.raw_stamp = self.filtered_stamp = None
            self.fix_type = None
            self.satellites = None
            self.ekf_flags = self.ekf_at = None
            self.status, self.error = status, error

    def update(self, message):
        kind = message.get_type()
        source = (message.get_srcSystem(), message.get_srcComponent())
        now = self.clock()
        with self.lock:
            if kind == "HEARTBEAT":
                # Ignore ground stations, cameras and other components.
                if source[1] != 1 or getattr(message, "autopilot", 8) == 8:
                    return
                if self.target is not None and source != self.target:
                    return
                self.target = source
                self.heartbeat_at = now
                self.error = None
                return
            if self.target is None or source != self.target:
                return
            if kind == "GPS_RAW_INT":
                fix = getattr(message, "fix_type", None)
                self.fix_type = fix if type(fix) is int and 0 <= fix <= 8 else None
                satellites = getattr(message, "satellites_visible", None)
                self.satellites = satellites if type(satellites) is int and 0 <= satellites < 255 else None
                if self.fix_type is None or self.fix_type < 3:
                    self.raw = self.raw_at = self.raw_stamp = None
                    self.filtered = self.filtered_at = self.filtered_stamp = None
                    return
                position = _coordinates(message)
                if position is None:
                    self.raw = self.raw_at = None
                    return
                stamp = getattr(message, "time_usec", 0)
                if type(stamp) is int and stamp > 0 and stamp == self.raw_stamp:
                    return  # Repeated cached sensor data must not stay fresh forever.
                self.raw, self.raw_at, self.raw_stamp = position, now, stamp
            elif kind == "GLOBAL_POSITION_INT":
                position = _coordinates(message)
                if position is None:
                    self.filtered = self.filtered_at = None
                    return
                stamp = getattr(message, "time_boot_ms", 0)
                if type(stamp) is int and stamp > 0 and stamp == self.filtered_stamp:
                    return
                self.filtered, self.filtered_at, self.filtered_stamp = position, now, stamp
            elif kind == "EKF_STATUS_REPORT":
                flags = getattr(message, "flags", None)
                self.ekf_flags = flags if type(flags) is int and 0 <= flags <= 65535 else None
                self.ekf_at = now

    def snapshot(self):
        now = self.clock()
        with self.lock:
            position, source = None, None
            age = None if self.raw_at is None else max(0, now - self.raw_at)
            heartbeat_age = None if self.heartbeat_at is None else max(0, now - self.heartbeat_at)
            fresh_ekf = self.ekf_flags is not None and now - self.ekf_at < self.max_age
            if self.target is None:
                status = self.status
            elif heartbeat_age is None or heartbeat_age >= HEARTBEAT_TIMEOUT:
                status = "disconnected"
            elif self.fix_type is None:
                status = "waiting_for_fix"
            elif self.fix_type < 3:
                status = "no_3d_fix"
            elif self.raw is None:
                status = "invalid_position"
            elif age >= self.max_age:
                status = "stale"
            elif fresh_ekf and self.ekf_flags & EKF_GPS_GLITCHING:
                status = "gps_glitch"
            else:
                status = "fix"
                filtered_valid = (fresh_ekf and self.ekf_flags & EKF_POSITION_VALID == EKF_POSITION_VALID
                                  and not self.ekf_flags & EKF_POSITION_INVALID)
                if filtered_valid and self.filtered is not None and now - self.filtered_at < self.max_age:
                    position, source = dict(self.filtered), "GLOBAL_POSITION_INT"
                else:
                    position, source = dict(self.raw), "GPS_RAW_INT"
            return {"position": position, "gps": {
                "status": status, "fix_type": self.fix_type, "satellites_visible": self.satellites,
                "age_s": None if age is None else round(age, 2), "source": source,
                "heartbeat_age_s": None if heartbeat_age is None else round(heartbeat_age, 2),
                "ekf_flags": self.ekf_flags,
                "error": self.error,
            }}


def request_position_messages(connection, target, rate=2):
    interval_us = int(1_000_000 / rate)
    for message_id in MESSAGE_IDS:
        connection.mav.command_long_send(*target, SET_MESSAGE_INTERVAL, 0,
                                         message_id, interval_us, 0, 0, 0, 0, 0)


def request_legacy_streams(connection, target, rate=2):
    # ArduPilot puts GPS_RAW_INT in EXTENDED_STATUS and filtered position in POSITION.
    for stream_id in (2, 6):
        connection.mav.request_data_stream_send(*target, stream_id, rate, 1)


def _open_connection(device, baud):
    try:
        from pymavlink import mavutil
    except ImportError as error:
        raise ImportError("Install pymavlink in the camera venv: python3 -m pip install pymavlink") from error
    return mavutil.mavlink_connection(device, baud=baud, source_system=245,
                                      source_component=191, autoreconnect=False, dialect="ardupilotmega")


class MavlinkGPS:
    def __init__(self, device="auto", baud=115200, max_age=DEFAULT_MAX_AGE,
                 connection_factory=_open_connection, device_finder=find_pixhawk):
        if type(baud) is not int or baud <= 0:
            raise ValueError("GPS baud rate must be positive")
        self.device, self.baud = device, baud
        self.state = GPSState(max_age)
        self.factory, self.finder = connection_factory, device_finder
        self.stop = threading.Event()
        self.connection = None
        self.last_report = None
        self.worker = threading.Thread(target=self._run, name="pixhawk-gps", daemon=True)
        self.worker.start()

    def _report(self):
        sample = self.snapshot()
        gps = sample["gps"]
        key = gps["status"], gps["error"]
        if key != self.last_report:
            self.last_report = key
            detail = gps["error"] or sample["position"] or f"satellites={gps['satellites_visible']}"
            print(f"[GPS] {gps['status']}: {detail}", flush=True)

    def _run(self):
        while not self.stop.is_set():
            connection = None
            try:
                device = self.finder(self.device)
                connection = self.factory(device, self.baud)
                self.connection = connection
                self.state.reset()
                started = time.monotonic()
                last_heartbeat = last_request = float("-inf")
                legacy = False
                while not self.stop.is_set():
                    now = time.monotonic()
                    if now - last_heartbeat >= 1:
                        # ONBOARD_CONTROLLER / AUTOPILOT_INVALID / ACTIVE.
                        connection.mav.heartbeat_send(18, 8, 0, 0, 4)
                        last_heartbeat = now
                    message = connection.recv_match(blocking=True, timeout=0.2)
                    if message is not None:
                        self.state.update(message)
                        source = (message.get_srcSystem(), message.get_srcComponent())
                        if source == self.state.target and message.get_type() == "COMMAND_ACK":
                            if getattr(message, "command", None) == SET_MESSAGE_INTERVAL and getattr(message, "result", None) == 3:
                                legacy = True
                                last_request = float("-inf")
                    target = self.state.target
                    now = time.monotonic()
                    if target is not None and now - last_request >= 10:
                        if legacy:
                            request_legacy_streams(connection, target)
                        else:
                            request_position_messages(connection, target)
                        last_request = now
                    sample = self.snapshot()
                    heartbeat_age = sample["gps"]["heartbeat_age_s"]
                    if (target is None and now - started >= HEARTBEAT_TIMEOUT) or (
                        heartbeat_age is not None and heartbeat_age >= HEARTBEAT_TIMEOUT
                    ):
                        raise TimeoutError("Pixhawk heartbeat timed out")
                    self._report()
            except ImportError as error:
                self.state.reset("dependency_missing", str(error))
                self._report()
                return
            except (OSError, ValueError, RuntimeError) as error:
                self.state.reset("connection_error", str(error))
                self._report()
            finally:
                self.connection = None
                if connection is not None:
                    try:
                        connection.close()
                    except OSError:
                        pass
            self.stop.wait(2)

    def snapshot(self):
        return self.state.snapshot()

    def position(self):
        return self.snapshot()["position"]

    def close(self):
        self.stop.set()
        self.worker.join(timeout=3)
        self.state.reset("stopped")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read Pixhawk GPS over USB without starting the camera or radio")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--duration", type=float, default=20)
    args = parser.parse_args(argv)
    if args.baud <= 0 or not math.isfinite(args.duration) or args.duration <= 0:
        parser.error("baud and duration must be positive and finite")
    reader = MavlinkGPS(args.device, args.baud)
    fixed = False
    try:
        deadline = time.monotonic() + args.duration
        while time.monotonic() < deadline:
            sample = reader.snapshot()
            print(json.dumps(sample, allow_nan=False), flush=True)
            fixed = fixed or sample["position"] is not None
            if reader.stop.wait(1):
                break
    except KeyboardInterrupt:
        pass
    finally:
        reader.close()
    return 0 if fixed else 1


if __name__ == "__main__":
    raise SystemExit(main())
