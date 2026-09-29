"""Ports (interfaces) the application needs. Adapters implement them."""
from datetime import datetime
from typing import Any, Dict, List, Optional

try:  # Python 3.8+
    from typing import Protocol
except ImportError:  # pragma: no cover
    Protocol = object  # type: ignore

from ..domain.risk import SiteRisk
from ..domain.sites import Site
from ..domain.validation import ValidationReport


class SiteRepository(Protocol):
    def all(self) -> Dict[str, Site]: ...


class RecordRepository(Protocol):
    def all(self) -> List[Dict[str, Any]]: ...


class FhirTranslator(Protocol):
    def assessment_bundle(self, report: ValidationReport, site: Site) -> Optional[Dict[str, Any]]: ...

    def risk_bundle(self, risk: SiteRisk, site: Site) -> Optional[Dict[str, Any]]: ...


class FhirServer(Protocol):
    def post_transaction(self, bundle: Dict[str, Any]) -> Dict[str, Any]: ...


class Clock(Protocol):
    def now(self) -> datetime: ...
