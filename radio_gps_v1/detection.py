import copy
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Detection:
    packet_id: int
    object_type: str
    confidence: float
    timestamp: float
    latitude: float | None
    longitude: float | None
    altitude_m: float | None
    image_path: str | None
    gps: dict | None = None

    @classmethod
    def from_packet(cls, packet):
        position = packet.get("position") or {}

        return cls(
            packet_id=packet["packet_id"],
            object_type=packet["object"]["type"],
            confidence=packet["object"]["confidence"],
            timestamp=packet["timestamp"],
            latitude=position.get("latitude"),
            longitude=position.get("longitude"),
            altitude_m=position.get("altitude_m"),
            image_path=packet.get("thumbnail"),
            gps=copy.deepcopy(packet.get("gps")),
        )

    @property
    def formatted_time(self):
        return datetime.fromtimestamp(self.timestamp).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    @property
    def formatted_latitude(self):
        return "Unavailable" if self.latitude is None else f"{self.latitude:.6f}{self.gps_suffix}"

    @property
    def formatted_longitude(self):
        return "Unavailable" if self.longitude is None else f"{self.longitude:.6f}{self.gps_suffix}"

    @property
    def gps_suffix(self):
        return " (SIM)" if self.gps and self.gps.get("simulated") else ""

    @property
    def gps_summary(self):
        if not self.gps:
            return "GPS source not supplied" if self.latitude is not None else "GPS unavailable"
        mode = "SIMULATED TEST DATA" if self.gps['simulated'] else self.gps['source'].upper()
        summary = f"GPS: {mode} | {self.gps['status']}"
        if self.gps.get('age_s') is not None:
            summary += f" | age {self.gps['age_s']:.1f}s at detection"
        if self.altitude_m is not None:
            summary += " | altitude MSL"
        return summary


    @property
    def formatted_altitude(self):
        if self.altitude_m is None:
            return "Not provided"
        if self.gps:
            return f"{self.altitude_m} MSL{self.gps_suffix}"
        return str(self.altitude_m)
