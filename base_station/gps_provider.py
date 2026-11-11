"""Nonblocking detection-time GPS snapshots; simulation is always explicit."""
import copy
import math
from pathlib import Path
import threading
import time

STATUSES = {'disabled', 'waiting', 'valid', 'no_fix', 'stale', 'disconnected', 'invalid'}
SOURCES = {'disabled', 'simulated', 'mavlink'}


def positive_seconds(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError('GPS max age must be a finite positive number')
    return value


def position_values(position):
    if not isinstance(position, dict):
        raise ValueError('GPS position must be an object')
    result = {}
    for key in ('latitude', 'longitude', 'altitude_m'):
        value = position.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f'GPS {key} must be numeric')
        if not math.isfinite(value):
            raise ValueError(f'GPS {key} must be finite')
        result[key] = value
    if not -90 <= result['latitude'] <= 90 or not -180 <= result['longitude'] <= 180:
        raise ValueError('GPS coordinate outside range')
    return result


def validate_gps_metadata(packet):
    """Optional schema extension. Old packets without gps remain supported."""
    if 'gps' not in packet:
        return
    meta = packet['gps']
    if not isinstance(meta, dict):
        raise ValueError('gps must be an object')
    if meta.get('source') not in SOURCES or meta.get('status') not in STATUSES:
        raise ValueError('Invalid GPS source or status')
    if type(meta.get('simulated')) is not bool:
        raise ValueError('gps.simulated must be boolean')
    if meta['simulated'] != (meta['source'] == 'simulated'):
        raise ValueError('GPS simulation marker does not match source')
    for name in ('age_s', 'received_at'):
        value = meta.get(name)
        if value is not None:
            try:
                valid = (not isinstance(value, bool) and isinstance(value, (int, float))
                         and math.isfinite(value) and value >= 0)
            except OverflowError:
                valid = False
            if not valid:
                raise ValueError(f'gps.{name} must be null or finite and nonnegative')
    if meta.get('altitude_reference') not in (None, 'MSL'):
        raise ValueError('This GPS integration uses MSL altitude in meters')
    if meta['status'] == 'valid':
        position_values(packet.get('position'))
        if meta.get('age_s') is None or meta.get('received_at') is None:
            raise ValueError('Valid GPS requires age and receive time')
        if meta['source'] == 'disabled' or meta.get('altitude_reference') != 'MSL':
            raise ValueError('Valid GPS requires an active source and altitude reference')
    elif packet.get('position') is not None:
        raise ValueError('Unavailable GPS must have position null')


class GPSCache:
    def __init__(self, source='mavlink', max_age=3, clock=time.monotonic, wall=time.time):
        if source not in SOURCES:
            raise ValueError('Unknown GPS source')
        self.source, self.max_age = source, positive_seconds(max_age)
        self.clock, self.wall = clock, wall
        self.lock = threading.Lock()
        self._position = None
        self._received = self._received_wall = None
        self._status = 'disabled' if source == 'disabled' else 'waiting'
        self._extra = {}

    def update(self, position, **extra):
        try:
            position = position_values(position)
        except (ValueError, OverflowError):
            self.invalidate('invalid')
            return False
        with self.lock:
            self._position = position
            self._received, self._received_wall = self.clock(), self.wall()
            self._status, self._extra = 'valid', dict(extra)
        return True

    def invalidate(self, status):
        if status not in STATUSES - {'valid', 'stale'}:
            raise ValueError('Invalid unavailable GPS status')
        with self.lock:
            self._status = status
            self._position = None
            self._received = self._received_wall = None
            self._extra = {}

    def snapshot(self):
        with self.lock:
            age = None if self._received is None else max(0, self.clock() - self._received)
            status = self._status
            if status == 'valid' and age > self.max_age:
                status = 'stale'
            meta = {'source': self.source, 'simulated': self.source == 'simulated',
                    'status': status, 'age_s': None if age is None else round(age, 3),
                    'received_at': self._received_wall,
                    'altitude_reference': None if self.source == 'disabled' else 'MSL'}
            meta.update(self._extra)
            return {'position': copy.deepcopy(self._position) if status == 'valid' else None,
                    'gps': meta}

    def attach(self, packet):
        outgoing = dict(packet)
        outgoing.update(self.snapshot())
        return outgoing

    def start(self):
        return self

    def close(self):
        pass


class SimulatedGPS(GPSCache):
    """Repeats a 45s test scenario. Coordinates never represent a real fix."""
    def __init__(self, max_age=3, clock=time.monotonic, wall=time.time):
        super().__init__('simulated', max_age, clock, wall)
        self.started = clock()

    def snapshot(self):
        elapsed = max(0, self.clock() - self.started) % 45
        if 10 <= elapsed < 15:
            self.invalidate('no_fix')
        elif 20 <= elapsed < 30:
            # A fixed old reading tests freshness, independent of call frequency.
            self.update({'latitude': 37.2211, 'longitude': -80.4234, 'altitude_m': 24.8})
            with self.lock:
                self._received -= self.max_age + 1
                self._received_wall -= self.max_age + 1
        elif 30 <= elapsed < 35:
            self.invalidate('disconnected')
        else:
            self.update({'latitude': round(37.2211 + elapsed * .000001, 7),
                         'longitude': -80.4234, 'altitude_m': 24.8}, fix_type=3)
        return super().snapshot()


