# Reconnect the Pi for the GPS simulation bench

This is optional after the Windows-only test. No Pixhawk is needed. You are testing real camera detections and radio image delivery with explicitly simulated GPS.

## 1 Reconnect the working hardware

Keep the camera ribbon and AI HAT assembly as they were when streaming worked. Connect the Pi's Ethernet cable to the desktop Ethernet port. Connect the Pi-side radio to the Pi USB port and the base radio to Windows. Keep antennas attached to both radios. Use the Pi's existing power supply for power; the Ethernet cable is not its power source. Allow the Pi to boot.

You do not need to reinstall Windows internet sharing or change the working radio baud settings just to use link-local SSH. COM8, /dev/ttyUSB0 and IPv6 scope %6 below are the last observed desktop values and may change with hardware/interface changes.

## 2 Open Pi SSH from Windows PowerShell

```powershell
ssh nightowl@fe80::8aa2:9eff:fec6:538e%6
```

Enter the Pi password privately. If it fails, in Windows check Ethernet's current index:

```powershell
Get-NetAdapter | Format-Table Name,ifIndex,Status,LinkSpeed
```

If Ethernet is index 6, discover its IPv6 neighbors:

```powershell
ping -6 -n 3 ff02::1%6
Get-NetNeighbor -InterfaceIndex 6
```

Substitute the actual Ethernet index if different, including the scope suffix in SSH. The previously reachable Pi MAC was 88-A2-9E-C6-53-8E. Do not guess an unrelated Wi-Fi neighbor as the Pi. A changed-host-key warning needs investigation before accepting it.

## 3 Install this code copy once

Stop an existing camera launcher with Ctrl+C. Leave the original camera source and ~/radio_image_v2 folder intact. In a separate WINDOWS PowerShell window, copy exactly the new base_station folder:

```powershell
scp -r "C:\Users\theon\Downloads\Night_Owl_GPS_Test\base_station" "nightowl@[fe80::8aa2:9eff:fec6:538e%6]:~/radio_gps_v1"
```

Use this destination once. If ~/radio_gps_v1 already exists, inspect it and choose a fresh destination name rather than repeating a folder copy and creating nesting. Never run scp -r . from the Windows home folder.

## 4 Start the new Windows serial GUI

Close old GUI/miniterm sessions holding COM8. In Windows:

```powershell
cd "C:\Users\theon\Downloads\Night_Owl_GPS_Test\base_station"
py -m serial.tools.list_ports -v
py gui.py --serial-port COM8 --baud 57600
```

Use the actual Windows radio port. The previously observed base radio serial was DU0E3ZUNA. Do not run receiver.py alongside the GUI. If the Windows-only test GUI is still open, close it first to avoid confusing the two sessions.

## 5 Start live camera with simulated GPS on the Pi

Run these commands at the nightowl@Jrpi prompt:

```bash
cd ~/radio_gps_v1
source ~/radio_camera_env/bin/activate
python -m serial.tools.list_ports -v
python Night_Owl_Live_Radio.py --check --gps simulate
python -u Night_Owl_Live_Radio.py --serial-port /dev/ttyUSB0 --baud 57600 --gps simulate
```

The existing environment has camera/Hailo modules and pyserial. Simulation adds no new Pi package. Do not install pymavlink for this test. The launcher still needs ~/night_owl_final.py, ~/yolov11n_h8.hef and the working system camera/Hailo installation.

Watch for SIMULATED TEST DATA on the Pi. Stand in camera view. Allow the original 85% threshold, 10-second per-class cooldown and several seconds per image transmission. Select new rows to check the SIM label and GPS status alongside the thumbnail. Because detections are intermittent, not every 45-second scenario phase will necessarily produce a camera detection; the Windows-only sender exercises phases more predictably.

--check verifies camera-source adaptation and dependency discovery without starting hardware. Passing it alone does not prove the camera/radio pipeline.

## 6 Optional browser video

In another Windows PowerShell window:

```powershell
ssh -N -L 8081:127.0.0.1:8080 nightowl@fe80::8aa2:9eff:fec6:538e%6
```

Leave that tunnel open and browse http://localhost:8081. Video travels over Ethernet/SSH, while detections and thumbnails travel over radios. The browser is optional; the receiving GUI must remain open.

## 7 Default no GPS and rollback

Stop the launcher with Ctrl+C. Omit --gps simulate, or use --gps none, to resume detections with GPS explicitly disabled. There is no automatic fallback to fabricated coordinates.

To roll back completely, close the new GUI and stop the new launcher, then launch the old Windows Night_Owl_Image_Test/base_station GUI and ~/radio_image_v2 launcher as before. Do not run two programs on the same radio port or camera.

## 8 Stop safely

Ctrl+C in the Pi live terminal; wait for its prompt. Run sudo shutdown -h now. Wait at least 30 seconds for shutdown before removing power. Close the Windows GUI and stop the SSH video tunnel with Ctrl+C. Do not move the ribbon while powered.

Clock synchronization remains a separate task: the previous session showed the Pi clock days behind Windows. GPS freshness uses a monotonic clock, but the detection timestamp and recorded received_at still depend on the Pi wall clock. The simulation does not correct the system clock.
