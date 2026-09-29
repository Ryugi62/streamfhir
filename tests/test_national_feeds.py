"""AC-46: the same importer shape reads two more OneAquaHealth cities' open agency feeds (Ghent VMM, Benevento ARPAC)."""
from streamfhir.adapters import national_feeds
from streamfhir.adapters.json_repository import site_from_json
from streamfhir.domain.validation import validate_record
from tests.conftest import NOW

VMM = """Sample Point\tDatum\tSample ID\tTijdstip\tParameter Symbool\tParameter omschrijving\tTeken\tResultaat\tEenheid
OW172100\t2026-09-08\t1\t10:15:00\tNO3-\tNitraat\t=\t1,67\tmgN/L
OW172100\t2026-09-08\t1\t10:15:00\tpH\tZuurtegraad\t=\t7,6\t-
OW172100\t2026-09-08\t1\t10:15:00\tT\tTemperatuur\t=\t18,2\t°C
OW172100\t2026-09-08\t1\t10:15:00\toPO4 f\tOrthofosfaat\t=\t0,11\tmgP/L
OW172100\t2026-09-08\t1\t10:15:00\tAg o\tZilver\t<\t0,05\tµg/L
OW172100\t2024-01-08\t0\t10:15:00\tpH\tZuurtegraad\t=\t7,1\t
"""
ARPAC_ST = """_id,codice_sito,latitudine_n,longitudine_e,fiume,comune,localita,provincia,perennita
158,S8,41.1321842189,14.7649688793,SABATO,Benevento,Ponte Leproso,BN,P
"""
ARPAC = """cod_staz,Desc Staz,data,inq,Desc inq,Valore mis,um
S8,PONTE LEPROSO,01/10/2025 10:15,IC164,AZOTO NITRICO (COME N),3,mg/L
S8,PONTE LEPROSO,01/10/2025 10:15,IC26,PH,7.9,unità pH
S8,PONTE LEPROSO,01/10/2025 10:15,MI32,TEMPERATURA,16.4,°C
S8,PONTE LEPROSO,01/10/2025 10:15,P_TOT,FOSFORO TOTALE,<30,µg/L
"""
VMM_STATIONS = [{"code": "OW172100", "name": "Bovenschelde in Gent", "lat": 51.0016, "lon": 3.72403}]


def test_ac46_vmm_ghent_samples_become_laboratory_records_with_nitrate_as_n():
    sites = national_feeds.vmm_sites(VMM_STATIONS, city="Ghent")
    records, stats = national_feeds.parse_vmm({"OW172100": VMM}, {s["site_id"]: s for s in sites}, since="2025-09-01")
    assert [r["record_id"] for r in records] == ["BE-VMM-OW172100-2026-09-08T1015"]
    r = records[0]
    assert r["values"] == {"nitrate": 1.67, "nitrate-basis": "as-N", "ph": 7.6, "water-temperature": 18.2,
                           "phosphate": 0.11, "phosphate-basis": "as-P"}
    assert r["origin"] == "vmm" and r["observer_name"] == "Vlaamse Milieumaatschappij (VMM)"
    assert stats["older_than_since"] == 1
    site = {s["site_id"]: site_from_json(s) for s in sites}
    assert validate_record(r, site, NOW).status == "ok"


def test_ac46_arpac_benevento_samples_parse_italian_dates_and_keep_total_p_as_a_note():
    sites = national_feeds.arpac_sites(ARPAC_ST, {"S8"}, city="Benevento")
    records, _ = national_feeds.parse_arpac(ARPAC, {s["site_id"]: s for s in sites})
    r = records[0]
    assert r["observed_at"] == "2025-10-01T10:15:00+02:00"
    assert r["values"] == {"nitrate": 3.0, "nitrate-basis": "as-N", "ph": 7.9, "water-temperature": 16.4}
    assert any("Total phosphorus" in n and "<30" in n for n in r["source_info"])
    assert sites[0]["name"] == "Benevento · Sabato, Ponte Leproso"


def test_ac48_orthophosphate_reported_as_p_is_scored_on_its_po4_equivalent_and_keeps_its_unit():
    """AC-48: VMM orthophosphate (mg P/L) is imported with basis as-P; R2 compares 3.066 x P with 0.5 mg/L PO4;
    the Observation keeps the reported value with unit mg{P}/L."""
    from streamfhir.adapters.fhir_mapper import FhirMapper
    from streamfhir.domain.risk import evaluate_site
    sites = national_feeds.vmm_sites(VMM_STATIONS, city="Ghent")
    records, _ = national_feeds.parse_vmm({"OW172100": VMM.replace("\t0,11\tmgP/L", "\t0,27\tmgP/L")},
                                         {s["site_id"]: s for s in sites}, since="2025-09-01")
    r = records[0]
    assert r["values"]["phosphate"] == 0.27 and r["values"]["phosphate-basis"] == "as-P"
    site = {s["site_id"]: site_from_json(s) for s in sites}
    rep = validate_record(r, site, NOW)
    assert rep.status == "ok"
    risk = evaluate_site(sites[0]["site_id"], [rep], as_of=None)
    assert "R2" in {f.rule_id for f in risk.fired}            # 0.27 x 3.066 = 0.83 mg/L PO4 >= 0.5
    b = FhirMapper(test_data=False, pseudonymous=False).assessment_bundle(rep, site[sites[0]["site_id"]])
    po4 = [e["resource"] for e in b["entry"] if e["resource"]["resourceType"] == "Observation"
           and e["resource"]["code"]["coding"][0]["code"] == "phosphate"][0]
    assert po4["valueQuantity"]["value"] == 0.27 and po4["valueQuantity"]["code"] == "mg{P}/L"
