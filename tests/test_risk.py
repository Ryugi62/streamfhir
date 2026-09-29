"""AC-10, AC-11: explainable One Health risk rules."""
from streamfhir.domain.risk import HIGH, LOW, MODERATE, RULES, evaluate_site
from streamfhir.domain.validation import validate_record
from tests.conftest import make_record


def reports(sites, now, *records):
    return [validate_record(r, sites, now) for r in records]


def test_ac10_sewage_plus_people_by_two_observers_is_high_and_corroborated(sites, now):
    a = make_record(record_id="A", observer="obs-1", photos=[],
                    values={"odour": "sewage", "people-contact": True})
    b = make_record(record_id="B", observer="obs-2", photos=[], observed_at="2026-09-22T09:00:00+00:00",
                    values={"water-colour": "milky-grey", "people-contact": True})
    risk = evaluate_site("S-TEST", reports(sites, now, a, b))
    fired = {f.rule_id: f for f in risk.fired}
    assert risk.level == HIGH
    assert {"R4", "R6"} <= set(fired)
    assert fired["R4"].corroborated is True
    assert risk.needs_flag is True


def test_ac11_good_site_is_low_without_flag(sites, now):
    risk = evaluate_site("S-TEST", reports(sites, now, make_record()))
    assert risk.level == LOW
    assert risk.fired == ()
    assert risk.needs_flag is False


def test_single_report_without_photo_is_not_corroborated(sites, now):
    r = make_record(photos=[], values={"odour": "chemical"})
    risk = evaluate_site("S-TEST", reports(sites, now, r))
    fired = {f.rule_id: f for f in risk.fired}
    assert fired["R8"].corroborated is False
    assert risk.level == MODERATE


def test_blocked_records_are_excluded(sites, now):
    bad = make_record(record_id="BAD", values={"ph": 15, "odour": "sewage"})
    risk = evaluate_site("S-TEST", reports(sites, now, make_record(), bad))
    assert risk.excluded_record_ids == ("BAD",)
    assert "R4" not in {f.rule_id for f in risk.fired}


def test_old_records_fall_outside_14_day_window(sites, now):
    old = make_record(record_id="OLD", observed_at="2026-08-01T09:00:00+00:00", values={"odour": "sewage"})
    new = make_record(record_id="NEW", observed_at="2026-09-25T09:00:00+00:00")
    risk = evaluate_site("S-TEST", reports(sites, now, old, new))
    assert risk.record_ids == ("NEW",)
    assert risk.level == LOW


def test_algal_bloom_with_dogs_hits_animal_lane(sites, now):
    r = make_record(values={"surface": "algal-scum", "water-temperature": 24.0, "animal-contact": True})
    risk = evaluate_site("S-TEST", reports(sites, now, r))
    assert {"R3", "R7"} <= {f.rule_id for f in risk.fired}
    assert risk.lane_points["animal"] >= 4
    assert risk.level == HIGH


def test_nutrient_rule_uses_printed_thresholds(sites, now):
    r = make_record(values={"nitrate": 55})
    risk = evaluate_site("S-TEST", reports(sites, now, r))
    assert {"R2", "R9"} <= {f.rule_id for f in risk.fired}


def test_every_rule_is_explainable():
    ids = [r.rule_id for r in RULES]
    assert len(ids) == len(set(ids)) >= 8
    for rule in RULES:
        assert rule.condition_text and rule.why and sum(rule.points.values()) > 0
