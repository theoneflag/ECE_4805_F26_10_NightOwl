# Night Owl Base Station with GPS handling

Read [START_HERE.md](../START_HERE.md) first. Test GPS on Windows without hardware, then optionally use [GPS_SETUP.md](../GPS_SETUP.md) for the Pi and radios.

```powershell
py -m pip install -r requirements.txt
py -m unittest -v test_validation test_serial_link test_radio_images test_gps
py gui.py
```

The optional real telemetry reader requires requirements-gps.txt and a separate confirmed Pixhawk connection. It is implemented and software-tested, not hardware-verified. GPS defaults to disabled for the live launcher; --gps simulate creates visibly labeled test data. Camera, JPEG previews, filtering, selectable details, logging and selected ZIP export remain.

The original Pi camera source and model/system camera dependencies are installed separately and are not included. Do not run receiver.py alongside the GUI. Keep generated logs and images private; the table does not automatically replay prior sessions.
