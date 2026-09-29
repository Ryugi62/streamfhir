"""AC-45: real citizen-science records (Earthwatch FreshWater Watch) go through the same validation, rules and FHIR."""
from streamfhir.adapters import fww_importer
from streamfhir.adapters.fhir_mapper import FhirMapper
from streamfhir.adapters.json_repository import site_from_json
from streamfhir.domain.risk import evaluate_site
from streamfhir.domain.validation import validate_record
from tests.conftest import NOW


def _feat(oid, day_ms, name, lat, lon, body="Stream", colour="Brown", surface="Foam", algae="Blue_green_scum",
          uses="Public_use_of_bank,Animal_access", litter="1_5m_from_river_edge", photo=True, flow="Steady",
          sources="Outfall_pipe_currently_discharging", time="15:00"):
    return {"attributes": {"objectid": oid, "sample_date": day_ms, "sample_time": time, "site_name": name, "group_id": "g1",
                           "hydrological_water_flow": flow, "ecological_pollution_sources": sources,
                           "group_name": "Some citizen project", "ecological_fw_body_type": body, "optical_colour": colour,
                           "ecological_water_surface": surface, "ecological_algae": algae, "ecological_water_uses": uses,
                           "ecological_litter": litter, "chemical_nitrate": "1-2", "chemical_phosphate": "0.02-0.05",
                           "photo_url": "https://example.org/fww/%d.jpg" % oid if photo else None, "country": "Portugal"},
            "geometry": {"x": lon, "y": lat}}


PAYLOAD = {"features": [
    _feat(1, 1790161200000, "Rio Vouga, Aveiro", 40.6527, -8.5794),                        # 2026-09-23 11:00 UTC
    _feat(2, 1790161200000, "Lake_city park", 40.6362, -8.6532, body="Lake", colour="Grey", surface="None", algae="No_algae",
          uses="None", litter="No", photo=False, flow="Still", sources="None"),
]}


def _import():
    sites = fww_importer.parse_sites(PAYLOAD, region="Central Portugal")
    records, stats = fww_importer.parse_records(PAYLOAD, {s["site_id"]: s for s in sites}, tz="Europe/Lisbon")
    return sites, records, stats


def test_ac45_fww_maps_visual_observations_and_keeps_the_rest_as_source_info():
    sites, records, stats = _import()
    r1, r2 = records
    assert r1["values"] == {"water-colour": "brown-turbid", "surface": "algal-scum", "litter": "some", "people-contact": False}
    assert r1["observed_at"] == "2026-09-23T15:00:00+01:00"                           # sampling time kept, local time
    assert r1["photos"] and r1["observer"] == "fww-group-g1" and r1["synthetic"] is False
    info = " ".join(r1["source_info"])
    assert "as NO3-N" in info and "as PO4-P" in info and "animals can reach the water" in info   # not an observed contact
    assert r2["values"]["water-colour"] == "other" and r2["values"]["surface"] == "none"   # 'Grey' is not 'milky-grey'
    assert sites[1]["flow"] == "still" and sites[0]["name"].startswith("Central Portugal · ")
    assert sites[1]["name"] == "Central Portugal · Lake city park"
    assert stats["records"] == 2


def test_ac45_implausible_scum_and_outfall_go_to_a_person_and_a_photo_alone_does_not_raise_a_flag():
    sites, records, _ = _import()
    site = {s["site_id"]: site_from_json(s) for s in sites}
    rep = validate_record(records[0], site, NOW)
    msgs = " ".join(i.message for i in rep.issues)
    assert rep.status == "review" and "flowing water" in msgs and "outfall" in msgs.lower()
    assert "StreamFHIR plausibility check" in msgs and "Blue_green_scum" in msgs     # our inference is labelled as ours
    risk = evaluate_site(sites[0]["site_id"], [rep], as_of=None, photo_corroborates=False)
    assert risk.level == "verify" and not risk.needs_flag
    # a reviewer who checked the photo and confirms makes it count
    risk2 = evaluate_site(sites[0]["site_id"], [rep], as_of=None, decisions={rep.record_id: "confirm"}, photo_corroborates=False)
    assert risk2.level == "high"
    flag = FhirMapper(test_data=False, pseudonymous=True, now_fn=lambda: NOW).risk_bundle(risk2, site[sites[0]["site_id"]])
    f = [e["resource"] for e in flag["entry"] if e["resource"]["resourceType"] == "Flag"][0]
    assert f["status"] == "active"      # period ends 14 days after 23 Sep 2026, i.e. after the mapper's clock in this test
