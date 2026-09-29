"""Adapters: two more OneAquaHealth cities' open agency river-quality feeds, read into the same record shape as Hub'Eau.

* Ghent - VMM (Vlaamse Milieumaatschappij) Databank waterkwaliteit, per-station report export (tab-separated,
  decimal comma; 'Teken' = '<' for results below a limit). Licence: modellicentie gratis hergebruik.
* Benevento - ARPA Campania open data "Monitoraggio Fiumi" (CSV per year; dates dd/mm/yyyy hh:mm local time;
  values may start with '<'). Licence: CC BY.

Mapped: nitrate as N (VMM 'NO3-' mgN/L, ARPAC 'IC164'), pH, water temperature, and VMM orthophosphate as P
('oPO4 f' mgP/L, basis as-P: the value is kept, the rule compares its PO4 equivalent). ARPAC reports total phosphorus,
which is not orthophosphate, so it stays a note.
"""
import csv
import io
from collections import OrderedDict
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

BRUSSELS, ROME = ZoneInfo("Europe/Brussels"), ZoneInfo("Europe/Rome")


def _num(text: str) -> Optional[float]:
    try:
        return float(str(text).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


def _new(rid, site, when, origin, observer, name, source):
    return {"record_id": rid, "synthetic": False, "origin": origin, "site_id": site["site_id"], "observed_at": when,
            "observer": observer, "observer_name": name, "lat": site["lat"], "lon": site["lon"], "photos": [],
            "values": {}, "below_limit": {}, "source_notes": [], "source_info": [], "source": source}


def _put(rec: Dict, code: str, value: float, name: str) -> None:
    if code in rec["values"]:
        rec["source_notes"].append("%s was reported more than once for this sampling; the first value was kept." % name)
        return
    rec["values"][code] = value
    if code == "nitrate":
        rec["values"]["nitrate-basis"] = "as-N"
    if code == "phosphate":
        rec["values"]["phosphate-basis"] = "as-P"


def vmm_sites(stations: Iterable[Dict], city: str) -> List[Dict]:
    return [{"site_id": "BE-VMM-" + s["code"], "name": "%s · %s" % (city, s["name"]), "lat": s["lat"], "lon": s["lon"],
             "water_body": s["name"], "flow": "flowing",
             "description": "Real Flemish river monitoring station %s (VMM)" % s["code"]} for s in stations]


def parse_vmm(texts: Dict[str, str], sites: Dict[str, Dict], since: Optional[str] = None) -> Tuple[List[Dict], Dict[str, int]]:
    recs: "OrderedDict[str, Dict]" = OrderedDict()
    stats = {"rows": 0, "mapped_values": 0, "older_than_since": 0}
    older = set()
    for code, text in texts.items():
        site = sites["BE-VMM-" + code]
        for row in csv.DictReader(io.StringIO(text), delimiter="\t"):
            stats["rows"] += 1
            day, clock = row["Datum"], (row.get("Tijdstip") or "00:00:00")
            if since and day < since:
                older.add((code, day))
                continue
            rid = "BE-VMM-%s-%sT%s" % (code, day, clock[:5].replace(":", ""))
            rec = recs.get(rid)
            if rec is None:
                when = datetime.fromisoformat("%sT%s" % (day, clock)).replace(tzinfo=BRUSSELS).isoformat()
                rec = recs[rid] = _new(rid, site, when, "vmm", "VMM", "Vlaamse Milieumaatschappij (VMM)",
                                       "VMM Databank waterkwaliteit, station %s" % code)
            sym, sign, value, unit = row["Parameter Symbool"], (row.get("Teken") or "=").strip(), _num(row["Resultaat"]), (row.get("Eenheid") or "").strip()
            target = {"NO3-": ("nitrate", "mgN/L"), "pH": ("ph", "-"), "T": ("water-temperature", "°C"),
                      "oPO4 f": ("phosphate", "mgP/L")}.get(sym)
            if target is None or value is None:
                continue
            if sign != "=":
                rec["source_info"].append("%s %s %s %s (below a laboratory limit; not a number)." % (row["Parameter omschrijving"], sign, row["Resultaat"], unit))
                continue
            if unit != target[1]:
                rec["source_notes"].append("%s is reported in '%s', not '%s'; it was not imported." % (row["Parameter omschrijving"], unit, target[1]))
                continue
            _put(rec, target[0], value, row["Parameter omschrijving"])
            stats["mapped_values"] += 1
    stats["older_than_since"] = len(older)
    records = [r for r in recs.values() if r["values"]]
    stats["records"] = len(records)
    return records, stats


def arpac_sites(stations_csv: str, codes: Iterable[str], city: str) -> List[Dict]:
    keep, out = set(codes), []
    for s in csv.DictReader(io.StringIO(stations_csv)):
        if s["codice_sito"] in keep:
            river = (s.get("fiume") or "").split("_")[0].split(" ")[0].title()
            out.append({"site_id": "IT-ARPAC-" + s["codice_sito"], "name": "%s · %s, %s" % (city, river, s["localita"]),
                        "lat": float(s["latitudine_n"]), "lon": float(s["longitudine_e"]), "water_body": s.get("fiume") or "",
                        "flow": "flowing", "description": "Real Campania river monitoring station %s (ARPAC), %s" % (
                            s["codice_sito"], s.get("comune") or "")})
    return out


def parse_arpac(text: str, sites: Dict[str, Dict]) -> Tuple[List[Dict], Dict[str, int]]:
    recs: "OrderedDict[str, Dict]" = OrderedDict()
    stats = {"rows": 0, "mapped_values": 0}
    for row in csv.DictReader(io.StringIO(text)):
        stats["rows"] += 1
        site = sites.get("IT-ARPAC-" + row["cod_staz"])
        if site is None:
            continue
        local = datetime.strptime(row["data"].strip(), "%d/%m/%Y %H:%M").replace(tzinfo=ROME)
        rid = "IT-ARPAC-%s-%s" % (row["cod_staz"], local.strftime("%Y-%m-%dT%H%M"))
        rec = recs.get(rid)
        if rec is None:
            rec = recs[rid] = _new(rid, site, local.isoformat(), "arpac", "ARPAC", "ARPA Campania",
                                   "ARPA Campania open data, Monitoraggio Fiumi, station %s" % row["cod_staz"])
        raw, unit = (row.get("Valore mis") or "").strip(), (row.get("um") or "").strip()
        if row["inq"] == "P_TOT":
            rec["source_info"].append("Total phosphorus %s %s (not orthophosphate; kept as a note)." % (raw, unit))
            continue
        target = {"IC164": "nitrate", "IC26": "ph", "MI32": "water-temperature"}.get(row["inq"])
        if target is None:
            continue
        if raw.startswith("<"):
            rec["source_info"].append("%s %s %s (below a laboratory limit; not a number)." % (row["Desc inq"], raw, unit))
            continue
        value = _num(raw)
        if value is None:
            rec["source_notes"].append("%s has no numeric value in the source (%r)." % (row["Desc inq"], raw))
            continue
        _put(rec, target, value, row["Desc inq"])
        stats["mapped_values"] += 1
    records = [r for r in recs.values() if r["values"]]
    stats["records"] = len(records)
    return records, stats
