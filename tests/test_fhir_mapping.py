"""AC-7, AC-8, AC-9, AC-12: FHIR R4 mapping."""
import itertools
import json
import os

from streamfhir.adapters.fhir_mapper import (CS_ANSWER, CS_INDICATOR, CS_RISK, FhirMapper,
                                             codesystem_resources)
from streamfhir.domain.risk import evaluate_site
from streamfhir.domain.validation import validate_record
from tests.conftest import ROOT, make_record


def mapper():
    counter = itertools.count(1)
    return FhirMapper(id_factory=lambda: "00000000-0000-4000-8000-%012d" % next(counter))


def resources(bundle, rtype):
    return [e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == rtype]


def test_ac8_bundle_is_transaction_with_conditional_location(sites, now):
    report = validate_record(make_record(), sites, now)
    b = mapper().assessment_bundle(report, sites["S-TEST"])
    assert b["resourceType"] == "Bundle" and b["type"] == "transaction"
    loc_entry = [e for e in b["entry"] if e["resource"]["resourceType"] == "Location"][0]
    assert loc_entry["request"]["method"] == "POST"
    assert loc_entry["request"]["ifNoneExist"].startswith("identifier=")
    assert loc_entry["fullUrl"].startswith("urn:uuid:")
    obs = resources(b, "Observation")
    assert len(obs) == 1 + 16  # panel + 16 indicators
    assert all(o["subject"]["reference"] == loc_entry["fullUrl"] for o in obs)


def test_ac8_panel_has_members_and_provenance_targets_every_observation(sites, now):
    report = validate_record(make_record(), sites, now)
    b = mapper().assessment_bundle(report, sites["S-TEST"])
    urls = {e["resource"]["resourceType"] + e["fullUrl"]: e["fullUrl"] for e in b["entry"]}
    obs_urls = {e["fullUrl"] for e in b["entry"] if e["resource"]["resourceType"] == "Observation"}
    panel = [o for o in resources(b, "Observation") if "hasMember" in o][0]
    assert len(panel["hasMember"]) == 16
    prov = resources(b, "Provenance")[0]
    assert {t["reference"] for t in prov["target"]} >= obs_urls
    assert prov["agent"][0]["who"]["identifier"]["value"] == "obs-aaa"
    assert urls  # sanity


def test_quantity_uses_ucum_and_scores_are_integers(sites, now):
    report = validate_record(make_record(), sites, now)
    obs = resources(mapper().assessment_bundle(report, sites["S-TEST"]), "Observation")
    by_code = {o["code"]["coding"][0]["code"]: o for o in obs}
    temp = by_code["water-temperature"]["valueQuantity"]
    assert temp == {"value": 15.2, "unit": "°C", "system": "http://unitsofmeasure.org", "code": "Cel"}
    assert by_code["bank-stability"]["valueInteger"] == 7
    assert by_code["dead-fish"]["valueBoolean"] is False
    assert by_code["odour"]["valueCodeableConcept"]["coding"][0]["code"] == "odour-none"


def test_ac7_review_record_is_preliminary_with_note(sites, now):
    report = validate_record(make_record(values={"ph": 4.2}), sites, now)
    obs = resources(mapper().assessment_bundle(report, sites["S-TEST"]), "Observation")
    assert all(o["status"] == "preliminary" for o in obs)
    ph = [o for o in obs if o["code"]["coding"][0]["code"] == "ph"][0]
    assert "4.2" in ph["note"][0]["text"]
    assert ph["valueQuantity"]["value"] == 4.2


def test_ok_record_is_final_and_tagged_as_test_data(sites, now):
    report = validate_record(make_record(), sites, now)
    b = mapper().assessment_bundle(report, sites["S-TEST"])
    obs = resources(b, "Observation")
    assert {o["status"] for o in obs} == {"final"}
    assert obs[0]["meta"]["security"][0]["code"] == "HTEST"


def test_blocked_record_is_not_mapped(sites, now):
    report = validate_record(make_record(values={"ph": 15}), sites, now)
    assert mapper().assessment_bundle(report, sites["S-TEST"]) is None


def _codings(obj):
    if isinstance(obj, dict):
        if "system" in obj and "code" in obj:
            yield obj["system"], obj["code"]
        for v in obj.values():
            yield from _codings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _codings(v)


def _published_codes():
    codes = set()
    for name in os.listdir(os.path.join(ROOT, "fhir")):
        if name.startswith("CodeSystem-"):
            with open(os.path.join(ROOT, "fhir", name)) as fh:
                cs = json.load(fh)
            codes |= {(cs["url"], c["code"]) for c in cs["concept"]}
    return codes


def test_ac9_every_streamfhir_code_is_published(sites, now):
    published = _published_codes()
    m = mapper()
    report = validate_record(make_record(values={"surface": "algal-scum", "water-temperature": 24.0}), sites, now)
    b = m.assessment_bundle(report, sites["S-TEST"])
    rb = m.risk_bundle(evaluate_site("S-TEST", [report]), sites["S-TEST"])
    ours = {(s, c) for s, c in _codings([b, rb]) if s in (CS_INDICATOR, CS_ANSWER, CS_RISK)}
    assert ours and ours <= published


def test_published_codesystems_match_catalogue():
    for cs in codesystem_resources():
        with open(os.path.join(ROOT, "fhir", "CodeSystem-%s.json" % cs["id"])) as fh:
            assert json.load(fh) == cs, "run: python3 -m streamfhir build-fhir"


def test_ac12_high_site_emits_exactly_one_flag_on_location(sites, now):
    r = make_record(values={"odour": "sewage", "people-contact": True})
    risk = evaluate_site("S-TEST", [validate_record(r, sites, now)])
    rb = mapper().risk_bundle(risk, sites["S-TEST"])
    flags = resources(rb, "Flag")
    assert len(flags) == 1
    loc_url = [e["fullUrl"] for e in rb["entry"] if e["resource"]["resourceType"] == "Location"][0]
    assert flags[0]["subject"]["reference"] == loc_url
    assert "R4" in flags[0]["code"]["text"] and "R6" in flags[0]["code"]["text"]
    assert flags[0]["status"] == "active"


def test_low_site_emits_no_flag(sites, now):
    risk = evaluate_site("S-TEST", [validate_record(make_record(), sites, now)])
    assert mapper().risk_bundle(risk, sites["S-TEST"]) is None
