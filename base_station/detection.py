from dataclasses import dataclass
from datetime import datetime


@dataclass
class Detection:
    packet_id: int
    object_type: str
    confidence: float
    timestamp: float
    latitude: float
    longitude: float
    altitude_m: float | None
    image_path: str | None

    @classmethod
    def from_packet(cls, packet):
        position = packet["position"]

        return cls(
            packet_id=packet["packet_id"],
            object_type=packet["object"]["type"],
            confidence=packet["object"]["confidence"],
            timestamp=packet["timestamp"],
            latitude=position["latitude"],
            longitude=position["longitude"],
            altitude_m=position.get("altitude_m"),
            image_path=packet.get("thumbnail"),
        )

    @property
    def formatted_time(self):
        return datetime.fromtimestamp(self.timestamp).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
