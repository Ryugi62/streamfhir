"""Record validation with human-in-the-loop flags.

Rules of the game:
* An *error* means the record cannot be shared (status ``blocked``).
* A *warning* means a person should take a quick look (status ``review``);
  the value is kept exactly as reported - never silently corrected.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .indicators import BOOLEAN, CATEGORY, COUNT, INDICATORS, QUANTITY, SCORE
from .sites import Site, distance_m

ERROR = "error"
WARNING = "warning"

OK = "ok"
REVIEW = "review"
BLOCKED = "blocked"

LOCATION_TOLERANCE_M = 250
MAX_GPS_ACCURACY_M = 50
MAX_AGE_DAYS = 365
REQUIRED_FIELDS = ("record_id", "site_id", "observed_at", "observer", "lat", "lon")


@dataclass(frozen=True)
class Issue:
    severity: str
    field: str
    code: str
    message: str


@dataclass(frozen=True)
class StreamAssessment:
    record_id: str
    site_id: str
    observed_at: datetime
    observer: str
    lat: float
    lon: float
    gps_accuracy_m: Optional[float]
    photos: Tuple[str, ...]
    values: Dict[str, Any]
    synthetic: bool = False


@dataclass(frozen=True)
class ValidationReport:
    record_id: str
    status: str
    issues: Tuple[Issue, ...]
    assessment: Optional[StreamAssessment] = field(default=None)

    def warnings_for(self, code: str) -> List[Issue]:
        return [i for i in self.issues if i.severity == WARNING and i.field == "values." + code]


def parse_time(text: Any) -> Optional[datetime]:
    if not isinstance(text, str):
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else None


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _fmt(v: float) -> str:
    return ("%g" % v)


def _check_value(code: str, value: Any, issues: List[Issue]) -> None:
    ind = INDICATORS.get(code)
    f = "values." + code
    if ind is None:
        issues.append(Issue(ERROR, f, "unknown-indicator",
                            "'%s' is not an indicator we know. Remove it or use one of the listed indicators." % code))
        return
    if ind.kind == SCORE:
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 10:
            issues.append(Issue(ERROR, f, "out-of-range",
                                "%s must be a whole number from 1 to 10 (got %r)." % (ind.display, value)))
    elif ind.kind == COUNT:
        lo, hi = ind.hard_range
        if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
            issues.append(Issue(ERROR, f, "out-of-range",
                                "%s must be a whole number from %d to %d (got %r)." % (ind.display, lo, hi, value)))
    elif ind.kind == QUANTITY:
        if not _num(value):
            issues.append(Issue(ERROR, f, "invalid-type", "%s must be a number (got %r)." % (ind.display, value)))
            return
        lo, hi = ind.hard_range
        if not lo <= value <= hi:
            issues.append(Issue(ERROR, f, "out-of-range",
                                "%s %s is impossible (valid range %s-%s). Please re-check the reading."
                                % (ind.display, _fmt(value), _fmt(lo), _fmt(hi))))
            return
        plo, phi = ind.plausible_range
        if not plo <= value <= phi:
            issues.append(Issue(WARNING, f, "implausible",
                                "%s %s is unusual for a stream (typical %s-%s). Kept as reported - a reviewer should confirm."
                                % (ind.display, _fmt(value), _fmt(plo), _fmt(phi))))
    elif ind.kind == CATEGORY:
        if value not in ind.answers:
            issues.append(Issue(ERROR, f, "invalid-answer",
                                "%s must be one of: %s (got %r)." % (ind.display, ", ".join(ind.answers), value)))
    elif ind.kind == BOOLEAN:
        if not isinstance(value, bool):
            issues.append(Issue(ERROR, f, "invalid-type", "%s must be yes/no (got %r)." % (ind.display, value)))


def validate_record(raw: Mapping[str, Any], sites: Mapping[str, Site], now: datetime) -> ValidationReport:
    issues: List[Issue] = []
    record_id = str(raw.get("record_id") or "(no id)")

    for name in REQUIRED_FIELDS:
        if raw.get(name) in (None, ""):
            issues.append(Issue(ERROR, name, "missing-field",
                                "'%s' is missing. We need it to know who, where and when." % name))

    observed_at = None
    if raw.get("observed_at"):
        observed_at = parse_time(raw.get("observed_at"))
        if observed_at is None:
            issues.append(Issue(ERROR, "observed_at", "invalid-timestamp",
                                "The date/time must look like 2026-09-20T09:30:00+01:00 (with a time zone)."))
        elif observed_at > now + timedelta(minutes=10):
            issues.append(Issue(ERROR, "observed_at", "future-timestamp",
                                "The observation time %s is in the future. Please check the phone's date." % raw["observed_at"]))
        elif observed_at < now - timedelta(days=MAX_AGE_DAYS):
            issues.append(Issue(WARNING, "observed_at", "stale",
                                "This observation is more than a year old. It is kept, but it will not describe today's stream."))

    lat, lon = raw.get("lat"), raw.get("lon")
    coords_ok = _num(lat) and _num(lon) and -90 <= lat <= 90 and -180 <= lon <= 180
    if lat is not None and lon is not None and not coords_ok:
        issues.append(Issue(ERROR, "lat/lon", "invalid-coordinates",
                            "Coordinates %r, %r are not valid latitude/longitude." % (lat, lon)))

    site = sites.get(raw.get("site_id")) if raw.get("site_id") else None
    if raw.get("site_id") and site is None:
        issues.append(Issue(ERROR, "site_id", "unknown-site",
                            "Site '%s' is not registered. Pick a registered site so visits can be compared." % raw.get("site_id")))
    if site is not None and coords_ok:
        d = distance_m(lat, lon, site.lat, site.lon)
        if d > LOCATION_TOLERANCE_M:
            issues.append(Issue(WARNING, "lat/lon", "location-mismatch",
                                "The phone position is %d m from '%s' (limit %d m). Was this the right site?"
                                % (round(d), site.name, LOCATION_TOLERANCE_M)))

    acc = raw.get("gps_accuracy_m")
    if _num(acc) and acc > MAX_GPS_ACCURACY_M:
        issues.append(Issue(WARNING, "gps_accuracy_m", "poor-gps",
                            "GPS accuracy was %s m (we like %d m or better). The position may be off." % (_fmt(acc), MAX_GPS_ACCURACY_M)))

    values = raw.get("values") or {}
    if not isinstance(values, dict) or not values:
        issues.append(Issue(ERROR, "values", "no-indicators", "The record has no indicator values, so there is nothing to share."))
        values = {}
    for code, value in values.items():
        _check_value(code, value, issues)

    if "nitrate" in values and "nitrate-basis" not in values:
        issues.append(Issue(WARNING, "values.nitrate", "nitrate-basis-missing",
                            "Nitrate %s mg/L has no basis. Is it 'as NO3' or 'as N'? They differ 4.43 times, so a reviewer should confirm."
                            % _fmt(values["nitrate"]) if _num(values["nitrate"]) else "Nitrate has no basis (as NO3 or as N)."))
    if "sensitive-invertebrates" in values and "kick-sample-minutes" not in values:
        issues.append(Issue(WARNING, "values.sensitive-invertebrates", "effort-missing",
                            "The invertebrate count has no sampling time, so a zero cannot be told apart from 'barely looked'."))

    photos = tuple(p for p in (raw.get("photos") or []) if isinstance(p, str) and p)
    if not photos:
        for code, value in values.items():
            ind = INDICATORS.get(code)
            if ind is not None and value in ind.needs_photo_when and (ind.kind != BOOLEAN or value is True):
                issues.append(Issue(WARNING, "values." + code, "photo-needed",
                                    "%s = %s is an important claim but no photo was attached. A reviewer should confirm it."
                                    % (ind.display, "yes" if value is True else value)))

    if any(i.severity == ERROR for i in issues):
        return ValidationReport(record_id, BLOCKED, tuple(issues), None)

    assessment = StreamAssessment(
        record_id=record_id, site_id=raw["site_id"], observed_at=observed_at, observer=str(raw["observer"]),
        lat=float(lat), lon=float(lon), gps_accuracy_m=acc if _num(acc) else None, photos=photos,
        values=dict(values), synthetic=bool(raw.get("synthetic", False)))
    status = REVIEW if issues else OK
    return ValidationReport(record_id, status, tuple(issues), assessment)
