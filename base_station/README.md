# Night Owl Base Station

A Python desktop interface for viewing detection data received
from the Night Owl drone.

## Features

- Display received detections.
- Filter detections by object type.
- View detection confidence, timestamps, and coordinates.
- Inspect detection details and thumbnail images.
- Widen the selected-detection sidebar by dragging the divider beside the table.

## Files

- gui.py: Main application interface.
- details_panel.py: Selected detection details and image preview.
- detection.py: Detection data handling.
- packet_receiver.py: Incoming detection packet handling.
- protocol.py: Shared JSON framing and strict encoding/decoding.
- radio.py: Dedicated JSON serial radio transport (requires pyserial).
- image_transfer.py: Compact JPEG compression, acknowledged chunks and image verification.
- mavlink_gps.py: USB Pixhawk GPS reader with independent telemetry requests.
- install_autostart.py: Install/update the Pi's boot-time systemd service.

## Running

Start either `python3 base_station/receiver.py` for the console receiver or
`python3 base_station/gui.py` for the GUI, then run
`python3 base_station/sender.py` in another terminal. Both receivers default to
TCP `127.0.0.1:5000`; run only one receiver at a time.

## Running over the dedicated SiK JSON radio

The Pi sends detections and the laptop GUI receives them. Run `sender.py` on
the Pi and `gui.py` on the laptop. The GUI already contains the receiver, so
there is no separate server process to start. QGroundControl is not required
for this JSON link.

Install pyserial on both machines (`sudo apt install python3-serial` on
Debian/Ubuntu/Raspberry Pi OS). The laptop also needs Tkinter
(`sudo apt install python3-tk` if missing). Thumbnail transfer and preview require
Pillow on both machines (`python3 -m pip install pillow` in the relevant venv,
or `sudo apt install python3-pil python3-pil.imagetk` for the laptop system Python).

Find the radio ports on each machine:

```bash
python3 base_station/gui.py --list-ports
```

On the Pi, after copying the files into `/home/nightowl/Documents/src`, use
`python3 sender.py --list-ports` from that directory. The Pi's port name is
independent of the laptop's port name. Prefer a `/dev/serial/by-id/...` path
when available because USB port numbers can change after reconnecting. A radio
wired to the Pi's UART pins may use `/dev/serial0` instead of a USB device.

Allow your Linux account to access serial ports if it is not already in the
`dialout` group:

```bash
sudo usermod -aG dialout "$USER"
```

Log out and back in to apply the new group membership. To grant access to just
one attached radio immediately instead, use the following if `setfacl` is
installed (this temporary permission must be renewed after reconnecting):

```bash
sudo setfacl -m u:"$USER":rw /dev/ttyUSB1
```

Start the laptop GUI, selecting the dedicated JSON radio:

```bash
python3 base_station/gui.py --serial-port /dev/serial/by-id/usb-FTDI_FT231X_USB_UART_DU0E3ZUN-if00-port0 --baud 115200
```

In another laptop terminal, copy the updated scripts and sample packet:

```bash
scp -6 base_station/*.py base_station/sample_packet.json 'nightowl@[fe80::8aa2:9eff:fec6:538e%wlp0s20f3]:/home/nightowl/Documents/src/'
ssh -6 nightowl@fe80::8aa2:9eff:fec6:538e%wlp0s20f3
```

Then, in the Pi's SSH shell, send one sample packet (replace `/dev/ttyUSB0`
with the Pi's actual JSON radio device):

```bash
cd /home/nightowl/Documents/src
python3 sender.py --serial-port /dev/ttyUSB0 --baud 115200
```

You should see an `accepted` reply on the Pi and a detection row in the laptop
GUI. This sender sends one sample packet per invocation; use the live camera
launcher below for real detections and automatic thumbnail transfer. The sample
sender sends only metadata, so its sample thumbnail filename must refer to an
existing laptop image to show a preview.

Use the actual serial baud rate configured for each local radio. The CLI
default is 57600; the commands above explicitly select 115200, which SiK calls
`115`. Baud rates have not been verified on the attached hardware. Radio
settings are not modified by these scripts.

