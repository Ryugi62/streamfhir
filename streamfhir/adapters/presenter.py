"""Adapter: turn domain/application objects into JSON-ready dicts for the UI and CLI."""
import dataclasses
from datetime import datetime
from typing import Any

from ..domain.risk import rules_table


def to_jsonable(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        out = {f.name: to_jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
        if hasattr(obj, "needs_flag"):
            out["needs_flag"] = obj.needs_flag
        return out
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    return obj


def _risk_json(risk) -> dict:
    out = to_jsonable(risk)
    if not risk.hazard_assessed:
        out["level"] = "not-assessed"     # missing data is not good news: never report "low" for nothing observed
    return out


def overview_json(overview, as_of=None, dataset=None) -> dict:
    return {"rules": rules_table(), "as_of": to_jsonable(as_of), "dataset": dataset or {"synthetic": True}, "sites": [{
        "site": to_jsonable(o.site),
        "risk": _risk_json(o.risk),
        "reports": [{"record_id": r.record_id, "status": r.status, "decision": o.decisions.get(r.record_id),
                     "basis": (o.bases or {}).get(r.record_id),
                     "observed_at": to_jsonable(r.assessment.observed_at) if r.assessment else None,
                     "has_photo": bool(r.assessment and r.assessment.photos),
                     "values": to_jsonable(r.assessment.values) if r.assessment else {},
                     "below_limit": to_jsonable(r.assessment.below_limit) if r.assessment else {},
                     "observer_name": r.assessment.observer_name if r.assessment else "",
                     "issues": to_jsonable(r.issues)} for r in o.reports],
        "flag_bundle": o.flag_bundle} for o in overview]}


def check_json(result) -> dict:
    report = result.report
    return {"report": {"record_id": report.record_id, "status": report.status, "issues": to_jsonable(report.issues)},
            "bundle": result.bundle}
