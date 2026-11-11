# Real telemetry handoff

## Implemented but not hardware verified

MavlinkGPS reads GPS_RAW_INT on an independent configurable MAVLink telemetry connection in a background thread. It sends no flight/configuration commands and does not request message streams; the teammate must configure the controller to provide that message. Connection errors clear position and trigger a one-second reconnect loop. Silence expires a fix after --gps-max-age seconds (default 3).

Latitude and longitude are converted from degE7 to decimal degrees, and alt from millimeters to meters above mean sea level (MSL). This uses the raw GPS sensor position, not GLOBAL_POSITION_INT's filtered vehicle estimate. It does not claim to determine the object's position on the ground. If the team needs a fused aircraft position or home-relative altitude, agree and implement that explicitly instead of silently mixing references.

Accepted fix types are 3D, differential, RTK float/fixed and PPP (3,4,5,6,8). 2D, no-fix, static and unrecognized fix types do not supply a position. A 3D fix is not an accuracy guarantee; HDOP/accuracy thresholds and source selection need project-specific agreement and hardware testing.

System/component IDs default to 1/1 and are selectable; other sources are ignored. Confirm actual IDs on the aircraft. Age is based on local monotonic time at reception, not a synchronized sensor clock. Positive repeated sensor timestamps do not refresh a fix. Backward sensor time clears the fix until a subsequent advancing reading; zero/unknown sensor timestamps rely on arrival-time freshness. Source time_usec and received_at are logged. Message-buffer delay, GNSS integrity and absolute time synchronization remain limitations.

## Configuration example for the teammate

These are configuration placeholders, not a command to run now. The Pixhawk telemetry device must differ from the dedicated data radio. Stop and identify it before choosing paths; use /dev/serial/by-id when possible.

```bash
# Only after optional pymavlink has been installed in the Pi camera environment:
python -u Night_Owl_Live_Radio.py \
  --serial-port DATA_RADIO_DEVICE --baud 57600 \
  --gps mavlink --gps-device PIXHAWK_DEVICE --gps-baud CONFIRMED_BAUD \
  --gps-system-id CONFIRMED_SYSTEM --gps-component-id CONFIRMED_COMPONENT
```

requirements-gps.txt lists the optional MAVLink dependency; simulation and GPS-disabled modes do not require it. Install in a compatible virtual environment with internet or appropriate offline Linux/Pi wheels, not arbitrary Windows wheels. The development test used pymavlink 2.4.50. No Pixhawk connection, ArduPilot parameter changes, hardware stream-rate check or firmware validation was performed.

The adapter still neutralizes the old get_gps() stub, but now wraps packet creation with _gps.attach(...). The provider supplies both position and its provenance in that operation, before the radio queue. Therefore there is no need to edit ~/night_owl_final.py's hardcoded stub for this integration. --gps none overwrites any old stub coordinates with null.

## Packet extension

Existing position keys remain latitude, longitude, altitude_m. A new optional gps object records source (disabled/simulated/mavlink), simulated (boolean), status, age_s, received_at and altitude_reference. Raw GPS adds fix_type, sensor_time_usec and selected system/component IDs. An unavailable/stale/invalid fix has position=null. Legacy packets without this object are still accepted, with source shown as unspecified when coordinates exist.

New receivers reject inconsistent source/simulation markers or coordinates accompanied by an unavailable status. New GUI table coordinates show (SIM); details show SIMULATED TEST DATA; coordinate/altitude exports carry the gps object. Update both ends: an older GUI cannot provide those new labels. This provenance describes the selected source; it is not cryptographic authentication.

## Acceptance work with Pixhawk

- Confirm wiring, device, baud and source IDs independently of the data-radio connection.
- Confirm fresh GPS_RAW_INT messages arrive without changing flight behavior.
- Compare coordinates and MSL altitude with the controller's telemetry using the same reference.
- Check no fix, telemetry loss, stale/repeated timestamps and reconnection while detections/images continue.
- Confirm GPS snapshots correspond to detection creation, not eventual radio send time.
- Verify labels, filtering, thumbnails, coordinate exports and logs on physical hardware.
- Evaluate update rate/max age, accuracy, buffered data, timing and radio coexistence before flight.

## Primary protocol references

- MAVLink GPS_RAW_INT: https://mavlink.io/en/messages/common.html#GPS_RAW_INT
- MAVLink GLOBAL_POSITION_INT: https://mavlink.io/en/messages/common.html#GLOBAL_POSITION_INT
- Official pymavlink usage: https://mavlink.io/en/mavgen_python/
