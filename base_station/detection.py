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
    expects_image: bool = False
    altitude_reference: str | None = None

    @classmethod
    def from_packet(cls, packet):
        position = packet["position"] or {}

        return cls(
            packet_id=packet["packet_id"],
            object_type=packet["object"]["type"],
            confidence=packet["object"]["confidence"],
            timestamp=packet["timestamp"],
            latitude=position.get("latitude"),
            longitude=position.get("longitude"),
            altitude_m=position.get("altitude_m"),
            image_path=packet.get("thumbnail"),
            expects_image=isinstance(packet.get("image"), dict),
            altitude_reference=position.get("altitude_reference"),
        )

    @property
    def formatted_latitude(self):
        return "Unavailable" if self.latitude is None else f"{self.latitude:.6f}"

    @property
    def formatted_longitude(self):
        return "Unavailable" if self.longitude is None else f"{self.longitude:.6f}"

    @property
    def formatted_altitude(self):
        if self.altitude_m is None:
            return "Not provided"
        suffix = " (AMSL)" if self.altitude_reference == "AMSL" else ""
        return f"{self.altitude_m}{suffix}"

    @property
    def formatted_time(self):
        return datetime.fromtimestamp(self.timestamp).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
