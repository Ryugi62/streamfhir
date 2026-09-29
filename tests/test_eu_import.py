"""AC-32..AC-36: real public EU data (France Hub'Eau / Sandre) through the same pipeline, plus an EEA Waterbase coverage check.

Source vocabularies are kept: the site carries its national and EU identifiers, and each Observation carries the
EEA WISE determinand code (and the Sandre parameter code for French data) next to the StreamFHIR code.
"""
from streamfhir.adapters import hubeau_importer, waterbase_importer
from streamfhir.adapters.fhir_mapper import (EEA_OBSERVED_PROPERTY, EEA_SITE_SCHEME, SANDRE_PARAMETER,
                                             SANDRE_STATION, FhirMapper, concept_map)
from streamfhir.adapters.json_repository import site_from_json
from streamfhir.domain.validation import validate_record
from tests.conftest import NOW

HUB_STATIONS = {"data": [{"code_station": "05163000", "libelle_station": "La Garonne dans Toulouse (St-Pierre)",
                          "nom_cours_eau": "La Garonne", "latitude": 43.6019, "longitude": 1.4355,
                          "libelle_commune": "Toulouse", "type_entite_hydro": "Cours d'eau"}]}


def _hub(code, par, lib, value, unit, rem="1", qual="1", day="2026-09-08", hour="10:05:00"):
    return {"code_station": code, "date_prelevement": day, "heure_prelevement": hour, "code_parametre": par,
            "libelle_parametre": lib, "resultat": value, "symbole_unite": unit, "code_remarque": rem,
            "mnemo_remarque": "", "limite_quantification": 0.02, "code_qualification": qual,
            "libelle_qualification": {"1": "Correcte", "3": "Incertaine"}[qual], "nom_producteur_analyse": "Agence de l'eau",
            "code_producteur_analyse": "18310006400033"}


HUB_ANALYSES = {"data": [
    _hub("05163000", "1302", "Potentiel en Hydrogène (pH)", 8.1, "unité pH"),
    _hub("05163000", "1301", "Température de l'Eau", 19.4, "°C"),
    _hub("05163000", "1340", "Nitrates", 4.6, "mg(NO3)/L"),
    _hub("05163000", "1433", "Orthophosphates (PO4)", 0.02, "mg(PO4)/L", rem="10"),     # below LOQ
    _hub("05163000", "1302", "Potentiel en Hydrogène (pH)", 7.9, "unité pH", day="2026-09-22", qual="3"),
    _hub("05163000", "1340", "Nitrates", 30.0, "mg/L", day="2026-09-22"),
]}


def _hub_import():
    sites = hubeau_importer.parse_stations(HUB_STATIONS, city="Toulouse")
    records, stats = hubeau_importer.parse_analyses(HUB_ANALYSES, {s["site_id"]: s for s in sites})
    return sites, records, stats


def test_ac32_hubeau_one_record_per_sampling_with_sandre_identifiers():
    sites, records, stats = _hub_import()
    assert sites[0]["identifiers"] == [{"system": SANDRE_STATION, "value": "05163000"}]
    assert sites[0]["name"].startswith("Toulouse · ")
    assert [r["observed_at"] for r in records] == ["2026-09-08T10:05:00+02:00", "2026-09-22T10:05:00+02:00"]
    assert records[0]["values"] == {"ph": 8.1, "water-temperature": 19.4, "nitrate": 4.6, "nitrate-basis": "as-NO3"}
    assert records[0]["origin"] == "sandre" and records[0]["synthetic"] is False
    assert stats["below_quantification_limit"] == 1 and stats["records"] == 2


def test_ac32_hubeau_below_loq_is_counted_not_imported_and_uncertain_result_goes_to_a_person():
    sites, records, _ = _hub_import()
    site = {s["site_id"]: site_from_json(s) for s in sites}
    r1, r2 = (validate_record(r, site, NOW) for r in records)
    assert r1.status == "ok"                       # a result below the quantification limit is not an error
    assert "phosphate" not in records[0]["values"]
    assert records[0]["source_info"] and "below the quantification limit" in records[0]["source_info"][0]
    assert r2.status == "review" and "Incertaine" in " ".join(i.message for i in r2.issues)
    assert records[1]["values"]["nitrate-basis"] == "as-NO3"   # Sandre 1340 is the NO3- ion, even when the unit is plain mg/L


def test_ac33_hubeau_site_keeps_its_eu_monitoring_site_code_when_france_reports_it_to_the_eea():
    sites = hubeau_importer.parse_stations(HUB_STATIONS, city="Toulouse", eu_site_codes={"FR05163000"})
    assert {"system": EEA_SITE_SCHEME + "euMonitoringSiteCode", "value": "FR05163000"} in sites[0]["identifiers"]
    assert hubeau_importer.parse_stations(HUB_STATIONS, city="Toulouse", eu_site_codes=set())[0]["identifiers"] == [
        {"system": SANDRE_STATION, "value": "05163000"}]


