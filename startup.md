# Night Owl laptop startup

These commands run the GUI on an Ubuntu/Debian Linux laptop. The Pi runs the
camera, GPS reader, and transmitter. If `nightowl.service` is enabled on the
Pi, power it on; no SSH session is needed for normal operation.

## Start the GUI

Plug the laptop's **dedicated JSON radio** into USB, then open a terminal.
Replace `/path/to/night_owl` with the folder containing this project:

```bash
cd /path/to/night_owl
/usr/bin/python3 base_station/gui.py --list-ports
ls -l /dev/serial/by-id/
```

Choose the radio paired with the Pi's JSON radio. Replace `YOUR_RADIO` below
with its actual by-id filename. Set the baud rate to match the Pi's transmitter
and the radio's serial configuration; `115200` is an example:

```bash
JSON_RADIO="/dev/serial/by-id/YOUR_RADIO"
RADIO_BAUD=115200
/usr/bin/python3 base_station/gui.py --serial-port "$JSON_RADIO" --baud "$RADIO_BAUD"
```

If multiple radios are attached, identify the dedicated JSON radio before
launching. `/dev/ttyUSB0` and `/dev/ttyUSB1` can change after reconnecting;
prefer a by-id path when available. If your adapter has no by-id path, set
`JSON_RADIO` to the device reported by `--list-ports`, for example:

```bash
JSON_RADIO="/dev/ttyUSB0"
/usr/bin/python3 base_station/gui.py --serial-port "$JSON_RADIO" --baud "$RADIO_BAUD"
```

## First-time laptop setup

Install the GUI, radio, and image dependencies for the system Python used above:

```bash
sudo apt update
sudo apt install python3-tk python3-serial python3-pil python3-pil.imagetk acl
```

Give your account serial-port access:

```bash
sudo usermod -aG dialout "$USER"
```

Log out and log back in after changing group membership. Then run the GUI
startup command again.

For immediate, temporary access to the currently attached JSON radio, use the
same terminal where you set `JSON_RADIO`:

```bash
sudo setfacl -m u:"$USER":rw "$JSON_RADIO"
```

Temporary device permissions must be applied again after unplugging the radio
or rebooting.

## Confirm reception

- The GUI status should show the selected radio and baud rate.
- A valid camera detection adds a row. Detections depend on the confidence
  threshold and cooldown configured on the Pi.
- Select a row to inspect it. Its thumbnail appears after the radio transfer
  completes; received images are saved in `base_station/received_images/`.
- Coordinates display when the Pixhawk has a fresh valid GPS fix. Otherwise
  they display `Unavailable`.
- Drag the divider beside the table to resize the selected-detection sidebar.

## Stop or restart

Press **Ctrl+C** in the GUI terminal, or close its window. To restart, run the
GUI startup command again. In a new terminal, repeat the project directory and
radio settings commands first. Run one GUI instance per JSON radio.

## Common startup problems

- **Permission denied:** apply the serial-access steps above.
- **Device not found:** reconnect the radio and check `--list-ports` and
  `/dev/serial/by-id/` for its actual path.
- **Port already in use:** close another GUI or serial program using that radio.
- **Listening but no detections:** confirm the Pi's camera service is running,
  both radios are paired for the JSON link, and the baud rate matches the
  working setup. Keep the camera pointed at a target in good light.
