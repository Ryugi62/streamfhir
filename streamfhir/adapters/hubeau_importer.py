"""Adapter: import real public river monitoring data for France from Hub'Eau (hubeau.eaufrance.fr, API
"qualite_rivieres" v2, open, no account). Codes are the French national Sandre codes.

Input: the JSON of /station_pc and /analyse_pc for the same query.
Output: StreamFHIR sites + records (one record per station and sampling date/time). The site keeps its Sandre
station code and, when France reports the same station to the EEA, its EU monitoring-site code.
Mapped: pH (1302), water temperature (1301), nitrate (1340, the NO3- ion) and orthophosphate (1433, as PO4).
Results below the quantification limit are not imported as numbers; they are counted and listed in `source_info`.
Anything else odd in the source becomes a `source_note`, so validation routes the record to a person.
"""
from collections import OrderedDict
from datetime import datetime
from typing import Dict, Iterable, List, Tuple
from zoneinfo import ZoneInfo

from .fhir_mapper import EEA_SITE_SCHEME, SANDRE_STATION

PARIS = ZoneInfo("Europe/Paris")
PARAMS = {  # Sandre parameter -> (StreamFHIR indicator, accepted units)
    "1302": ("ph", ("unité pH",)),
    "1301": ("water-temperature", ("°C",)),
    "1340": ("nitrate", ("mg(NO3)/L", "mg/L")),
    "1433": ("phosphate", ("mg(PO4)/L", "mg/L")),
}
QUANTIFIED = "1"                  # Sandre remark code 1: result within the quantification range
BELOW_LIMIT = {"10": "quantification", "2": "detection"}   # 10: < LQ, 2: < LD


def parse_stations(payload: Dict, city: str = "", eu_site_codes: Iterable[str] = ()) -> List[Dict]:
    eu = set(eu_site_codes)
    sites = []
    for st in payload.get("data", []):
        lat, lon = st.get("latitude"), st.get("longitude")
        if lat is None or lon is None:
            continue
        code = st["code_station"]
        ids = [{"system": SANDRE_STATION, "value": code}]
        if "FR" + code in eu:
            ids.append({"system": EEA_SITE_SCHEME + "euMonitoringSiteCode", "value": "FR" + code})
        name = st.get("libelle_station") or code
        sites.append({"site_id": "FR-" + code, "name": ("%s · %s" % (city, name)) if city else name,
                      "lat": lat, "lon": lon, "water_body": st.get("nom_cours_eau") or name,
                      "description": "Real French river monitoring station %s (Hub'Eau / Sandre)%s" % (
                          code, ", also reported to the EEA as FR" + code if "FR" + code in eu else ""),
                      "flow": "still" if "plan d'eau" in (st.get("type_entite_hydro") or "").lower() else "flowing",
                      "identifiers": ids, "city": city})
    return sites


def _stamp(day: str, hour: str) -> Tuple[str, List[str]]:
    notes = []
    if not hour:
        notes.append("The source has no sampling time; midnight local time was used for the date.")
        hour = "00:00:00"
    local = datetime.fromisoformat("%sT%s" % (day, hour)).replace(tzinfo=PARIS)
    return local.isoformat(), notes


def parse_analyses(payload: Dict, stations: Dict[str, Dict]) -> Tuple[List[Dict], Dict[str, int]]:
    recs: "OrderedDict[Tuple[str, str, str], Dict]" = OrderedDict()
    stats = {"rows": 0, "mapped_values": 0, "below_quantification_limit": 0, "skipped_rows": 0}
    rows = sorted(payload.get("data", []), key=lambda r: (r["code_station"], r["date_prelevement"], r.get("heure_prelevement") or ""))
    for row in rows:
        stats["rows"] += 1
        sid = "FR-" + row["code_station"]
        st = stations.get(sid)
        if st is None:
            stats["skipped_rows"] += 1
            continue
        key = (sid, row["date_prelevement"], row.get("heure_prelevement") or "")
        rec = recs.get(key)
        if rec is None:
            when, notes = _stamp(row["date_prelevement"], row.get("heure_prelevement") or "")
            rec = {"record_id": "%s-%s%s" % (sid, row["date_prelevement"], ("T" + key[2][:5].replace(":", "")) if key[2] else ""),
                   "synthetic": False, "origin": "sandre", "site_id": sid, "observed_at": when,
                   "observer": row.get("code_producteur_analyse") or row.get("nom_producteur_analyse") or "unknown producer",
                   "observer_name": row.get("nom_producteur_analyse") or "a Hub'Eau data producer (code %s)" % row.get("code_producteur_analyse"),
                   "lat": st["lat"], "lon": st["lon"],
                   "photos": [], "values": {}, "below_limit": {}, "source_notes": notes, "source_info": [],
                   "source": "Hub'Eau qualite_rivieres (Sandre), station %s" % row["code_station"]}
            recs[key] = rec
        mapped = PARAMS.get(row.get("code_parametre"))
        if mapped is None:
            stats["skipped_rows"] += 1
            continue
        code, units = mapped
        name, unit, value = row.get("libelle_parametre"), row.get("symbole_unite") or "", row.get("resultat")
        remark = str(row.get("code_remarque") or "")
        if remark in BELOW_LIMIT:
            stats["below_quantification_limit"] += 1
            limit = row.get("limite_quantification") if remark == "10" else row.get("limite_detection")
            if isinstance(limit, (int, float)) and unit in units and code not in rec["below_limit"]:
                rec["below_limit"][code] = limit
            rec["source_info"].append("%s was below the %s limit (< %s %s); it was not imported as a number." % (
                name, BELOW_LIMIT[remark], row.get("limite_quantification") if remark == "10" else row.get("limite_detection"), unit))
            continue
        if remark != QUANTIFIED or not isinstance(value, (int, float)):
            rec["source_notes"].append("%s has source remark code %r (%s) and no usable number; it was not imported." % (
                name, remark, row.get("mnemo_remarque") or "unknown"))
            stats["skipped_rows"] += 1
            continue
        if unit not in units:
            rec["source_notes"].append("%s is reported in '%s', which is not an expected unit; it was not imported." % (name, unit))
            stats["skipped_rows"] += 1
            continue
        if code in rec["values"]:
            rec["source_notes"].append("%s was reported more than once for this sampling; the first value was kept." % name)
            stats["skipped_rows"] += 1
            continue
        if str(row.get("code_qualification") or "1") != "1":
            rec["source_notes"].append("The producer qualified the %s result as '%s' (not 'Correcte')." % (
                name, row.get("libelle_qualification") or row.get("code_qualification")))
        rec["values"][code] = value
        if code == "nitrate":
            rec["values"]["nitrate-basis"] = "as-NO3"    # Sandre 1340 is the nitrate ion NO3- by definition
        stats["mapped_values"] += 1
    records = [r for r in recs.values() if r["values"] or r["source_notes"] or r["below_limit"]]
    stats["records"] = len(records)
    stats["records_without_values"] = len(recs) - len(records)
    return records, stats