def test_ac34_observations_carry_eu_and_national_codes():
    sites, records, _ = _hub_import()
    site = {s["site_id"]: site_from_json(s) for s in sites}
    b = FhirMapper(test_data=False, pseudonymous=False).assessment_bundle(validate_record(records[0], site, NOW), site[sites[0]["site_id"]])
    res = [e["resource"] for e in b["entry"]]
    loc = [r for r in res if r["resourceType"] == "Location"][0]
    assert {"system": SANDRE_STATION, "value": "05163000"} in loc["identifier"]
    obs = {c["code"]: r for r in res if r["resourceType"] == "Observation" for c in r["code"]["coding"]}
    assert {"system": EEA_OBSERVED_PROPERTY, "code": "EEA_3152-01-0", "display": "pH"} in obs["ph"]["code"]["coding"]
    assert {"system": SANDRE_PARAMETER, "code": "1302", "display": "Potentiel en Hydrogène (pH)"} in obs["ph"]["code"]["coding"]
    assert {"9480-5", "CAS_14797-55-8", "1340"} <= {c["code"] for c in obs["nitrate"]["code"]["coding"]}


def test_ac34_citizen_records_get_eu_codes_but_no_national_codes_and_nitrate_as_n_gets_none():
    from tests.conftest import GOOD, SITES, make_record
    rep = validate_record(GOOD, SITES, NOW)
    codes = {(c["system"], c["code"]) for e in FhirMapper().assessment_bundle(rep, SITES["S-TEST"])["entry"]
             for c in e["resource"].get("code", {}).get("coding", [])}
    assert (EEA_OBSERVED_PROPERTY, "CAS_14797-55-8") in codes and not [c for c in codes if c[0] == SANDRE_PARAMETER]
    rec = make_record(values=dict(GOOD["values"], **{"nitrate-basis": "as-N"}))
    codes = {c["code"] for e in FhirMapper().assessment_bundle(validate_record(rec, SITES, NOW), SITES["S-TEST"])["entry"]
             for c in e["resource"].get("code", {}).get("coding", [])}
    assert "CAS_14797-55-8" not in codes and "9480-5" not in codes


def test_ac36_eea_coverage_summary_per_city():
    rows = [{"monitoringSiteIdentifier": "PT12H02", "observedPropertyDeterminandCode": "EEA_3152-01-0", "phenomenonTimeReferenceYear": 2009, "resultNumberOfSamples": 12},
            {"monitoringSiteIdentifier": "PT12H02", "observedPropertyDeterminandCode": "CAS_14797-55-8", "phenomenonTimeReferenceYear": 2011, "resultNumberOfSamples": 4},
            {"monitoringSiteIdentifier": "PT99", "observedPropertyDeterminandCode": "EEA_3121-01-5", "phenomenonTimeReferenceYear": 2020, "resultNumberOfSamples": 3},
            {"monitoringSiteIdentifier": "PT99", "observedPropertyDeterminandCode": "CAS_7440-43-9", "phenomenonTimeReferenceYear": 2023, "resultNumberOfSamples": 3}]
    s = waterbase_importer.coverage(rows)
    assert s["sites_with_data"] == 2 and s["latest_year"] == {"ph": 2009, "nitrate": 2011, "water-temperature": 2020}
    assert s["samples"] == 19


def test_ac35_concept_map_matches_what_the_mapper_emits():
    cm = concept_map()
    assert cm["resourceType"] == "ConceptMap" and cm["status"] == "draft"
    targets = {(g["target"], el["code"], t["code"]) for g in cm["group"] for el in g["element"] for t in el["target"]}
    assert (EEA_OBSERVED_PROPERTY, "ph", "EEA_3152-01-0") in targets
    assert (SANDRE_PARAMETER, "phosphate", "1433") in targets
    nitrate = [t for g in cm["group"] if g["target"] == EEA_OBSERVED_PROPERTY for el in g["element"]
               if el["code"] == "nitrate" for t in el["target"]][0]
    assert nitrate["dependsOn"][0]["value"] == "as-NO3"


def _hub_bundle(i=0):
    sites, records, _ = _hub_import()
    site = {s["site_id"]: site_from_json(s) for s in sites}
    b = FhirMapper(test_data=False, pseudonymous=False).assessment_bundle(validate_record(records[i], site, NOW), site[sites[0]["site_id"]])
    return [e["resource"] for e in b["entry"]]


