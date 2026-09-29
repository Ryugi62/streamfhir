"""Adapter: import real public monitoring data from the US Water Quality Portal (WQP, waterqualitydata.us).

Input: the WQP 'narrowResult' Result CSV and the Station CSV for the same query.
Output: StreamFHIR sites + records (one record per sampling activity), with anything odd in the source
kept as a 'source note' so validation routes it to a person instead of silently fixing it.
Only pH, water temperature (deg C) and nitrate are mapped; everything else is ignored.
"""
import csv
import io
from collections import OrderedDict
from typing import Dict, List, Tuple

TZ = {"EST": "-05:00", "EDT": "-04:00", "CST": "-06:00", "CDT": "-05:00", "MST": "-07:00", "MDT": "-06:00",
      "PST": "-08:00", "PDT": "-07:00", "UTC": "+00:00", "GMT": "+00:00"}
PH_UNITS = ("", "None", "std units", "None ")


def _float(text: str):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def parse_stations(csv_text: str) -> List[Dict]:
    sites = []
    for row in csv.DictReader(io.StringIO(csv_text)):
        lat, lon = _float(row.get("LatitudeMeasure")), _float(row.get("LongitudeMeasure"))
        if lat is None or lon is None:
            continue
        sites.append({"site_id": row["MonitoringLocationIdentifier"], "name": row.get("MonitoringLocationName") or row["MonitoringLocationIdentifier"],
                      "lat": lat, "lon": lon, "water_body": row.get("MonitoringLocationName") or "",
                      "description": "Real WQP station (%s, %s)" % (row.get("MonitoringLocationTypeName", ""), row.get("OrganizationFormalName", "")),
                      "flow": "still" if "lake" in (row.get("MonitoringLocationTypeName") or "").lower() else "flowing"})
    return sites


def parse_results(csv_text: str, stations: Dict[str, Dict]) -> Tuple[List[Dict], Dict[str, int]]:
    acts: "OrderedDict[str, Dict]" = OrderedDict()
    stats = {"rows": 0, "mapped_values": 0, "skipped_rows": 0}
    for row in csv.DictReader(io.StringIO(csv_text)):
        stats["rows"] += 1
        sid = row["MonitoringLocationIdentifier"]
        st = stations.get(sid)
        aid = row["ActivityIdentifier"]
        rec = acts.get(aid)
        if rec is None:
            date, time, tz = row.get("ActivityStartDate", ""), row.get("ActivityStartTime/Time", ""), row.get("ActivityStartTime/TimeZoneCode", "")
            notes = []
            if not time:
                notes.append("The source has no sampling time; midnight was used for the date.")
                time = "00:00:00"
            offset = TZ.get(tz)
            if offset is None:
                notes.append("The source time zone '%s' is unknown; UTC was assumed." % tz)
                offset = "+00:00"
            rec = {"record_id": aid, "synthetic": False, "site_id": sid, "observed_at": "%sT%s%s" % (date, time, offset),
                   "observer": row.get("OrganizationIdentifier", ""), "lat": st["lat"] if st else None,
                   "lon": st["lon"] if st else None, "photos": [], "values": {}, "source_notes": notes,
                   "source": "US Water Quality Portal, organisation %s" % row.get("OrganizationFormalName", "")}
            acts[aid] = rec
        name, unit = row.get("CharacteristicName"), (row.get("ResultMeasure/MeasureUnitCode") or "").strip()
        value = _float(row.get("ResultMeasureValue"))
        code = {"pH": "ph", "Temperature, water": "water-temperature", "Nitrate": "nitrate"}.get(name)
        if code is None:
            stats["skipped_rows"] += 1
            continue
        if value is None:
            rec["source_notes"].append("%s had no numeric value in the source (%r)." % (name, row.get("ResultMeasureValue")))
            stats["skipped_rows"] += 1
            continue
        if code in rec["values"]:
            rec["source_notes"].append("%s was reported more than once in this activity; the first value was kept." % name)
            stats["skipped_rows"] += 1
            continue
        if code == "ph" and unit not in PH_UNITS:
            rec["source_notes"].append("pH is reported with unit '%s' in the source; the value was kept as pH." % unit)
        if code == "water-temperature" and unit != "deg C":
            rec["source_notes"].append("Water temperature unit '%s' is not deg C; the value was not imported." % unit)
            stats["skipped_rows"] += 1
            continue
        if code == "nitrate" and not unit.lower().replace(" ", "").startswith("mg/l"):
            rec["source_notes"].append("Nitrate unit '%s' is not mg/L; the value was not imported." % unit)
            stats["skipped_rows"] += 1
            continue
        if code == "nitrate":
            u = unit.lower().replace(" ", "")
            if "asn" in u or u.endswith("-n"):
                rec["values"]["nitrate-basis"] = "as-N"
            elif "no3" in u:
                rec["values"]["nitrate-basis"] = "as-NO3"
        rec["values"][code] = value
        stats["mapped_values"] += 1
    records = [r for r in acts.values()]
    stats["records"] = len(records)
    return records, stats
