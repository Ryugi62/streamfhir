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
    assert len(obs) == 1 + 17  # panel + 17 indicators
    assert all(o["subject"]["reference"] == loc_entry["fullUrl"] for o in obs)
    # idempotent re-send + review propagation: every Observation and Media is a conditional update
    for e in b["entry"]:
        if e["resource"]["resourceType"] in ("Observation", "Media"):
            rt = e["resource"]["resourceType"]
            assert e["request"]["method"] == "PUT" and e["request"]["url"].startswith(rt + "?identifier=")
            assert "#" not in e["request"]["url"]
        if e["resource"]["resourceType"] == "Provenance":
            assert e["request"]["method"] == "PUT" and e["request"]["url"] == "Provenance/" + e["resource"]["id"]


def test_ac8_panel_has_members_and_provenance_targets_every_observation(sites, now):
    report = validate_record(make_record(), sites, now)
    b = mapper().assessment_bundle(report, sites["S-TEST"])
    urls = {e["resource"]["resourceType"] + e["fullUrl"]: e["fullUrl"] for e in b["entry"]}
    obs_urls = {e["fullUrl"] for e in b["entry"] if e["resource"]["resourceType"] == "Observation"}
    panel = [o for o in resources(b, "Observation") if "hasMember" in o][0]
    assert len(panel["hasMember"]) == 17
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
    assert {c["code"] for c in obs[0]["meta"]["security"]} == {"HTEST", "U", "PSEUDED"}


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
    from streamfhir.adapters.fhir_mapper import conformance_resources
    for cs in [r for r in conformance_resources() if r["resourceType"] == "CodeSystem"]:
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


def test_photo_is_evidence_only_for_visual_claims(sites, now):
    report = validate_record(make_record(), sites, now)
    obs = resources(mapper().assessment_bundle(report, sites["S-TEST"]), "Observation")
    by_code = {o["code"]["coding"][0]["code"]: o for o in obs}
    assert "derivedFrom" in by_code["surface"] and "derivedFrom" in by_code["stream-assessment"]
    assert "derivedFrom" not in by_code["ph"] and "derivedFrom" not in by_code["water-temperature"]


def test_loinc_nitrate_only_when_expressed_as_no3(sites, now):
    m = mapper()
    as_no3 = resources(m.assessment_bundle(validate_record(make_record(), sites, now), sites["S-TEST"]), "Observation")
    as_n = resources(m.assessment_bundle(validate_record(make_record(values={"nitrate-basis": "as-N"}), sites, now),
                                         sites["S-TEST"]), "Observation")
    no3 = [o for o in as_no3 if o["code"]["coding"][0]["code"] == "nitrate"][0]
    n = [o for o in as_n if o["code"]["coding"][0]["code"] == "nitrate"][0]
    assert {"system": "http://loinc.org", "code": "9480-5", "display": "Nitrate [Mass/volume] in Water"} in no3["code"]["coding"]
    assert all(c["system"] != "http://loinc.org" for c in n["code"]["coding"])


def test_reviewer_decisions_change_status_and_provenance(sites, now):
    report = validate_record(make_record(values={"ph": 4.2}), sites, now)
    ok = mapper().assessment_bundle(report, sites["S-TEST"], "confirm", "site-visit")
    assert {o["status"] for o in resources(ok, "Observation")} == {"final"}
    roles = [a["type"]["coding"][0]["code"] for a in resources(ok, "Provenance")[0]["agent"]]
    assert roles == ["author", "assembler", "verifier"]
    assert "site-visit" in resources(ok, "Provenance")[0]["agent"][2]["who"]["display"]
    bad = mapper().assessment_bundle(report, sites["S-TEST"], "reject")
    assert {o["status"] for o in resources(bad, "Observation")} == {"entered-in-error"}


def test_flag_is_traceable_expiring_and_updatable(sites, now):
    r = make_record(values={"odour": "sewage", "people-contact": True})
    risk = evaluate_site("S-TEST", [validate_record(r, sites, now)], as_of=now)
    rb = mapper().risk_bundle(risk, sites["S-TEST"])
    flag_entry = [e for e in rb["entry"] if e["resource"]["resourceType"] == "Flag"][0]
    flag = flag_entry["resource"]
    assert flag_entry["request"]["method"] == "PUT" and flag_entry["request"]["url"].startswith("Flag?identifier=")
    assert flag["extension"][0]["url"] == "http://hl7.org/fhir/StructureDefinition/flag-detail"
    assert flag["extension"][0]["valueReference"]["identifier"]["value"] == "T-1"
    assert flag["period"]["end"] > flag["period"]["start"]
    prov = resources(rb, "Provenance")[0]
    assert prov["target"][0]["reference"] == flag_entry["fullUrl"]


def test_stand_down_sets_same_flag_inactive(sites, now):
    risk = evaluate_site("S-TEST", [validate_record(make_record(), sites, now)], as_of=now)
    sd = mapper().stand_down_bundle(risk, sites["S-TEST"], "2026-09-20T09:30:00+01:00")
    flag_entry = [e for e in sd["entry"] if e["resource"]["resourceType"] == "Flag"][0]
    assert flag_entry["resource"]["status"] == "inactive"
    assert flag_entry["resource"]["period"]["start"] == "2026-09-20T09:30:00+01:00"  # original start kept
    assert flag_entry["request"]["url"].endswith("|S-TEST")


