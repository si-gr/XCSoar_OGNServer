from dataclasses import dataclass
from datetime import datetime


@dataclass
class Beacon:
    address: str
    name: str
    latitude: float
    longitude: float
    track: float
    altitude: float
    ground_speed: float
    climb_rate: float
    reference_timestamp: datetime
    beacon_type: str

    def __eq__(self, other):
        return self.address == other.address

    def to_csv_row(self, nickname: str = "", avg_climb: float | None = None) -> str:
        display_name = nickname if nickname else self.name
        return (
            f"{display_name},"
            f"{str(self.latitude)[:8]},"
            f"{str(self.longitude)[:8]},"
            f"{self.track},"
            f"{round(self.altitude)},"
            f"{round(self.ground_speed)},"
            f"{round(self.climb_rate, 1)},"
            f"{round(self.reference_timestamp.timestamp())},"
            f"{self.beacon_type}"
        ) + (f",{round(avg_climb, 1)}\n" if avg_climb is not None else "\n")
