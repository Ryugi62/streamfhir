"""AC-1..AC-6: validation with human-in-the-loop flags (never silent correction)."""
from streamfhir.domain.validation import BLOCKED, OK, REVIEW, validate_record
from tests.conftest import make_record


def codes(report):
    return {i.code for i in report.issues}


def test_ac1_complete_plausible_record_is_ok(sites, now):
    report = validate_record(make_record(), sites, now)
    assert report.status == OK
    assert report.issues == ()
    assert report.assessment.values["ph"] == 7.4


def test_ac2_missing_observer_blocks(sites, now):
    r = make_record()
    del r["observer"]
    report = validate_record(r, sites, now)
    assert report.status == BLOCKED
    assert "missing-field" in codes(report)


def test_ac2_missing_coordinates_blocks(sites, now):
    r = make_record()
    del r["lat"]
    assert validate_record(r, sites, now).status == BLOCKED


def test_ac2_future_timestamp_blocks(sites, now):
    report = validate_record(make_record(observed_at="2030-06-01T10:00:00+00:00"), sites, now)
    assert report.status == BLOCKED
    assert "future-timestamp" in codes(report)


def test_invalid_timestamp_blocks(sites, now):
    report = validate_record(make_record(observed_at="yesterday"), sites, now)
    assert report.status == BLOCKED
    assert "invalid-timestamp" in codes(report)


def test_ac3_impossible_ph_blocks(sites, now):
    report = validate_record(make_record(values={"ph": 15}), sites, now)
    assert report.status == BLOCKED
    assert any(i.field == "values.ph" and i.code == "out-of-range" for i in report.issues)


def test_ac3_implausible_ph_needs_review_and_value_is_kept(sites, now):
    report = validate_record(make_record(values={"ph": 4.2}), sites, now)
    assert report.status == REVIEW
    assert "implausible" in codes(report)
    assert report.assessment.values["ph"] == 4.2  # never silently corrected


def test_ac4_dead_fish_without_photo_needs_review(sites, now):
    report = validate_record(make_record(photos=[], values={"dead-fish": True}), sites, now)
    assert report.status == REVIEW
    assert "photo-needed" in codes(report)


def test_ac4_algal_scum_with_photo_is_ok(sites, now):
    report = validate_record(make_record(values={"surface": "algal-scum"}), sites, now)
    assert report.status == OK


def test_ac5_location_far_from_site_needs_review(sites, now):
    report = validate_record(make_record(lat=40.2190), sites, now)  # ~440 m north
    assert report.status == REVIEW
    assert "location-mismatch" in codes(report)


def test_poor_gps_accuracy_needs_review(sites, now):
    report = validate_record(make_record(gps_accuracy_m=120), sites, now)
    assert report.status == REVIEW
    assert "poor-gps" in codes(report)


def test_ac6_unknown_indicator_blocks(sites, now):
    report = validate_record(make_record(values={"mercury": 3}), sites, now)
    assert report.status == BLOCKED
    assert "unknown-indicator" in codes(report)


def test_ac6_score_outside_1_to_10_blocks(sites, now):
    report = validate_record(make_record(values={"bank-stability": 11}), sites, now)
    assert report.status == BLOCKED


def test_invalid_category_answer_blocks(sites, now):
    report = validate_record(make_record(values={"odour": "roses"}), sites, now)
    assert report.status == BLOCKED
    assert "invalid-answer" in codes(report)


def test_unknown_site_blocks(sites, now):
    report = validate_record(make_record(site_id="S-NOPE"), sites, now)
    assert report.status == BLOCKED
    assert "unknown-site" in codes(report)


def test_every_issue_has_plain_message(sites, now):
    report = validate_record(make_record(values={"ph": 4.2}, gps_accuracy_m=120), sites, now)
    assert all(len(i.message) > 20 for i in report.issues)


def test_invertebrate_count_above_3_blocks(sites, now):
    report = validate_record(make_record(values={"sensitive-invertebrates": 4}), sites, now)
    assert report.status == BLOCKED


def test_nitrate_without_basis_needs_review(sites, now):
    r = make_record()
    del r["values"]["nitrate-basis"]
    report = validate_record(r, sites, now)
    assert report.status == REVIEW and "nitrate-basis-missing" in codes(report)
