import copy
import os
import sys
from datetime import datetime, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from streamfhir.domain.sites import Site  # noqa: E402

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)

SITES = {
    "S-TEST": Site("S-TEST", "Test Brook - park reach", 40.2150, -8.4050, "Test Brook"),
    "S-OTHER": Site("S-OTHER", "Other Brook", 40.1985, -8.4120, "Other Brook"),
}

GOOD = {
    "record_id": "T-1",
    "synthetic": True,
    "site_id": "S-TEST",
    "observed_at": "2026-09-20T09:30:00+01:00",
    "observer": "obs-aaa",
    "lat": 40.2151,
    "lon": -8.4051,
    "gps_accuracy_m": 6,
    "photos": ["https://example.org/demo-photos/t-1.jpg"],
    "values": {
        "channel-condition": 8, "bank-stability": 7, "riparian-zone": 9, "instream-habitat": 7,
        "water-temperature": 15.2, "ph": 7.4, "nitrate": 5, "nitrate-basis": "as-NO3", "phosphate": 0.1, "transparency": 110,
        "water-colour": "clear", "surface": "none", "odour": "none", "litter": "none",
        "dead-fish": False, "people-contact": False, "animal-contact": True,
    },
}


def make_record(**overrides):
    r = copy.deepcopy(GOOD)
    values = overrides.pop("values", None)
    r.update(overrides)
    if values:
        r["values"].update(values)
    return r


@pytest.fixture
def sites():
    return dict(SITES)


@pytest.fixture
def now():
    return NOW
