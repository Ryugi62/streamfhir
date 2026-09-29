"""Application use cases.

UC-1 CheckRecord · UC-2 SiteRiskOverview · UC-3 ShareBundle · UC-4 ReviewRecord.
"""
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..domain.risk import CONFIRM, REJECT, SiteRisk, evaluate_site
from ..domain.sites import Site
from ..domain.validation import REVIEW, ValidationReport, validate_record
from .ports import Clock, FhirServer, FhirTranslator, RecordRepository, ReviewStore, SiteRepository


REVIEW_BASES = ("site-visit", "photo-checked", "lab-result")


class InMemoryReviews:
    """Default ReviewStore (decisions live for the lifetime of the process)."""
    def __init__(self):
        self._d: Dict[str, str] = {}
        self._b: Dict[str, str] = {}

    def all(self) -> Dict[str, str]:
        return dict(self._d)

    def bases(self) -> Dict[str, str]:
        return dict(self._b)

    def set(self, record_id: str, decision: Optional[str], basis: Optional[str] = None) -> None:
        if decision is None:
            self._d.pop(record_id, None)
            self._b.pop(record_id, None)
        else:
            self._d[record_id] = decision
            if basis:
                self._b[record_id] = basis


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
    decisions: Dict[str, str]
    bases: Dict[str, str] = None


class StreamFhirService:
    def __init__(self, sites: SiteRepository, records: RecordRepository, translator: FhirTranslator,
                 server: FhirServer, clock: Clock, reviews: Optional[ReviewStore] = None):
        self.sites = sites
        self.records = records
        self.translator = translator
        self.server = server
        self.clock = clock
        self.reviews = reviews or InMemoryReviews()

    # UC-1
    def check_record(self, raw: Dict[str, Any], stamp_now: bool = False) -> CheckResult:
        """stamp_now: a record entered right now gets the service clock as its observation time
        (the demo evaluates as of a fixed date, so a device clock must not decide 'the future')."""
        sites = self.sites.all()
        if stamp_now:
            raw = dict(raw, observed_at=self.clock.now().isoformat())
        report = validate_record(raw, sites, self.clock.now())
        bundle = None
        if report.assessment is not None:
            decision = self.reviews.all().get(report.record_id)
            basis = self._bases().get(report.record_id)
            bundle = self.translator.assessment_bundle(report, sites[report.assessment.site_id], decision, basis)
        return CheckResult(report, bundle)

    def _bases(self) -> Dict[str, str]:
        return self.reviews.bases() if hasattr(self.reviews, "bases") else {}

    # UC-2
    def site_overview(self) -> List[SiteOverview]:
        sites = self.sites.all()
        now = self.clock.now()
        decisions = self.reviews.all()
        by_site: Dict[str, List[ValidationReport]] = {sid: [] for sid in sites}
        for raw in self.records.all():
            sid = raw.get("site_id")
            if sid in by_site:
                by_site[sid].append(validate_record(raw, sites, now))
        out = []
        for sid, site in sites.items():
            risk = evaluate_site(sid, by_site[sid], as_of=now, decisions=decisions, still_water=site.still_water)
            flag = self.translator.risk_bundle(risk, site) if risk.needs_flag else None
            site_decisions = {r.record_id: decisions[r.record_id] for r in by_site[sid] if r.record_id in decisions}
            bases = self._bases()
            out.append(SiteOverview(site, risk, by_site[sid], flag, site_decisions,
                                    {k: bases[k] for k in site_decisions if k in bases}))
        return out

    def flag_bundles(self, include_stand_down: bool = False) -> List[Dict[str, Any]]:
        """Active Flags for high sites; with include_stand_down, an inactive update only for sites
        whose Flag is currently active on the server (never creates Flags for never-flagged sites)."""
        out = []
        for o in self.site_overview():
            if o.flag_bundle:
                out.append(o.flag_bundle)
            elif include_stand_down:
                current = self.server.find_flag(o.site.site_id)
                if current and current.get("status") == "active":
                    start = (current.get("period") or {}).get("start")
                    version = (current.get("meta") or {}).get("versionId")
                    out.append(self.translator.stand_down_bundle(o.risk, o.site, start, version))
        return out

    # UC-3
    def share(self, bundle: Dict[str, Any], live: bool = False) -> Dict[str, Any]:
        counts = Counter(e["resource"]["resourceType"] for e in bundle.get("entry", []))
        if not live:
            return {"mode": "dry-run", "resource_counts": dict(counts),
                    "message": "Dry run: nothing was sent. %d resources are ready for a FHIR server." % sum(counts.values())}
        result = self.server.post_transaction(bundle)
        return dict(result, mode="live", resource_counts=dict(counts))

    # UC-4
    def review(self, record_id: str, decision: Optional[str], basis: Optional[str] = None) -> Dict[str, Any]:
        if decision not in (CONFIRM, REJECT, None):
            raise ValueError("decision must be 'confirm', 'reject' or null")
        if decision == CONFIRM and basis not in REVIEW_BASES:
            raise ValueError("a confirmation needs a basis: one of %s" % ", ".join(REVIEW_BASES))
        sites = self.sites.all()
        raw = next((r for r in self.records.all() if r.get("record_id") == record_id), None)
        if raw is None:
            raise KeyError(record_id)
        report = validate_record(raw, sites, self.clock.now())
        if report.status != REVIEW:
            raise ValueError("only records that need review can be confirmed or rejected")
        self.reviews.set(record_id, decision, basis)
        return {"record_id": record_id, "decision": decision, "basis": basis}
