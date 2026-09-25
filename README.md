# Night Owl Communication Prototype

A Virginia Tech senior design project focused on developing
a communication system that sends drone object-detection and
location data to a ground-station computer.

## Project Goal

Our goal is to create a reliable communication interface
between the drone's onboard Raspberry Pi and the ground
station while incorporating flight data from ArduPilot.

## Planned Features

- Detect objects using an onboard camera and Raspberry Pi.
- Retrieve GPS and altitude data from the Pixhawk.
- Send detection data to a ground station computer.
- Validate received packets and reject invalid data.
- Return acceptance or rejection acknowledgments.
- Record detection information for later analysis.

These features are planned and may change during development.

## Technologies

- Onboard computer: Raspberry Pi
- Flight controller: Pixhawk 6C with ArduPilot
- Communication: Python, TCP sockets, and JSON
- Ground station software: Mission Planner
- Sensors: Camera, GPS, and LiDAR
- Version control: Git and GitHub

## Team Members

- Joseph E.
- Christopher F.
- Justin S.
- Luke H.
- Crew G.

## Project Status

Drone assembly and subsystem development are currently in
progress. The local Python communication prototype can send,
validate, accept, and reject simulated detection packets.
