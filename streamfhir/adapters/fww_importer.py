"""Adapter: import real citizen-science records from Earthwatch Europe's FreshWater Watch (public ArcGIS feature
service, no account). Volunteers record what they see; StreamFHIR maps only the fields whose meaning matches its
indicators, and keeps everything else (kit colour bands for nitrate and phosphate, pollution sources) as source_info.

Mapping (source -> StreamFHIR):
  optical_colour Colourless/Brown/Green -> water-colour clear/brown-turbid/green; any other colour -> other
  ecological_algae Blue_green_scum -> surface algal-scum; else water_surface Oily_sheen/Foam/None -> oily-sheen/foam/none
  ecological_water_uses Swimming -> people-contact; Animal_access is kept as a note (animals can reach the water; not an
  observed contact, so it never counts as pets or livestock in the water)
  ecological_litter In/near the water -> some; None/No -> none
  photo_url -> photo kept for a reviewer
Plausibility: a blue-green scum ticked on flowing or colourless water, a slurry film, or an outfall pipe discharging go to
a person (source notes -> "needs a quick look") instead of counting on their own.
Kit bands are colour-chart readings of the FreshWater Watch kit: nitrate as NO3-N, phosphate as PO4-P (printed on the cards).
"""
from collections import OrderedDict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Dict, List, Tuple

COLOUR = {"Colourless": "clear", "Brown": "brown-turbid", "Green": "green"}
NONE = ("", "None", None)


def _site_id(a: Dict, g: Dict) -> str:
    return "FWW-%.4f,%.4f" % (g["y"], g["x"])


def parse_sites(payload: Dict, region: str = "") -> List[Dict]:
    sites: "OrderedDict[str, Dict]" = OrderedDict()
    for f in payload.get("features", []):
        a, g = f["attributes"], f.get("geometry") or {}
        if "x" not in g or "y" not in g:
            continue
        sid = _site_id(a, g)
        name = " ".join((a.get("site_name") or "").replace("_", " ").split()) or "Unnamed site"
        body = a.get("ecological_fw_body_type") or ""
        sites.setdefault(sid, {"site_id": sid, "name": ("%s · %s" % (region, name)) if region else name,
                               "lat": g["y"], "lon": g["x"], "water_body": name,
                               "description": "Real citizen-science site (FreshWater Watch, %s)" % (body or "type not given"),
                               "flow": "still" if body in ("Lake", "Pond", "Wetland") else "flowing", "region": region})
    return list(sites.values())


def _values(a: Dict) -> Tuple[Dict, List[str], List[str]]:
    v, info, notes = {}, [], []
    colour = a.get("optical_colour")
    if colour not in NONE:
        v["water-colour"] = COLOUR.get(colour, "other")
        if colour not in COLOUR:
            info.append("Water colour '%s' in the source is recorded as 'other'." % colour)
    algae, surface = a.get("ecological_algae") or "", a.get("ecological_water_surface") or ""
    if "Blue_green_scum" in algae:
        v["surface"] = "algal-scum"
        flowing = (a.get("hydrological_water_flow") or "") in ("Steady", "Surging", "Fast")
        if flowing or colour == "Colourless":
            notes.append("CHECK: the volunteer ticked 'Blue_green_scum' (algae) with flow '%s' and colour '%s'; surface scums "
                         "rarely form on %s water, so a reviewer should check the photo before it counts." % (
                             a.get("hydrological_water_flow") or "?", colour or "?", "flowing" if flowing else "colourless"))
    elif "Oily_sheen" in surface:
        v["surface"] = "oily-sheen"
    elif "Foam" in surface:
        v["surface"] = "foam"
    elif surface == "None":
        v["surface"] = "none"
    if algae not in NONE and algae not in ("No_algae", "Blue_green_scum"):
        info.append("Algae reported as '%s' (not a blue-green scum)." % algae)
    if surface not in NONE and not any(k in surface for k in ("Oily_sheen", "Foam")):
        info.append("Water surface reported as '%s'." % surface)
    uses = a.get("ecological_water_uses")
    if uses not in NONE or uses == "None":
        v["people-contact"] = "Swimming" in (uses or "")
        if "Animal_access" in (uses or ""):
            info.append("Water uses include animal access: animals can reach the water (not an observed contact).")
    if "Slurry" in surface:
        notes.append("CHECK: the volunteer reported 'Slurry' on the surface: a possible manure or sewage signal for a reviewer to check.")
    litter = a.get("ecological_litter")
    if litter not in NONE or litter == "None":
        v["litter"] = "none" if litter in ("None", "No") else "some"
    for key, label in (("chemical_nitrate", "Nitrate kit band %s mg/L as NO3-N"), ("chemical_phosphate", "Phosphate kit band %s mg/L as PO4-P")):
        if a.get(key) not in NONE:
            info.append((label + " (colour-chart band; not scored).") % a[key])
    sources = a.get("ecological_pollution_sources")
    if sources not in NONE:
        info.append("Pollution sources noted: %s." % sources)
        if "Outfall_pipe_currently_discharging" in sources:
            notes.append("CHECK: the volunteer noted 'Outfall_pipe_currently_discharging': a possible sewage signal for a reviewer to check.")
    return v, info, notes


def parse_records(payload: Dict, sites: Dict[str, Dict], tz: str = "UTC") -> Tuple[List[Dict], Dict[str, int]]:
    zone = ZoneInfo(tz)
    records, stats = [], {"features": 0, "records": 0, "skipped": 0}
    for f in payload.get("features", []):
        stats["features"] += 1
        a, g = f["attributes"], f.get("geometry") or {}
        sid = _site_id(a, g) if "x" in g and "y" in g else None
        if sid not in sites or not a.get("sample_date"):
            stats["skipped"] += 1
            continue
        values, info, notes = _values(a)
        if not values:
            stats["skipped"] += 1
            continue
        day = datetime.fromtimestamp(a["sample_date"] / 1000, tz=timezone.utc).date()
        clock = (a.get("sample_time") or "").strip()
        try:
            hh, mm = (int(x) for x in clock.split(":")[:2])
            when = datetime(day.year, day.month, day.day, hh, mm, tzinfo=zone).isoformat()
        except ValueError:
            when = datetime(day.year, day.month, day.day, tzinfo=zone).isoformat()
            notes.append("The source has no sampling time; midnight local time was used for the date.")
        records.append({"record_id": "FWW-%s" % a["objectid"], "synthetic": False, "site_id": sid, "observed_at": when,
                        "observer": "fww-group-%s" % (a.get("group_id") or "unknown")[:12],
                        "lat": g["y"], "lon": g["x"], "photos": [a["photo_url"]] if a.get("photo_url") else [],
                        "values": values, "source_info": info, "source_notes": notes,
                        "source": "Earthwatch Europe FreshWater Watch, project '%s'" % (a.get("group_name") or "?")})
    stats["records"] = len(records)
    return records, stats
