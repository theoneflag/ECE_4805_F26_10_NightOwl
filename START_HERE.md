# Night Owl GPS handling test

Updated November 9, 2026. The Pi and Pixhawk are NOT required for the first Windows test. This is a new test copy; keep the working image-only folders intact.

## First test entirely on Windows

1. Extract this ZIP to Downloads. Open PowerShell in the folder containing gui.py, normally:

```powershell
cd "C:\Users\theon\Downloads\Night_Owl_GPS_Test\base_station"
py -m pip install -r requirements.txt
py -m unittest -v test_validation test_serial_link test_radio_images test_gps
py gui.py
```

Close the older GUI first, because this GUI needs its own TCP port 5000. No COM radio or camera is used in this first test.

2. Open a second PowerShell window in that SAME base_station folder:

```powershell
cd "C:\Users\theon\Downloads\Night_Owl_GPS_Test\base_station"
py sender.py --packet sample_packet_no_gps.json --simulate --count 24 --interval 2 --start-id 8000 --gps simulate
```

This runs about 46 seconds. GPS modes cycle every 45 seconds from sender startup:

| Elapsed time | Expected GPS status |
| --- | --- |
| 0-10 s | valid, simulated coordinates |
| 10-15 s | no_fix, position unavailable |
| 15-20 s | recovered simulated fix |
| 20-30 s | stale, position unavailable |
| 30-35 s | disconnected, position unavailable |
| 35-45 s | recovered simulated fix |

Select rows from different phases. Valid coordinates have `(SIM)` in the table/details. The details panel always shows **SIMULATED TEST DATA** for these rows, even when location is unavailable or the GPS checkbox is hidden. Export with GPS location or Altitude checked: detection.json includes a gps object preserving simulated=true, status, age and MSL altitude reference. These are invented test readings, not your actual location. Missing images are expected in this camera-free test; uncheck Image for export.

Filtering, details checkboxes and export remain. GPS freshness is evaluated when the detection packet is created; a historical row retains its recorded status and does not turn stale just because it is viewed later. Logs persist on disk; the GUI does not reload prior rows automatically.

## Then optionally reconnect the Pi for camera and radio

See [GPS_SETUP.md](GPS_SETUP.md) for exact power, Ethernet, SSH, copy, receiver and launcher commands. Use the new code on BOTH ends to see simulation labels. The previous ~/radio_image_v2 install remains a rollback copy.

## What changed

- gps_provider.py: disabled, explicitly simulated and optional MAVLink GPS sources; cached nonblocking snapshots, freshness and telemetry reconnection.
- Night_Owl_Live_Radio.py: attaches GPS once when constructing a camera detection packet, before the radio queue. Default --gps none keeps location unavailable. The original camera source is adapted in memory, not overwritten.
- sender.py: optional --gps simulate / none / mavlink for camera-free testing. Without --gps, legacy fixture behavior is retained; sample_packet.json contains illustrative coordinates and is not live telemetry. Use sample_packet_no_gps.json in this guide.
- receiver.py: validates optional GPS provenance/status so malformed new metadata cannot silently lose its simulation marker.
- detection.py / details_panel.py: show simulation status, label coordinates, preserve GPS provenance when exporting coordinates or altitude. Older packets without GPS metadata remain supported.
- test_gps.py: GPS lifecycle, units, serialization, camera adaptation and image/serial regressions.

## Validation and boundaries

The development run discovered 22 tests: 21 passed, one existing OpenCV compression test skipped because OpenCV is absent in this review runtime. The optional actual pymavlink message encode/decode test and the real background reader over localhost UDP DID run with pymavlink 2.4.50. On a Windows environment without pymavlink, those two optional tests also skip; no optional install is required for simulation.

An additional actual sender CLI -> TCP receiver -> JSONL log -> GUI queue -> coordinate export smoke check passed. Tests use a representative camera-source fixture, not the actual Pi-only camera file; --check on the Pi still matters.

The new code has not yet been tested on your physical Pi/radios/camera. Existing radio JPEG handling is retained; range, flight, full accuracy, GPS hardware and coexistence remain unverified. See [GPS_HANDOFF.md](GPS_HANDOFF.md) before real telemetry integration.

## Timing correction

The test sender now waits for the requested interval before generating each simulated detection timestamp. This removes the artificial interval-sized gap between the packet timestamp and its simulated GPS snapshot. The Pi live camera path is unchanged by this correction.
