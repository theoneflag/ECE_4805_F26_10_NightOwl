# Night Owl

MDE capstone project developing a drone based
object-detection system with onboard processing and a custom
base-station interface. 

The system is intended to transmit object-detection information
and associated location data from the drone to a ground-station
computer for viewing, filtering, and analysis.

## Project Goal

Our goal is to connect onboard object detection, flight telemetry,
and ground station software into a system that helps operators
review detections and their associated information.

## System Overview

- The onboard camera captures images for object detection.
- The Raspberry Pi processes detections and prepares detection data.
- The Pixhawk 6C runs ArduPilot and provides flight telemetry.
- The communication software transfers detection packets to
  the ground station.
- The custom base-station GUI displays received detections
  and allows operators to inspect and filter the data.

Full integration of these subsystems is in progress.

## Implemented Software Features

- Local communication prototype using simulated detection packets.
- Packet validation with acceptance and rejection responses
  in the communication prototype.
- Python desktop GUI for displaying detection information.
- Filtering by object type.
- Detailed views of detection confidence, timestamps,
  coordinates, altitude, and bounding boxes.
- Thumbnail preview for detections with available image files.
- UDP packet reception for the base-station GUI.

## Planned Development

- Connect onboard object-detection output to packet transmission.
- Integrate Pixhawk GPS and altitude telemetry with detection data.
- Verify communication between the Raspberry Pi and ground station.
- Test the complete detection-to-display workflow.
- Document setup, configuration, and subsystem interfaces
  for future teams.

## Repository Structure

- `base_station/`: Base-station GUI, packet-handling modules,
  communication prototype scripts, and sample detection data.

## Technologies

- Onboard computer: Raspberry Pi 5
- Flight controller: Pixhawk 6C with ArduPilot
- Data format: JSON
- Communication: TCP prototype and UDP base-station receiver
- Flight configuration and telemetry: QGroundControl
- Sensors: Camera, GPS, and LiDAR
- Version control: Git and GitHub

## Base Station and Flight Software

The custom Night Owl GUI displays and filters object-detection
data, including detection details and available thumbnails.

QGroundControl supports vehicle configuration, flight telemetry,
and flight operations. The custom GUI provides the detection
review interface used alongside that flight software.

## Team Members

- Joseph E.
- Christopher F.
- Justin S.
- Luke H.
- Crew G.

## Project Status

The project has progressed through drone assembly, flight testing,
and development of the communication prototype and base-station GUI.

The communication prototype has been tested with simulated packets.
The GUI supports detection viewing, filtering, and detailed inspection.

Integration of onboard detections, flight telemetry, and transmission
to the base station remains in progress.
