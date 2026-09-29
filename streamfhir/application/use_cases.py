"""Application use cases (UC-1 CheckRecord, UC-2 SiteRiskOverview, UC-3 ShareBundle)."""
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..domain.risk import SiteRisk, evaluate_site
from ..domain.sites import Site
from ..domain.validation import ValidationReport, validate_record
from .ports import Clock, FhirServer, FhirTranslator, RecordRepository, SiteRepository


@dataclass(frozen=True)
class CheckResult:
    report: ValidationReport
    bundle: Optional[Dict[str, Any]]


@dataclass(frozen=True)
class SiteOverview:
    site: Site
    risk: SiteRisk
    reports: List[ValidationReport]
    flag_bundle: Optional[Dict[str, Any]]


class StreamFhirService:
    def __init__(self, sites: SiteRepository, records: RecordRepository, translator: FhirTranslator,
                 server: FhirServer, clock: Clock):
        self.sites = sites
        self.records = records
        self.translator = translator
        self.server = server
        self.clock = clock

    # UC-1
    def check_record(self, raw: Dict[str, Any]) -> CheckResult:
        sites = self.sites.all()
        report = validate_record(raw, sites, self.clock.now())
        bundle = None
        if report.assessment is not None:
            bundle = self.translator.assessment_bundle(report, sites[report.assessment.site_id])
        return CheckResult(report, bundle)

    # UC-2
    def site_overview(self) -> List[SiteOverview]:
        sites = self.sites.all()
        now = self.clock.now()
        by_site: Dict[str, List[ValidationReport]] = {sid: [] for sid in sites}
        for raw in self.records.all():
            sid = raw.get("site_id")
            if sid in by_site:
                by_site[sid].append(validate_record(raw, sites, now))
        out = []
        for sid, site in sites.items():
            risk = evaluate_site(sid, by_site[sid])
            flag = self.translator.risk_bundle(risk, site) if risk.needs_flag else None
            out.append(SiteOverview(site, risk, by_site[sid], flag))
        return out

    # UC-3
    def share(self, bundle: Dict[str, Any], live: bool = False) -> Dict[str, Any]:
        counts = Counter(e["resource"]["resourceType"] for e in bundle.get("entry", []))
        if not live:
            return {"mode": "dry-run", "resource_counts": dict(counts),
                    "message": "Dry run: nothing was sent. %d resources are ready for a FHIR server." % sum(counts.values())}
        result = self.server.post_transaction(bundle)
        return dict(result, mode="live", resource_counts=dict(counts))
