"""Adapter: coverage check against the EEA Waterbase (WISE-6) aggregated data that EU countries report.

Input: rows of [WISE_SOE].[latest].[Waterbase_T_WISE6_AggregatedData] for monitoring sites near a city
(queried from discodata.eea.europa.eu, open, no account).
Output: how many sites report the four StreamFHIR quantities, and the latest reported year of each - i.e. how fresh
the official EU picture of a city's streams is.
"""
from typing import Dict, Iterable

DETERMINANDS = {"EEA_3152-01-0": "ph", "EEA_3121-01-5": "water-temperature",
                "CAS_14797-55-8": "nitrate", "CAS_14265-44-2": "phosphate"}


def coverage(rows: Iterable[Dict]) -> Dict:
    sites, latest, samples = set(), {}, 0
    for r in rows:
        code = DETERMINANDS.get(r.get("observedPropertyDeterminandCode"))
        if code is None:
            continue
        sites.add(r["monitoringSiteIdentifier"])
        year = int(r["phenomenonTimeReferenceYear"])
        latest[code] = max(latest.get(code, year), year)
        samples += int(r.get("resultNumberOfSamples") or 0)
    return {"sites_with_data": len(sites), "latest_year": latest, "samples": samples}