The two radios in a pair must have matching network settings, including
`NETID`. Use a different `NETID` for the JSON pair than the flight telemetry
pair. Configure the dedicated JSON pair for transparent serial data
(`MAVLINK=0`) to avoid MAVLink framing/status data on the JSON stream. Keep
flight telemetry on its own pair. Selecting a USB port does not change its
wireless pairing. See the
[official SiK configuration reference](https://ardupilot.org/copter/docs/common-3dr-radio-advanced-configuration-and-technical-information.html).

## Testing the existing camera detector

All runtime Python code goes into `~/Documents/src` on the Pi: the live camera
launcher, `night_owl_final.py`, and the shared GUI/radio modules. The launcher
finds the camera source and transport modules beside its own file, regardless
of the shell's working directory. It adapts the detector in memory without
overwriting the camera source. The source uses `rpicam-vid`, GStreamer,
OpenCV, NumPy, Hailo bindings, Pillow, pymavlink, the model `/home/nightowl/yolov11m.hef`, and its
existing Hailo postprocessing plugin. Use the Pi's environment that already
supports that camera pipeline.

From the laptop's repository root, copy all runtime code and the sample packet
to the same Pi directory:

```bash
scp -6 ./*.py base_station/*.py base_station/sample_packet.json 'nightowl@[fe80::8aa2:9eff:fec6:538e%wlp0s20f3]:/home/nightowl/Documents/src/'
```

Restart the laptop GUI to load its support for detections without GPS:

```bash
python3 base_station/gui.py --serial-port /dev/ttyUSB1 --baud 115200
```

On the Pi, first check source adaptation and Python module discovery:

```bash
cd ~/Documents/src
python3 Night_Owl_Live_Radio.py --check
```

If dependencies are missing, switch to the environment used by the existing
camera detector and repeat the check. This check does not open a radio, run the
camera, or verify native GStreamer/Hailo plugin or accelerator operation.

When the check passes, launch the real pipeline, using the same Pi radio port
and baud rate that worked in the sample test:

```bash
python3 Night_Owl_Live_Radio.py --serial-port /dev/ttyUSB0 --baud 115200
```

Point the camera at a person in good light. The existing confidence threshold
is 0.85 and the cooldown is 10 seconds per object class, so a continuously
visible person produces at most one transmission every 10 seconds. Look for
`[DET] person ...`, `[RADIO] Queued ...`, and an `accepted` reply on the Pi, with
a matching new row in the laptop GUI. Ctrl+C stops the test.

The original detector contains hardcoded sample GPS coordinates. The launcher
replaces that function with live USB Pixhawk data. When GPS is disabled,
missing, stale or without a valid 3D fix, packets explicitly use
`position: null` and the laptop displays `Unavailable` for coordinates.
Original snapshots are saved on the Pi under `~/detections_radio/<session>/`.
By default the launcher also sends a compact JPEG thumbnail over the radio.
There is no live video display in the laptop GUI. A bounded queue and one radio
worker serialize outgoing detections and thumbnails. Detection metadata is
sent once; image chunks may be retried safely when their ACK is lost or a
chunk is rejected as damaged.

## Starting automatically when the Pi boots

Use `install_autostart.py` once on the Pi to update the existing
`nightowl.service`. All runtime code stays in `~/Documents/src`; systemd uses
the exact Python executable from the camera venv activated during installation.
The installer preserves that venv path instead of resolving its Python symlink
to the system interpreter. There is no venv activation or SSH login required
at boot.

Copy the installer from the laptop's repository root:

```bash
scp -6 base_station/install_autostart.py 'nightowl@[fe80::8aa2:9eff:fec6:538e%wlp0s20f3]:/home/nightowl/Documents/src/'
```

Stop any manually running Pi camera instance with Ctrl+C, then activate the
working camera venv in the Pi's SSH terminal. Run the installer as `nightowl`
with that venv's Python, using the same JSON radio and Pixhawk ports that
worked in the live test:

```bash
cd ~/Documents/src
python3 install_autostart.py --serial-port /dev/ttyUSB0 --gps-device /dev/serial/by-id/usb-Holybro_Pixhawk6C_38004B000251343131363638-if00
```

The installer checks the application dependencies, maps the selected devices
to stable `/dev/serial/by-id/` names, writes `nightowl.service` in `src`, and
verifies its syntax. It backs up the previous installed service to a
`nightowl-service-previous-*.bak` file in `src`, stops that service, installs the
new unit under `/etc/systemd/system/`, and enables and starts it. It invokes
sudo for those privileged steps; do not run the Python installer itself with
sudo. `--write-only` generates and verifies the unit without installing it.

The service uses available `dialout`, `video`, and `render` groups for hardware
access, retains any active GStreamer/library-path settings needed by the camera
environment, and sends output to the system journal. It does not depend on
Ethernet, internet access, or QGroundControl. Process failures restart after
5 seconds; startup retries also cover USB devices that appear late in boot.
Stopping or restarting the service terminates its capture subprocesses as a
group, releasing camera and accelerator resources. These settings supervise
the process; a live process with a stalled inference thread still needs
diagnosis in the logs.

Check status and watch output:

```bash
systemctl status nightowl.service --no-pager -l
sudo journalctl -u nightowl.service -f
```

After the initial setup, reboot to confirm startup:

```bash
sudo reboot
```

The Pi will start detection, GPS acquisition and thumbnail transmission on
boot. Start the laptop GUI to receive the detections. Future source-code
updates in `src` take effect after `sudo systemctl restart nightowl.service`.
Changing venv/device settings requires rerunning the installer with the desired
venv activated. To turn off automatic startup:

```bash
sudo systemctl disable --now nightowl.service
```

See the [systemd service reference](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html)
for restart and process lifecycle settings.

## Pixhawk GPS over USB

The launcher defaults to `--gps mavlink` and reads the Pixhawk through its own
USB connection on the Pi. QGroundControl is not required. GPS acquisition runs
in a separate thread from inference and image transfer, and coordinates are
copied into each detection when it is queued, not when the radio eventually
transmits it. These are the aircraft's coordinates, not a computed ground
location of the detected object.

Stop the old `nightowl.service` and any manually running camera instance before
testing to release the camera, accelerator and USB telemetry port. In the Pi's
working camera venv, install pymavlink:

```bash
sudo systemctl stop nightowl.service
cd ~/Documents/src
python3 -m pip install pymavlink
```

For a GPS-only test with QGroundControl closed, run:

```bash
python3 mavlink_gps.py --duration 20
```

The program automatically identifies a Pixhawk/Holybro/ArduPilot/ PX4 USB
device under `/dev/serial/by-id/`. It does not select an FTDI radio as the GPS
port. If identification is ambiguous or fails, list the devices and specify
the Pixhawk explicitly (use the actual filename shown on your Pi):

```bash
ls -l /dev/serial/by-id/
python3 mavlink_gps.py --device /dev/serial/by-id/usb-Holybro_Pixhawk6C_38004B000251343131363638-if00 --duration 20
```

`status: fix` and numeric `position` fields confirm GPS acquisition. Without a
3D fix, allow the GPS receiver to acquire satellites with a clear view of the
sky. `no_3d_fix`, `waiting_for_fix`, `stale` and `connection_error` identify the
current issue; satellite count and fix type are included in the diagnostic
output. The GPS test closes its connection when it finishes.

Then start the full camera, thumbnail and GPS pipeline:

```bash
python3 Night_Owl_Live_Radio.py --check
python3 Night_Owl_Live_Radio.py --serial-port /dev/ttyUSB0 --baud 115200
```

Use `--gps-device <Pixhawk USB path>` on the launcher if auto-detection needs
an explicit port. `--gps-baud` defaults to 115200, separately from the JSON
radio baud rate. `--gps none` keeps the camera/radio pipeline usable without a
Pixhawk or pymavlink. All runtime code remains in `~/Documents/src`.

On receiving the autopilot heartbeat, the reader requests `GPS_RAW_INT`,
`GLOBAL_POSITION_INT` and `EKF_STATUS_REPORT` at 2 Hz with
`MAV_CMD_SET_MESSAGE_INTERVAL`. It periodically renews its requests, with a
legacy stream-request fallback for firmware that rejects that command. Only
the selected autopilot's messages are used; data from other systems/components
is ignored. USB errors or heartbeat loss clear cached positions, and the
reader retries the USB connection.

A fresh 3D-or-better raw GPS fix is required. Filtered global position is used
only when fresh estimator flags confirm valid absolute horizontal and vertical
position; otherwise the raw GPS position is used. An explicit GPS-glitch report
makes position unavailable. Samples older than 5 seconds are unavailable by
default (`--gps-max-age` changes that threshold). Repeated sensor timestamps
do not refresh the cached sample's age.

Latitude/longitude are converted from degrees times 10^7. Altitude is converted
from millimetres and marked `AMSL` (above mean sea level), which is also shown
in the GUI and preserved in exports. It is not height above the local ground.
The reader sends heartbeats and telemetry-rate requests; it does not arm the
vehicle, change flight mode, or write persistent flight parameters. See the
[ArduPilot telemetry request documentation](https://ardupilot.org/dev/docs/mavlink-requesting-data.html)
and [MAVLink message definitions](https://mavlink.io/en/messages/common.html).

## Receiving detection thumbnails

The live camera launcher enables images by default; use `--no-images` to send
only detection metadata. Update both the laptop GUI and the Pi launcher/shared
modules, then restart both programs. Run the Pi launcher in the working camera
venv and install Pillow there if needed:

```bash
python3 -m pip install pillow
python3 Night_Owl_Live_Radio.py --check
python3 Night_Owl_Live_Radio.py --serial-port /dev/ttyUSB0 --baud 115200
```

For each detection, the launcher reads the saved snapshot, marks the detected
object with a box and label, and creates an image up to 320 pixels on its
longest side. JPEG quality and dimensions are reduced as needed to target a
maximum payload of 12,000 bytes. The original full-resolution photo stays on
the Pi.

The detection row appears before the image. Select it in the laptop GUI; the
details panel refreshes automatically when its thumbnail is complete. Verified
images are saved under `base_station/received_images/` on the laptop. Exporting
a detection with Image selected then includes that received thumbnail. The Pi
prints `[IMAGE] Sending thumbnail ...` and `[IMAGE] Thumbnail received ...`.

Thumbnail transfers take additional time depending on the radio settings and
link quality. They share the same radio worker as metadata, so later queued
detections can wait while an image is being sent. This feature is intended for
compact still images, not video.

Detection metadata includes an optional `image` manifest identifying the image
transfer, byte size, JPEG format, SHA-256 checksum, and raw chunk size. New
transfers default to 192-byte chunks; receivers accept chunk sizes from 48 to
384 bytes and use the size advertised in the manifest.
Each `image_chunk` message carries base64 data, a sequence number and CRC32;
the receiver acknowledges it before the sender proceeds. An `image_end`
message commits the image only after size/checksum and JPEG validation.
Accepted duplicate chunks and duplicate completion messages are idempotent.
Chunk ACK timeouts and recoverable size, base64, checksum or sequence errors
allow at most three sends of that chunk. The receiver preserves verified bytes
while requesting a retransmission. Error messages identify the chunk number
and distinguish length errors from CRC mismatches. Fatal errors still stop the
image immediately. After a failed transfer the sender attempts an `image_abort`
message to release partial state; an abort never removes an already verified
image. An image has a 120-second overall transfer deadline.
Incoming images are limited to 32,768 bytes and 640
pixels per side, with bounded pending state and temporary-file cleanup. A
failed image transfer leaves the detection metadata available in the GUI.
Update and restart both ends together when upgrading image transfer support.

## Packet protocol

Over TCP and serial radio, requests and responses are UTF-8, newline-delimited
JSON: serialize each message on one line and append a newline (`\n`). Literal
newlines inside JSON strings
must be escaped by the JSON encoder. External/drone senders must also append
this delimiter; unframed messages and pretty-printed multiline JSON are not
supported on the wire. The sample file may still be pretty-printed because the
sender serializes it before transmission.

Messages may arrive across any number of reads. Multiple messages can share
a connection or serial stream, and each accepted request receives its own
response. Messages are limited to 1,000,000 bytes excluding the newline. Connections close after
5 seconds without incoming data on TCP; incomplete messages are rejected. Invalid
JSON, invalid UTF-8, oversized messages and invalid detection fields receive a
rejection when the connection still permits a reply, then the TCP connection closes.
Serial receivers keep partial frames across idle reads, wait for new detections
indefinitely, and resume at the next newline after malformed or oversized frames.
Serial senders wait up to 10 seconds for a complete reply. The SiK link has a
much lower throughput than TCP; keep detection metadata small and wait for each
acknowledgement before sending another packet.

Numbers must be finite; `NaN`, `Infinity`, and overflowing floating-point numbers
are rejected. Packet IDs must be integers, timestamps must be supported Unix
times in seconds, and object types must be nonempty strings. Confidence must be
between 0 and 1, latitude between -90 and 90, and longitude between -180 and 180.
Boolean values are not accepted as numbers. Altitude, bounding box and thumbnail
are optional; altitude must be numeric or null and thumbnail must be a
string or null. `position` must be present, but may be `null` to explicitly mark
GPS as unavailable. If it is an object, valid latitude and longitude are both
required; partially missing coordinates remain invalid.

## Tests

Run `python3 -B -m unittest discover -s base_station/tests -v` from the repository
root. Tests use `unittest` and Pillow. The pyserial loopback and real MAVLink
encoding/decoding tests run when their packages are installed; no test requires
a physical radio, Pixhawk or camera.