def test_flag_cites_fired_hazard_rules_as_codes(sites, now):
    from streamfhir.adapters.fhir_mapper import CS_RULE, EXT_FLAG_RULE, conformance_resources
    r = make_record(values={"odour": "sewage", "people-contact": True})
    risk = evaluate_site("S-TEST", [validate_record(r, sites, now)], as_of=now)
    flag = resources(mapper().risk_bundle(risk, sites["S-TEST"]), "Flag")[0]
    rule_ext = [x for x in flag["extension"] if x["url"] == EXT_FLAG_RULE]
    codes = [x["valueCodeableConcept"]["coding"][0] for x in rule_ext]
    expected = {f.rule_id for f in risk.fired if f.kind == "hazard" and f.corroborated}
    assert {"R4", "R6"} <= expected
    assert {c["code"] for c in codes} == expected
    assert all(c["system"] == CS_RULE for c in codes)
    assert flag["extension"][0]["url"] == "http://hl7.org/fhir/StructureDefinition/flag-detail"
    sd = [x for x in conformance_resources() if x.get("url") == EXT_FLAG_RULE][0]
    assert sd["type"] == "Extension" and sd["context"] == [{"type": "element", "expression": "Flag"}]
    with open(os.path.join(ROOT, "fhir", "StructureDefinition-%s.json" % sd["id"])) as fh:
        assert json.load(fh) == sd, "run: python3 -m streamfhir build-fhir"


def test_stand_down_flag_cites_no_rules(sites, now):
    from streamfhir.adapters.fhir_mapper import EXT_FLAG_RULE
    risk = evaluate_site("S-TEST", [validate_record(make_record(), sites, now)], as_of=now)
    sd = mapper().stand_down_bundle(risk, sites["S-TEST"], "2026-09-20T09:30:00+01:00")
    flag = resources(sd, "Flag")[0]
    assert all(x["url"] != EXT_FLAG_RULE for x in flag.get("extension", []))


def test_flag_rule_extension_binds_to_hazard_rules_only():
    from streamfhir.adapters.fhir_mapper import CS_RULE, EXT_FLAG_RULE, VS_HAZARD_RULE, conformance_resources
    from streamfhir.domain.risk import HAZARD, RULES
    res = {r.get("url"): r for r in conformance_resources()}
    vs = res[VS_HAZARD_RULE]
    codes = {c["code"] for c in vs["compose"]["include"][0]["concept"]}
    assert vs["compose"]["include"][0]["system"] == CS_RULE
    assert codes == {r.rule_id for r in RULES if r.kind == HAZARD}
    value = [e for e in res[EXT_FLAG_RULE]["differential"]["element"] if e["id"] == "Extension.value[x]"][0]
    assert value["binding"] == {"strength": "required", "valueSet": VS_HAZARD_RULE}
    with open(os.path.join(ROOT, "fhir", "ValueSet-%s.json" % vs["id"])) as fh:
        assert json.load(fh) == vs, "run: python3 -m streamfhir build-fhir"


def test_ac39_flag_profile_constrains_subject_category_period_and_rule_citation():
    """AC-39: every Flag claims the stream-site-flag profile; the profile requires a Location subject, the safety
    category, an expiry, and (invariant ssf-1) at least one cited hazard rule while active."""
    import json, os
    from streamfhir.adapters.fhir_mapper import PROFILE_FLAG, conformance_resources
    sd = [r for r in conformance_resources() if r.get("url") == PROFILE_FLAG][0]
    els = {e["id"]: e for e in sd["differential"]["element"]}
    assert els["Flag.subject"]["type"][0]["targetProfile"] == ["http://hl7.org/fhir/StructureDefinition/Location"]
    assert els["Flag.category:safety"]["min"] == 1 and els["Flag.period.end"]["min"] == 1
    assert "status != 'active'" in els["Flag"]["constraint"][0]["expression"]
    with open(os.path.join(os.path.dirname(__file__), "..", "fhir", "StructureDefinition-stream-site-flag.json"), encoding="utf-8") as fh:
        assert json.load(fh) == sd, "run: python3 -m streamfhir build-fhir"


def test_ac47_a_flag_whose_expiry_has_passed_is_inactive_and_nitrate_states_its_basis_in_the_unit():
    """AC-47: Flag.status follows period.end at build time; every nitrate Observation carries its basis in the UCUM unit."""
    from datetime import datetime, timezone
    from streamfhir.adapters.fhir_mapper import FhirMapper
    from streamfhir.domain.risk import evaluate_site
    from streamfhir.domain.validation import validate_record
    from tests.conftest import NOW, SITES, make_record
    recs = [make_record(record_id="F-%d" % i, observer="obs-%d" % i, observed_at="2026-09-20T09:30:00+01:00",
                        values={"surface": "algal-scum", "animal-contact": True}) for i in (1, 2)]
    reps = [validate_record(r, SITES, NOW) for r in recs]
    risk = evaluate_site("S-TEST", reps, as_of=NOW)
    later = FhirMapper(now_fn=lambda: datetime(2026, 11, 1, tzinfo=timezone.utc)).risk_bundle(risk, SITES["S-TEST"])
    now = FhirMapper(now_fn=lambda: NOW).risk_bundle(risk, SITES["S-TEST"])
    status = lambda b: [e["resource"] for e in b["entry"] if e["resource"]["resourceType"] == "Flag"][0]["status"]
    assert status(now) == "active" and status(later) == "inactive"
    as_n = make_record(values={"nitrate": 3, "nitrate-basis": "as-N"})
    for rec, unit in ((make_record(), "mg{NO3}/L"), (as_n, "mg{N}/L")):
        b = FhirMapper().assessment_bundle(validate_record(rec, SITES, NOW), SITES["S-TEST"])
        obs = [e["resource"] for e in b["entry"] if e["resource"]["resourceType"] == "Observation"
               and e["resource"]["code"]["coding"][0]["code"] == "nitrate"][0]
        assert obs["valueQuantity"]["code"] == unit
