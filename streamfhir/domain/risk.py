"""Explainable, rule-based One Health risk summary per site.

No machine learning and no accuracy claims: every point on a site card comes from a
printed rule below. Thresholds marked "demo" are configurable starting points for a
local team to tune, not regulatory limits.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .indicators import SCORE_CODES
from .validation import BLOCKED, ValidationReport

ENVIRONMENT, ANIMAL, HUMAN = "environment", "animal", "human"
LANES = (ENVIRONMENT, ANIMAL, HUMAN)

LOW, MODERATE, HIGH = "low", "moderate", "high"
HIGH_TOTAL = 6          # total points for "high"
HIGH_LANE = 4           # or any single lane reaching this
MODERATE_TOTAL = 3
WINDOW_DAYS = 14

Values = Dict[str, object]


@dataclass(frozen=True)
class Rule:
    rule_id: str
    title: str
    points: Dict[str, int]
    condition_text: str
    why: str
    record_test: Optional[Callable[[Values], bool]] = None     # evaluated per record
    requires_any: Tuple[str, ...] = ()                           # site-level: fires only if one of these fired
    contact_code: Optional[str] = None                           # site-level: records with this contact flag


def _mean_score(v: Values) -> Optional[float]:
    s = [v[c] for c in SCORE_CODES if isinstance(v.get(c), int)]
    return sum(s) / len(s) if s else None


def _ge(v: Values, code: str, limit: float) -> bool:
    x = v.get(code)
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x >= limit


RULES: Tuple[Rule, ...] = (
    Rule("R1", "Poor habitat", {ENVIRONMENT: 2},
         "mean of the 4 visual habitat scores <= 5 (of 10)",
         "Degraded banks, channel and riparian strip reduce the stream's ability to clean itself and to host life.",
         record_test=lambda v: (_mean_score(v) is not None and _mean_score(v) <= 5)),
    Rule("R2", "Nutrient enrichment", {ENVIRONMENT: 2},
         "nitrate >= 25 mg/L or phosphate >= 0.5 mg/L (demo thresholds)",
         "Extra nutrients feed algae; algae use up oxygen and can include toxin-producing cyanobacteria.",
         record_test=lambda v: _ge(v, "nitrate", 25) or _ge(v, "phosphate", 0.5)),
    Rule("R3", "Possible toxic algal bloom", {ANIMAL: 3, HUMAN: 2},
         "surface = algal-scum, or water-colour = green with water temperature >= 20 C",
         "Scums in warm water are typical of cyanobacterial blooms, which can poison dogs, livestock and people. Only a lab test can confirm toxins.",
         record_test=lambda v: v.get("surface") == "algal-scum"
         or (v.get("water-colour") == "green" and _ge(v, "water-temperature", 20))),
    Rule("R4", "Sewage signal", {HUMAN: 3, ENVIRONMENT: 1},
         "odour = sewage or water-colour = milky-grey",
         "Sewage smell or grey water suggests faecal contamination, a route for gut infections in people and animals.",
         record_test=lambda v: v.get("odour") == "sewage" or v.get("water-colour") == "milky-grey"),
    Rule("R5", "Dead fish", {ANIMAL: 3, ENVIRONMENT: 1},
         "dead-fish = yes",
         "Fish kills point to low oxygen, toxins or pollution - an early warning for other animals.",
         record_test=lambda v: v.get("dead-fish") is True),
    Rule("R6", "People in contact with affected water", {HUMAN: 2},
         "people-contact = yes while R3 or R4 fired at the site",
         "Exposure turns a water problem into a human-health problem.",
         requires_any=("R3", "R4"), contact_code="people-contact"),
    Rule("R7", "Pets or livestock in contact with affected water", {ANIMAL: 2},
         "animal-contact = yes while R3, R4 or R5 fired at the site",
         "Dogs and livestock drink and swim in affected water and are often the first victims of blooms.",
         requires_any=("R3", "R4", "R5"), contact_code="animal-contact"),
    Rule("R8", "Oil or chemical signal", {ENVIRONMENT: 2, HUMAN: 1},
         "surface = oily-sheen or odour = chemical",
         "Hydrocarbons and chemicals harm aquatic life and can irritate skin on contact.",
         record_test=lambda v: v.get("surface") == "oily-sheen" or v.get("odour") == "chemical"),
    Rule("R9", "Nitrate above drinking-water value", {HUMAN: 1},
         "nitrate >= 50 mg/L (the EU drinking-water parametric value, used only as an awareness anchor)",
         "Stream water is not drinking water, but this level signals run-off that may reach wells and supplies.",
         record_test=lambda v: _ge(v, "nitrate", 50)),
)


@dataclass(frozen=True)
class FiredRule:
    rule_id: str
    title: str
    points: Dict[str, int]
    record_ids: Tuple[str, ...]
    observers: Tuple[str, ...]
    corroborated: bool
    why: str


@dataclass(frozen=True)
class SiteRisk:
    site_id: str
    level: str
    total_points: int
    lane_points: Dict[str, int]
    fired: Tuple[FiredRule, ...]
    record_ids: Tuple[str, ...]
    excluded_record_ids: Tuple[str, ...]
    window_start: Optional[datetime]
    window_end: Optional[datetime]

    @property
    def needs_flag(self) -> bool:
        return self.level == HIGH


def _level(total: int, lanes: Dict[str, int]) -> str:
    if total >= HIGH_TOTAL or max(lanes.values()) >= HIGH_LANE:
        return HIGH
    if total >= MODERATE_TOTAL:
        return MODERATE
    return LOW


def _fired(rule: Rule, supporting: List[ValidationReport]) -> FiredRule:
    observers = tuple(sorted({r.assessment.observer for r in supporting}))
    has_photo = any(r.assessment.photos for r in supporting)
    return FiredRule(rule.rule_id, rule.title, dict(rule.points),
                     tuple(r.record_id for r in supporting), observers,
                     corroborated=len(observers) >= 2 or has_photo, why=rule.why)


def evaluate_site(site_id: str, reports: Sequence[ValidationReport], window_days: int = WINDOW_DAYS) -> SiteRisk:
    usable = [r for r in reports if r.status != BLOCKED and r.assessment is not None
              and r.assessment.site_id == site_id]
    excluded = tuple(r.record_id for r in reports if r.status == BLOCKED)
    if not usable:
        return SiteRisk(site_id, LOW, 0, {k: 0 for k in LANES}, (), (), excluded, None, None)

    end = max(r.assessment.observed_at for r in usable)
    start = end - timedelta(days=window_days)
    window = sorted((r for r in usable if r.assessment.observed_at >= start), key=lambda r: r.assessment.observed_at)

    fired: List[FiredRule] = []
    for rule in RULES:
        if rule.record_test is not None:
            supporting = [r for r in window if rule.record_test(r.assessment.values)]
        else:
            if not any(f.rule_id in rule.requires_any for f in fired):
                continue
            supporting = [r for r in window if r.assessment.values.get(rule.contact_code) is True]
        if supporting:
            fired.append(_fired(rule, supporting))

    lanes = {k: 0 for k in LANES}
    for f in fired:
        for lane, pts in f.points.items():
            lanes[lane] += pts
    total = sum(lanes.values())
    return SiteRisk(site_id, _level(total, lanes), total, lanes, tuple(fired),
                    tuple(r.record_id for r in window), excluded, start, end)


def rules_table() -> List[Dict[str, object]]:
    return [{"id": r.rule_id, "title": r.title, "points": dict(r.points), "when": r.condition_text, "why": r.why}
            for r in RULES]
