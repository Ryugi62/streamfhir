"""Site entity and distance helper (pure)."""
import math
from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class Site:
    site_id: str
    name: str
    lat: float
    lon: float
    water_body: str
    description: str = ""
    flow: str = "flowing"          # "flowing" (stream reach) or "still" (pond, lake inlet)
    identifiers: Tuple[Tuple[str, str], ...] = ()   # (system, value) ids the site already has elsewhere, e.g. national or EU station codes

    @property
    def still_water(self) -> bool:
        return self.flow == "still"


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres (haversine)."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