def test_ac40_agency_records_are_laboratory_data_with_source_provenance_not_citizen_wording():
    res = _hub_bundle()
    obs = [r for r in res if r["resourceType"] == "Observation"]
    cats = {c["code"] for r in obs for cat in r["category"] for c in cat["coding"]}
    assert cats == {"laboratory"}
    perf = obs[0]["performer"][0]
    assert perf["identifier"] == {"system": "https://id.eaufrance.fr/int", "value": "18310006400033"}
    assert perf["display"] == "Agence de l'eau" and perf["type"] == "Organization"
    prov = [r for r in res if r["resourceType"] == "Provenance"][0]
    assert "Hub'Eau" in prov["text"]["div"] and "Hub'Eau" in prov["entity"][0]["what"]["display"]
    text = " ".join(r["text"]["div"] for r in res if "text" in r).lower()
    assert "citizen" not in text


def test_ac41_result_below_the_quantification_limit_is_a_comparator_observation():
    res = _hub_bundle()
    po4 = [r for r in res if r["resourceType"] == "Observation" and r["code"]["coding"][0]["code"] == "phosphate"][0]
    assert po4["valueQuantity"]["comparator"] == "<" and po4["valueQuantity"]["value"] == 0.02
    assert "quantification limit" in po4["note"][0]["text"]
    assert {c["code"] for c in po4["code"]["coding"]} == {"phosphate", "1433"}      # EEA reports phosphate as P: not mapped


def test_ac42_agency_lab_values_count_as_agency_results_not_unverified_reports():
    from streamfhir.domain.risk import evaluate_site
    sites, records, _ = _hub_import()
    site = {s["site_id"]: site_from_json(s) for s in sites}
    records[0]["values"]["nitrate"] = 31.0                          # a 'Correcte' (trusted) agency result above 25
    reps = [validate_record(records[0], site, NOW)]
    risk = evaluate_site(sites[0]["site_id"], reps, as_of=None)
    r2 = [f for f in risk.fired if f.rule_id == "R2"][0]
    assert r2.corroborated and "agency" in r2.corroboration


def test_ac43_api_says_not_assessed_instead_of_low_when_no_hazard_input_was_observed():
    from streamfhir.adapters.presenter import overview_json
    from streamfhir.application.use_cases import SiteOverview
    from streamfhir.domain.risk import evaluate_site
    sites, records, _ = _hub_import()
    site = {s["site_id"]: site_from_json(s) for s in sites}
    reps = [validate_record(r, site, NOW) for r in records]
    risk = evaluate_site(sites[0]["site_id"], reps, as_of=None)
    ov = SiteOverview(site[sites[0]["site_id"]], risk, reps, None, {}, {})
    assert overview_json([ov])["sites"][0]["risk"]["level"] == "not-assessed"


def test_ac35_eea_phosphate_is_inexact_because_the_eea_reports_it_as_p():
    cm = concept_map()
    t = [t for g in cm["group"] if g["target"] == EEA_OBSERVED_PROPERTY for el in g["element"]
         if el["code"] == "phosphate" for t in el["target"]][0]
    assert t["equivalence"] == "inexact" and "3.066" in t["comment"]


def test_ac44_interop_demo_puts_an_agency_and_a_citizen_nitrate_under_one_eea_code():
    """AC-44: `interop-demo` sends one real agency sampling and one clearly synthetic citizen check at the same real
    station; both nitrate Observations carry the EEA code, the citizen one is survey + HTEST, the agency one laboratory."""
    import os
    from streamfhir.infrastructure.cli import interop_bundles
    from streamfhir.infrastructure.container import build_service
    svc = build_service(os.path.join(os.path.dirname(__file__), "..", "data", "real-eu-toulouse"))
    agency, citizen = interop_bundles(svc, "FR-05157550")

    def nitrate(b):
        return [e["resource"] for e in b["entry"] if e["resource"]["resourceType"] == "Observation"
                and e["resource"]["code"]["coding"][0]["code"] == "nitrate"][0]
    a, c = nitrate(agency), nitrate(citizen)
    for o in (a, c):
        assert {"system": EEA_OBSERVED_PROPERTY, "code": "CAS_14797-55-8", "display": "Nitrate"} in o["code"]["coding"]
    assert a["category"][0]["coding"][0]["code"] == "laboratory" and c["category"][0]["coding"][0]["code"] == "survey"
    assert "HTEST" in {s["code"] for s in c["meta"]["security"]} and "HTEST" not in {s["code"] for s in a["meta"]["security"]}
    loc = lambda b: [e for e in b["entry"] if e["resource"]["resourceType"] == "Location"][0]["request"]["ifNoneExist"]
    assert loc(agency) == loc(citizen)            # same station, conditional create -> one Location on the server
