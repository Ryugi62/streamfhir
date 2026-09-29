"""Composition root: wire adapters into the application service."""
import os
from datetime import datetime, timezone

from ..adapters.fhir_mapper import FhirMapper
from ..adapters.hapi_client import DEFAULT_BASE, HapiFhirServer
from ..adapters.json_repository import JsonRecordRepository, JsonSiteRepository
from ..application.use_cases import StreamFhirService

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(ROOT, "data")


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FixedClock:
    """Demo clock: sample data is evaluated 'as of' a fixed date so the demo never goes stale."""
    def __init__(self, at: datetime):
        self.at = at

    def now(self) -> datetime:
        return self.at


def build_service(data_dir: str = DATA_DIR, fhir_base: str = None) -> StreamFhirService:
    records = JsonRecordRepository(os.path.join(data_dir, "assessments.json"))
    as_of = records.demo_as_of()
    clock = SystemClock() if (os.environ.get("STREAMFHIR_REAL_CLOCK") == "1" or not as_of) \
        else FixedClock(datetime.fromisoformat(as_of))
    meta = records.dataset()
    return StreamFhirService(
        JsonSiteRepository(os.path.join(data_dir, "sites.json")),
        records,
        FhirMapper(test_data=meta.get("synthetic", True), pseudonymous=meta.get("observer_kind", "citizen") == "citizen"),
        HapiFhirServer(fhir_base or os.environ.get("STREAMFHIR_FHIR_BASE", DEFAULT_BASE)),
        clock,
        per_site_as_of=bool(meta.get("evaluate_each_site_at_its_latest_visit")),
        photo_corroborates=not meta.get("photo_needs_review"),
    )
