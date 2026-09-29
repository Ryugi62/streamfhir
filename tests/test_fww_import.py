"""AC-45: real citizen-science records (Earthwatch FreshWater Watch) go through the same validation, rules and FHIR."""
from streamfhir.adapters import fww_importer
from streamfhir.adapters.fhir_mapper import FhirMapper
from streamfhir.adapters.json_repository import site_from_json
from streamfhir.domain.risk import evaluate_site
from streamfhir.domain.validation import validate_record
from tests.conftest import NOW


def _feat(oid, day_ms, name, lat, lon, body="Stream", colour="Brown", surface="Foam", algae="Blue_green_scum",
          uses="Public_use_of_bank,Animal_access", litter="1_5m_from_river_edge", photo=True):
    return {"attributes": {"objectid": oid, "sample_date": day_ms, "site_name": name, "group_id": "g1",
                           "group_name": "Some citizen project", "ecological_fw_body_type": body, "optical_colour": colour,
                           "ecological_water_surface": surface, "ecological_algae": algae, "ecological_water_uses": uses,
                           "ecological_litter": litter, "chemical_nitrate": "1-2", "chemical_phosphate": "0.02-0.05",
                           "photo_url": "https://example.org/fww/%d.jpg" % oid if photo else None, "country": "Portugal"},
            "geometry": {"x": lon, "y": lat}}


PAYLOAD = {"features": [
    _feat(1, 1790161200000, "Rio Vouga, Aveiro", 40.6527, -8.5794),                        # 2026-09-23 11:00 UTC
    _feat(2, 1790161200000, "Lake", 40.6362, -8.6532, body="Lake", colour="Grey", surface="None", algae="No_algae",
          uses="None", litter="No", photo=False),
]}


def _import():
    sites = fww_importer.parse_sites(PAYLOAD, region="Coimbra region")
    records, stats = fww_importer.parse_records(PAYLOAD, {s["site_id"]: s for s in sites})
    return sites, records, stats


def test_ac45_fww_maps_visual_observations_and_keeps_the_rest_as_source_info():
    sites, records, stats = _import()
    r1, r2 = records
    assert r1["values"] == {"water-colour": "brown-turbid", "surface": "algal-scum", "litter": "some",
                            "animal-contact": True, "people-contact": False}
    assert r1["photos"] and r1["observer"] == "fww-group-g1" and r1["synthetic"] is False
    assert any("nitrate" in n.lower() and "1-2" in n for n in r1["source_info"])       # kit bands are kept, not scored
    assert r2["values"]["water-colour"] == "other" and r2["values"]["surface"] == "none"   # 'Grey' is not 'milky-grey'
    assert sites[1]["flow"] == "still" and sites[0]["name"].startswith("Coimbra region · ")
    assert stats["records"] == 2


def test_ac45_a_real_photo_backed_scum_report_with_animal_access_raises_a_corroborated_flag():
    sites, records, _ = _import()
    site = {s["site_id"]: site_from_json(s) for s in sites}
    rep = validate_record(records[0], site, NOW)
    assert rep.status == "ok"
    risk = evaluate_site(sites[0]["site_id"], [rep], as_of=None)
    assert risk.level == "high" and {f.rule_id for f in risk.fired} >= {"R3", "R7"}
    flag = FhirMapper(test_data=False, pseudonymous=True).risk_bundle(risk, site[sites[0]["site_id"]])
    f = [e["resource"] for e in flag["entry"] if e["resource"]["resourceType"] == "Flag"][0]
    assert "HTEST" not in {s["code"] for s in f["meta"]["security"]}
