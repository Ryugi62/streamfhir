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


def test_r10_no_sensitive_invertebrates_hits_environment_lane(sites, now):
    r = make_record(values={"sensitive-invertebrates": 0, "kick-sample-minutes": 2})
    risk = evaluate_site("S-TEST", reports(sites, now, r))
    assert "R10" in {f.rule_id for f in risk.fired}
    assert risk.condition_points == 2 and risk.hazard_points == 0  # chronic condition, never a Flag


def test_r10_needs_sampling_effort(sites, now):
    r = make_record(values={"sensitive-invertebrates": 0})
    assert "R10" not in {f.rule_id for f in evaluate_site("S-TEST", reports(sites, now, r)).fired}


def test_uncorroborated_hazard_asks_for_verification_not_flag(sites, now):
    r = make_record(photos=[], values={"surface": "algal-scum", "water-temperature": 24.0, "animal-contact": True})
    risk = evaluate_site("S-TEST", reports(sites, now, r))
    assert risk.hazard_points == 7 and risk.confirmed_hazard_points == 0
    assert risk.level == "verify" and risk.needs_flag is False


def test_review_records_do_not_corroborate(sites, now):
    a = make_record(record_id="A", observer="obs-1", values={"odour": "sewage"})
    b = make_record(record_id="B", observer="obs-2", gps_accuracy_m=120, photos=[], values={"odour": "sewage"})
    risk = evaluate_site("S-TEST", reports(sites, now, a, b))
    r4 = {f.rule_id: f for f in risk.fired}["R4"]
    assert r4.corroboration == "trusted report with a photo attached"


def test_rejected_record_never_counts_and_confirmed_counts(sites, now):
    r = make_record(record_id="X", photos=[], values={"dead-fish": True, "animal-contact": True})
    rep = reports(sites, now, r)
    assert evaluate_site("S-TEST", rep, decisions={"X": "reject"}).fired == ()
    confirmed = evaluate_site("S-TEST", rep, decisions={"X": "confirm"})
    assert confirmed.level == "high"
    assert {f.rule_id: f for f in confirmed.fired}["R5"].corroboration == "confirmed by a reviewer"


def test_hazard_needs_two_trusted_clear_visits_a_week_apart(sites, now):
    bad = make_record(record_id="OLD", observed_at="2026-09-16T09:00:00+00:00", values={"odour": "sewage"})
    clear1 = make_record(record_id="C1", observed_at="2026-09-18T09:00:00+00:00")
    clear2 = make_record(record_id="C2", observed_at="2026-09-26T09:00:00+00:00")
    fired = lambda *r: {f.rule_id for f in evaluate_site("S-TEST", reports(sites, now, *r), as_of=now).fired}
    assert "R4" in fired(bad, clear1)             # one clear visit is not enough for a hazard
    assert "R4" not in fired(bad, clear1, clear2)  # two, 8 days apart, clear it


def test_partial_visit_does_not_clear_a_signal(sites, now):
    bad = make_record(record_id="OLD", observed_at="2026-09-16T09:00:00+00:00", values={"surface": "algal-scum"})
    p1 = make_record(record_id="P1", observed_at="2026-09-18T09:00:00+00:00")
    p2 = make_record(record_id="P2", observed_at="2026-09-26T09:00:00+00:00")
    for p in (p1, p2):
        del p["values"]["surface"]                 # looked at colour only, not at the surface
    assert "R3" in {f.rule_id for f in evaluate_site("S-TEST", reports(sites, now, bad, p1, p2), as_of=now).fired}


def test_stream_methods_are_skipped_at_still_water_sites(sites, now):
    r = make_record(values={"channel-condition": 2, "bank-stability": 2, "riparian-zone": 2, "instream-habitat": 2,
                            "sensitive-invertebrates": 0, "kick-sample-minutes": 2})
    flowing = {f.rule_id for f in evaluate_site("S-TEST", reports(sites, now, r)).fired}
    still = {f.rule_id for f in evaluate_site("S-TEST", reports(sites, now, r), still_water=True).fired}
    assert {"R1", "R10"} <= flowing and not ({"R1", "R10"} & still)


def test_window_is_relative_to_as_of_date(sites, now):
    from datetime import timedelta
    r = make_record(values={"odour": "sewage", "people-contact": True})
    assert evaluate_site("S-TEST", reports(sites, now, r), as_of=now).level == "high"
    assert evaluate_site("S-TEST", reports(sites, now, r), as_of=now + timedelta(days=30)).level == "low"


def test_nitrate_as_n_is_converted_before_thresholds(sites, now):
    r = make_record(values={"nitrate": 12.0, "nitrate-basis": "as-N"})  # = 53 mg/L as NO3
    assert {"R2", "R9"} <= {f.rule_id for f in evaluate_site("S-TEST", reports(sites, now, r)).fired}


def test_jar_test_showing_green_algae_does_not_fire_bloom_rule(sites, now):
    r = make_record(values={"surface": "algal-scum", "bloom-check": "settles-or-strings"})
    assert "R3" not in {f.rule_id for f in evaluate_site("S-TEST", reports(sites, now, r)).fired}


def test_flag_dates_and_evidence_come_from_trusted_reports_only(sites, now):
    trusted = make_record(record_id="T", observed_at="2026-09-18T09:00:00+00:00", values={"surface": "algal-scum"})
    doubtful = make_record(record_id="D", observed_at="2026-09-27T09:00:00+00:00", gps_accuracy_m=120,
                           values={"surface": "algal-scum"})
    r3 = {f.rule_id: f for f in evaluate_site("S-TEST", reports(sites, now, trusted, doubtful)).fired}["R3"]
    assert r3.record_ids == ("T", "D") and r3.trusted_record_ids == ("T",)
    assert r3.last_seen.day == 18
