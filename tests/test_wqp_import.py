"""AC-31: real public data (US Water Quality Portal) goes through the same pipeline; oddities become review notes."""
from streamfhir.adapters.fhir_mapper import FhirMapper
from streamfhir.adapters.wqp_importer import parse_results, parse_stations
from streamfhir.domain.sites import Site
from streamfhir.domain.validation import validate_record
from tests.conftest import NOW

STATIONS = """OrganizationIdentifier,OrganizationFormalName,MonitoringLocationIdentifier,MonitoringLocationName,MonitoringLocationTypeName,LatitudeMeasure,LongitudeMeasure
ORG,Some Agency,ST-1,Test Run at Bridge,Stream,39.0,-76.9
"""
HEAD = "OrganizationIdentifier,OrganizationFormalName,ActivityIdentifier,ActivityStartDate,ActivityStartTime/Time,ActivityStartTime/TimeZoneCode,MonitoringLocationIdentifier,CharacteristicName,ResultMeasureValue,ResultMeasure/MeasureUnitCode\n"
RESULTS = HEAD + """ORG,Some Agency,A1,2026-09-20,10:41:00,EDT,ST-1,pH,7.2,None
ORG,Some Agency,A1,2026-09-20,10:41:00,EDT,ST-1,"Temperature, water",18.5,deg C
ORG,Some Agency,A1,2026-09-20,10:41:00,EDT,ST-1,"Temperature, water",18.1,deg C
ORG,Some Agency,A2,2026-09-21,,EDT,ST-1,pH,6.9,Molar
ORG,Some Agency,A2,2026-09-21,,EDT,ST-1,Nitrate,2.1,mg/L asN
"""


def _import():
    sites = parse_stations(STATIONS)
    records, stats = parse_results(RESULTS, {s["site_id"]: s for s in sites})
    return sites, records, stats


def test_one_record_per_sampling_activity_with_real_coordinates():
    sites, records, stats = _import()
    assert [r["record_id"] for r in records] == ["A1", "A2"]
    assert records[0]["observed_at"] == "2026-09-20T10:41:00-04:00"
    assert records[0]["values"] == {"ph": 7.2, "water-temperature": 18.5}
    assert records[1]["values"]["nitrate-basis"] == "as-N"
    assert stats["rows"] == 5 and stats["mapped_values"] == 4 and stats["records"] == 2
    assert records[0]["synthetic"] is False


def test_source_oddities_become_review_notes_not_silent_fixes():
    sites, records, _ = _import()
    site = {s["site_id"]: Site(s["site_id"], s["name"], s["lat"], s["lon"], s["water_body"]) for s in sites}
    r1, r2 = (validate_record(r, site, NOW) for r in records)
    assert r1.status == "review" and "more than once" in r1.issues[0].message   # duplicate temperature
    assert r2.status == "review"
    msgs = " ".join(i.message for i in r2.issues)
    assert "no sampling time" in msgs and "Molar" in msgs


def test_real_data_is_not_tagged_as_test_data_or_pseudonymised():
    sites, records, _ = _import()
    site = {s["site_id"]: Site(s["site_id"], s["name"], s["lat"], s["lon"], s["water_body"]) for s in sites}
    rep = validate_record(records[0], site, NOW)
    b = FhirMapper(test_data=False, pseudonymous=False).assessment_bundle(rep, site["ST-1"])
    obs = [e["resource"] for e in b["entry"] if e["resource"]["resourceType"] == "Observation"][0]
    assert {c["code"] for c in obs["meta"]["security"]} == {"U"}
    assert obs["performer"][0]["display"].startswith("Monitoring organisation")
