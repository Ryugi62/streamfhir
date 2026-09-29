"""Explainable, rule-based One Health risk summary per site.

No machine learning and no accuracy claims: every point on a site card comes from a
printed rule below. Thresholds marked "demo" are configurable starting points for a
local team to tune, not regulatory limits.

Two separate scores (they answer different questions):
* health hazard  - acute signals of exposure risk for people and animals (rules of kind "hazard");
                   only this score can raise a FHIR Flag.
* ecological condition - slower, chronic state of the stream (rules of kind "condition").

A FHIR Flag is only raised when the *corroborated* hazard points reach the threshold.
Corroborated = supported by trusted records (status ok, or confirmed by a reviewer)
from >= 2 distinct observers, or by a trusted record with a photo attached, or by a
reviewer confirmation. Records a reviewer rejected never count.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .indicators import SCORE_CODES
from .validation import BLOCKED, OK, ValidationReport

ENVIRONMENT, ANIMAL, HUMAN = "environment", "animal", "human"
LANES = (ENVIRONMENT, ANIMAL, HUMAN)
HAZARD, CONDITION = "hazard", "condition"

HIGH, VERIFY, MODERATE, LOW = "high", "verify", "moderate", "low"
FLAG_HAZARD_POINTS = 5     # corroborated hazard points needed for a Flag
MODERATE_HAZARD_POINTS = 2
POOR_CONDITION_POINTS = 4
FAIR_CONDITION_POINTS = 2
WINDOW_DAYS = 14
CLEAR_VISITS = 2           # a health hazard is only cleared by >= 2 trusted clear visits ...
CLEAR_SPAN_DAYS = 7        # ... at least 7 days apart (scums move with wind within hours)
NO3_PER_N = 4.43           # 62 g/mol NO3 / 14 g/mol N

CONFIRM, REJECT = "confirm", "reject"

Values = Dict[str, object]


@dataclass(frozen=True)
class Rule:
    rule_id: str
    title: str
    kind: str
    points: Dict[str, int]
    condition_text: str
    why: str
    advice: str
    keys: Tuple[str, ...] = ()                                   # indicators the rule reads
    record_test: Optional[Callable[[Values], bool]] = None     # evaluated per record
    requires_any: Tuple[str, ...] = ()                           # contact rules: prerequisite hazards
    contact_code: Optional[str] = None
    clear_keys: Tuple[str, ...] = ()                             # ALL must be observed to count as a clear visit
    flowing_only: bool = False                                   # stream methods, skipped at still-water sites


def _num(v: Values, code: str) -> Optional[float]:
    x = v.get(code)
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def nitrate_as_no3(v: Values) -> Optional[float]:
    x = _num(v, "nitrate")
    if x is None:
        return None
    return x * NO3_PER_N if v.get("nitrate-basis") == "as-N" else x


def _mean_score(v: Values) -> Optional[float]:
    s = [v[c] for c in SCORE_CODES if isinstance(v.get(c), int)]
    return sum(s) / len(s) if s else None


def _ge(x: Optional[float], limit: float) -> bool:
    return x is not None and x >= limit


def _bloom(v: Values) -> bool:
    looks = v.get("surface") == "algal-scum" or (v.get("water-colour") == "green" and _ge(_num(v, "water-temperature"), 20))
    return looks and v.get("bloom-check") != "settles-or-strings"


def _invertebrates_absent(v: Values) -> bool:
    return v.get("sensitive-invertebrates") == 0 and _ge(_num(v, "kick-sample-minutes"), 1)


RULES: Tuple[Rule, ...] = (
    Rule("R1", "Poor habitat", CONDITION, {ENVIRONMENT: 2},
         "mean of the visual habitat scores <= 5 (of 10)",
         "Degraded banks, channel and riparian strip reduce the stream's ability to clean itself and to host life.",
         "Share with the city's river restoration team; repeat the visual check each season.",
         keys=SCORE_CODES, record_test=lambda v: _mean_score(v) is not None and _mean_score(v) <= 5,
         clear_keys=SCORE_CODES, flowing_only=True),
    Rule("R2", "Nutrient enrichment (screening)", CONDITION, {ENVIRONMENT: 2},
         "nitrate >= 25 mg/L as NO3 (a conservative screening value, half the French good/moderate boundary of 50) "
         "or orthophosphate >= 0.5 mg/L as PO4 (the French good/moderate boundary); one sample is a screening signal, not a status class",
         "Extra nutrients can feed algae; in most fresh waters phosphate is the limiting nutrient, so high phosphate matters most "
         "for blooms, while high nitrate mainly signals run-off.",
         "Look upstream for run-off sources (fields, allotments, outfalls) and keep monitoring.",
         keys=("nitrate", "phosphate"),
         record_test=lambda v: _ge(nitrate_as_no3(v), 25) or _ge(_num(v, "phosphate"), 0.5),
         clear_keys=("nitrate", "phosphate")),
    Rule("R3", "Possible cyanobacterial bloom", HAZARD, {ANIMAL: 3, HUMAN: 2},
         "surface = algal-scum, or water-colour = green with water >= 20 C; not counted if the jar/stick test "
         "points to ordinary green or filamentous algae",
         "Scums in warm water are typical of cyanobacterial blooms, which can poison dogs, livestock and people. "
         "Only a lab test can confirm toxins. If the jar/stick test was not done, the scum is treated as a possible bloom (precaution).",
         "Keep dogs and children out of the water and report the bloom to the local authority for testing.",
         keys=("surface", "water-colour", "bloom-check"), record_test=_bloom, clear_keys=("surface", "water-colour")),
    Rule("R4", "Sewage signal", HAZARD, {HUMAN: 3, ENVIRONMENT: 1},
         "odour = sewage or water-colour = milky-grey",
         "Sewage smell or grey water suggests faecal contamination, a route for gut infections in people and animals.",
         "Avoid touching the water, wash hands, and report it to the water utility or municipality.",
         keys=("odour", "water-colour"),
         record_test=lambda v: v.get("odour") == "sewage" or v.get("water-colour") == "milky-grey",
         clear_keys=("odour", "water-colour")),
    Rule("R5", "Dead fish", HAZARD, {ANIMAL: 3, ENVIRONMENT: 1},
         "dead-fish = yes",
         "Fish kills point to low oxygen, toxins or pollution - an early warning for other animals.",
         "Do not touch the fish; note how many and report to the environment authority.",
         keys=("dead-fish",), record_test=lambda v: v.get("dead-fish") is True, clear_keys=("dead-fish",)),
    Rule("R6", "People in contact with affected water", HAZARD, {HUMAN: 2},
         "people-contact = yes while R3 or R4 fired at the site",
         "Exposure turns a water problem into a human-health problem.",
         "Discourage paddling and swimming here until the signal has been checked.",
         keys=("people-contact",), requires_any=("R3", "R4"), contact_code="people-contact"),
    Rule("R7", "Pets or livestock in contact with affected water", HAZARD, {ANIMAL: 2},
         "animal-contact = yes while R3, R4 or R5 fired at the site",
         "Dogs and livestock drink and swim in affected water and are often the first victims of blooms.",
         "Keep pets and livestock out of the water and away from scum on the shore.",
         keys=("animal-contact",), requires_any=("R3", "R4", "R5"), contact_code="animal-contact"),
    Rule("R8", "Oil or chemical signal", HAZARD, {ENVIRONMENT: 2, HUMAN: 1},
         "surface = oily-sheen or odour = chemical",
         "Hydrocarbons and chemicals harm aquatic life and can irritate skin on contact.",
         "Avoid skin contact and report the sheen or smell to the environment authority.",
         keys=("surface", "odour"),
         record_test=lambda v: v.get("surface") == "oily-sheen" or v.get("odour") == "chemical",
         clear_keys=("surface", "odour")),
    Rule("R9", "Nitrate above 50 mg/L (EU Nitrates Directive level)", CONDITION, {HUMAN: 1},
         "nitrate >= 50 mg/L as NO3: the level at which the EU Nitrates Directive (91/676/EEC, Annex I) treats surface "
         "fresh water as affected by pollution; it is also the EU drinking-water parametric value",
         "Stream water is not drinking water, but this level signals run-off that may reach wells and supplies.",
         "Mention it to the water utility; private well owners nearby may want a lab test.",
         keys=("nitrate",), record_test=lambda v: _ge(nitrate_as_no3(v), 50), clear_keys=("nitrate",)),
    Rule("R10", "No mayfly, stonefly or caddisfly larvae found", CONDITION, {ENVIRONMENT: 2},
         "sensitive-invertebrates = 0 after >= 1 minute of kick-net sampling in a flowing reach",
         "Most families of these groups need clean, oxygen-rich water, so finding none suggests poorer conditions "
         "over weeks. Some families tolerate pollution, so this is a coarse screen, not a biotic index.",
         "Repeat the sample in a riffle; if still none, share with the ecology team for a full index.",
         keys=("sensitive-invertebrates", "kick-sample-minutes"), record_test=_invertebrates_absent,
         clear_keys=("sensitive-invertebrates", "kick-sample-minutes"), flowing_only=True),
)


@dataclass(frozen=True)
class FiredRule:
    rule_id: str
    title: str
    kind: str
    points: Dict[str, int]
    record_ids: Tuple[str, ...]
    observers: Tuple[str, ...]
    corroborated: bool
    corroboration: str
    why: str
    advice: str
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    trusted_record_ids: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SiteRisk:
    site_id: str
    level: str
    hazard_points: int
    confirmed_hazard_points: int
    lane_points: Dict[str, int]
    condition_points: int
    condition: str
    fired: Tuple[FiredRule, ...]
    record_ids: Tuple[str, ...]
    excluded_record_ids: Tuple[str, ...]
    window_start: Optional[datetime]
    window_end: Optional[datetime]
    decisions: Dict[str, str] = field(default_factory=dict)
    hazard_assessed: bool = True       # False: no record in the window observed any hazard input

    @property
    def needs_flag(self) -> bool:
        return self.level == HIGH

    @property
    def total_points(self) -> int:
        return self.hazard_points


def is_trusted(report: ValidationReport, decisions: Mapping[str, str]) -> bool:
    d = decisions.get(report.record_id)
    return d == CONFIRM or (d is None and report.status == OK)


def _corroboration(rule_supporting: List[ValidationReport], decisions: Mapping[str, str]) -> Tuple[bool, str]:
    trusted = [r for r in rule_supporting if is_trusted(r, decisions)]
    observers = {r.assessment.observer for r in trusted}
    if any(decisions.get(r.record_id) == CONFIRM for r in trusted):
        return True, "confirmed by a reviewer"
    if trusted and all(r.assessment.origin for r in trusted):
        return True, "agency monitoring result (laboratory), not a citizen report"
    if len(observers) >= 2:
        return True, "%d independent observers" % len(observers)
    if any(r.assessment.photos for r in trusted):
        return True, "trusted report with a photo attached"
    return False, "single or unreviewed report - verify"


def _fired(rule: Rule, supporting: List[ValidationReport], decisions: Mapping[str, str],
           prerequisite_ok: bool = True) -> FiredRule:
    ok, how = _corroboration(supporting, decisions)
    if not prerequisite_ok:
        ok, how = False, "exposure seen, but the hazard it depends on is not yet corroborated"
    trusted = [r for r in supporting if is_trusted(r, decisions)]
    times = [r.assessment.observed_at for r in (trusted or supporting)]  # dates come from trusted reports when any
    return FiredRule(rule.rule_id, rule.title, rule.kind, dict(rule.points),
                     tuple(r.record_id for r in supporting),
                     tuple(sorted({r.assessment.observer for r in supporting})),
                     ok, how, rule.why, rule.advice, min(times), max(times),
                     tuple(r.record_id for r in trusted))


def _observed_all(r: ValidationReport, keys: Sequence[str]) -> bool:
    return bool(keys) and all(k in r.assessment.values for k in keys)


def _cleared(rule: Rule, supporting: List[ValidationReport], window: List[ValidationReport],
             decisions: Mapping[str, str]) -> bool:
    """A signal is cleared only by later trusted visits that observed ALL of the rule's clear_keys without it.
    Hazards need CLEAR_VISITS such visits spanning >= CLEAR_SPAN_DAYS; condition rules need one."""
    last = max(r.assessment.observed_at for r in supporting)
    clears = sorted(r.assessment.observed_at for r in window
                    if r.assessment.observed_at > last and is_trusted(r, decisions)
                    and _observed_all(r, rule.clear_keys) and not rule.record_test(r.assessment.values))
    if rule.kind != HAZARD:
        return len(clears) >= 1
    return len(clears) >= CLEAR_VISITS and (clears[-1] - clears[0]) >= timedelta(days=CLEAR_SPAN_DAYS)


def evaluate_site(site_id: str, reports: Sequence[ValidationReport], as_of: Optional[datetime] = None,
                  decisions: Optional[Mapping[str, str]] = None, window_days: int = WINDOW_DAYS,
                  still_water: bool = False) -> SiteRisk:
    decisions = dict(decisions or {})
    usable = [r for r in reports if r.status != BLOCKED and r.assessment is not None
              and r.assessment.site_id == site_id and decisions.get(r.record_id) != REJECT]
    excluded = tuple(r.record_id for r in reports
                     if r.status == BLOCKED or decisions.get(r.record_id) == REJECT)
    empty = {k: 0 for k in LANES}
    if as_of is None:
        as_of = max((r.assessment.observed_at for r in usable), default=None)
    if as_of is None:
        return SiteRisk(site_id, LOW, 0, 0, empty, 0, "not assessed", (), (), excluded, None, None, decisions, False)
    start = as_of - timedelta(days=window_days)
    window = sorted((r for r in usable if start <= r.assessment.observed_at <= as_of),
                    key=lambda r: r.assessment.observed_at)

    fired: List[FiredRule] = []
    for rule in RULES:
        if rule.flowing_only and still_water:
            continue
        if rule.record_test is not None:
            supporting = [r for r in window if rule.record_test(r.assessment.values)]
            if supporting and not _cleared(rule, supporting, window, decisions):
                fired.append(_fired(rule, supporting, decisions))
        else:
            prereq = [f for f in fired if f.rule_id in rule.requires_any]
            if not prereq:
                continue
            since = min(f.first_seen for f in prereq)
            supporting = [r for r in window if r.assessment.values.get(rule.contact_code) is True
                          and r.assessment.observed_at >= since - timedelta(days=window_days)]
            if supporting:
                fired.append(_fired(rule, supporting, decisions, any(f.corroborated for f in prereq)))

    lanes = {k: 0 for k in LANES}
    hazard = confirmed = condition_pts = 0
    for f in fired:
        pts = sum(f.points.values())
        if f.kind == HAZARD:
            hazard += pts
            confirmed += pts if f.corroborated else 0
            for lane, p in f.points.items():
                lanes[lane] += p
        else:
            condition_pts += pts

    if confirmed >= FLAG_HAZARD_POINTS:
        level = HIGH
    elif hazard >= FLAG_HAZARD_POINTS:
        level = VERIFY
    elif hazard >= MODERATE_HAZARD_POINTS:
        level = MODERATE
    else:
        level = LOW
    def observed(kind: str) -> bool:
        keys = {k for rule in RULES if rule.kind == kind and not (rule.flowing_only and still_water)
                for k in (rule.clear_keys or rule.keys)}
        return any(k in r.assessment.values for r in window for k in keys)

    if not observed(CONDITION):
        cond = "not assessed"   # missing data is not good news
    else:
        cond = "poor" if condition_pts >= POOR_CONDITION_POINTS else "fair" if condition_pts >= FAIR_CONDITION_POINTS else "good"
    return SiteRisk(site_id, level, hazard, confirmed, lanes, condition_pts, cond, tuple(fired),
                    tuple(r.record_id for r in window), excluded, start, as_of, decisions,
                    hazard_assessed=observed(HAZARD) or any(f.kind == HAZARD for f in fired))


def rules_table() -> List[Dict[str, object]]:
    return [{"id": r.rule_id, "title": r.title, "kind": r.kind, "points": dict(r.points), "when": r.condition_text,
             "why": r.why, "advice": r.advice, "flowing_only": r.flowing_only,
             "clears_after": "%d trusted clear visits >= %d days apart" % (CLEAR_VISITS, CLEAR_SPAN_DAYS)
             if r.kind == HAZARD else "1 trusted clear visit"} for r in RULES]
