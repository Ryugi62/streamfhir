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


def build_service(data_dir: str = DATA_DIR, fhir_base: str = None) -> StreamFhirService:
    return StreamFhirService(
        JsonSiteRepository(os.path.join(data_dir, "sites.json")),
        JsonRecordRepository(os.path.join(data_dir, "assessments.json")),
        FhirMapper(),
        HapiFhirServer(fhir_base or os.environ.get("STREAMFHIR_FHIR_BASE", DEFAULT_BASE)),
        SystemClock(),
    )