class MavlinkGPS(GPSCache):
    """Read GPS_RAW_INT on a dedicated telemetry connection; send no commands."""
    def __init__(self, device, baud=57600, max_age=3, system_id=1, component_id=1,
                 connection_factory=None, clock=time.monotonic, wall=time.time):
        super().__init__('mavlink', max_age, clock, wall)
        if not device or baud <= 0 or not 1 <= system_id <= 255 or not 1 <= component_id <= 255:
            raise ValueError('GPS device, positive baud and system/component IDs 1..255 required')
        self.device, self.baud = device, baud
        self.system_id, self.component_id = system_id, component_id
        self.connection_factory = connection_factory
        self.stop = threading.Event()
        self.thread = None
        self.last_sensor_time = None

    def ingest(self, message):
        if message.get_type() != 'GPS_RAW_INT':
            return
        if (message.get_srcSystem(), message.get_srcComponent()) != (self.system_id, self.component_id):
            return
        fix = message.fix_type
        if type(fix) is not int or fix not in (3, 4, 5, 6, 8):
            self.invalidate('no_fix')
            return
        raw = [message.lat, message.lon, message.alt, message.time_usec]
        if any(type(v) is not int for v in raw) or not 0 <= message.time_usec < 2**64:
            self.invalidate('invalid')
            return
        if any(not -(2**31) <= v < 2**31 - 1 for v in raw[:3]):
            self.invalidate('invalid')
            return
        stamp = message.time_usec
        # Repeated sensor timestamps do not make a frozen fix look fresh.
        if stamp and self.last_sensor_time is not None:
            if stamp == self.last_sensor_time:
                return
            if stamp < self.last_sensor_time:
                self.last_sensor_time = stamp
                self.invalidate('waiting')
                return
        self.last_sensor_time = stamp or None
        self.update({'latitude': message.lat / 1e7, 'longitude': message.lon / 1e7,
                     'altitude_m': message.alt / 1000.0}, fix_type=fix,
                    sensor_time_usec=stamp, system_id=self.system_id, component_id=self.component_id)

    def start(self):
        if self.connection_factory is None:
            from pymavlink import mavutil
            self.connection_factory = mavutil.mavlink_connection
        if self.thread is not None:
            raise RuntimeError('GPS reader already started')
        self.thread = threading.Thread(target=self._run, daemon=True, name='nightowl-gps')
        self.thread.start()
        return self

    def _run(self):
        while not self.stop.is_set():
            connection = None
            try:
                connection = self.connection_factory(self.device, baud=self.baud,
                                                     autoreconnect=False)
                self.last_sensor_time = None
                self.invalidate('waiting')
                while not self.stop.is_set():
                    message = connection.recv_match(type='GPS_RAW_INT', blocking=True, timeout=.25)
                    if message is not None:
                        self.ingest(message)
            except Exception as error:
                # A broken telemetry link must not kill the detection pipeline.
                self.invalidate('disconnected')
                print(f'[GPS] Telemetry unavailable: {error}; retrying', flush=True)
            finally:
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        pass
            self.stop.wait(1)

    def close(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
        self.invalidate('disconnected')


def add_gps_arguments(parser, default='none'):
    parser.add_argument('--gps', choices=('none', 'simulate', 'mavlink'), default=default,
                        help='GPS source; simulation is explicitly marked as test data')
    parser.add_argument('--gps-max-age', type=float, default=3)
    parser.add_argument('--gps-device', help='Independent Pixhawk telemetry device (not data radio)')
    parser.add_argument('--gps-baud', type=int, default=57600)
    parser.add_argument('--gps-system-id', type=int, default=1)
    parser.add_argument('--gps-component-id', type=int, default=1)


def make_provider(args, data_radio=None):
    age = positive_seconds(args.gps_max_age)
    if args.gps == 'simulate':
        return SimulatedGPS(age)
    if args.gps == 'none':
        return GPSCache('disabled', age)
    if args.gps == 'mavlink':
        if not args.gps_device:
            raise ValueError('--gps-device is required for MAVLink GPS')
        if data_radio and (args.gps_device.casefold() == data_radio.casefold() or
                Path(args.gps_device).resolve() == Path(data_radio).resolve()):
            raise ValueError('Pixhawk GPS telemetry must use a different device from the data radio')
        return MavlinkGPS(args.gps_device, args.gps_baud, age,
                          args.gps_system_id, args.gps_component_id)
    raise ValueError('Unknown GPS mode')
