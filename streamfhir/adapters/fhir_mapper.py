"""Adapter: domain objects -> HL7 FHIR R4 JSON (transaction Bundles + conformance resources).

All StreamFHIR-specific codes live in CodeSystems under an *example* canonical base
(https://example.org/...). They are prototype codes, not HL7, LOINC or SNOMED CT codes.
External codes used: core HL7 terminology, UCUM, and exactly one LOINC code
(9480-5 "Nitrate [Mass/volume] in Water", confirmed with a $lookup on tx.fhir.org,
LOINC 2.82) - added only when the reading is expressed as NO3.
"""
import re
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from ..domain.indicators import (BOOLEAN, CATEGORY, COUNT, INDICATORS, PANEL_CODE, PANEL_DISPLAY, QUANTITY, SCORE,
                                 answer_code)
from ..domain.risk import CONFIRM, HAZARD, HIGH, LOW, MODERATE, REJECT, RULES, VERIFY, SiteRisk
from ..domain.sites import Site
from ..domain.validation import REVIEW, ValidationReport

VERSION = "0.7.0"
DATE = "2026-09-29"
BASE = "https://example.org/fhir/streamfhir"   # example canonical - replace when published
CS_INDICATOR = BASE + "/CodeSystem/stream-indicator"
CS_ANSWER = BASE + "/CodeSystem/stream-answer"
CS_RISK = BASE + "/CodeSystem/onehealth-risk-level"
CS_RULE = BASE + "/CodeSystem/onehealth-risk-rule"
EXT_FLAG_RULE = BASE + "/StructureDefinition/flag-rule"   # coded citation of a fired rule on a Flag
VS_INDICATOR = BASE + "/ValueSet/stream-indicator"
VS_HAZARD_RULE = BASE + "/ValueSet/onehealth-hazard-rule"
PROFILE_PANEL = BASE + "/StructureDefinition/stream-assessment-panel"
PROFILE_OBS = BASE + "/StructureDefinition/stream-indicator-observation"
PROFILE_FLAG = BASE + "/StructureDefinition/stream-site-flag"
VS_RISK_LEVEL = BASE + "/ValueSet/onehealth-risk-level"
SID_SITE = BASE + "/sid/site"
SID_OBSERVER = BASE + "/sid/observer"
SID_REVIEWER = BASE + "/sid/reviewer"
SID_RECORD = BASE + "/sid/record"
SID_DEVICE = BASE + "/sid/device"
SID_FLAG = BASE + "/sid/flag"

UCUM = "http://unitsofmeasure.org"
LOINC = "http://loinc.org"
# European water-data vocabularies (resolvable URIs of their publishers; they publish no FHIR CodeSystem resources)
EEA_OBSERVED_PROPERTY = "http://dd.eionet.europa.eu/vocabulary/wise/ObservedProperty"   # EEA WISE determinands (Waterbase)
EEA_SITE_SCHEME = "http://dd.eionet.europa.eu/vocabulary/wise/IdentifierScheme/"          # + euMonitoringSiteCode
SANDRE_PARAMETER = "https://id.eaufrance.fr/par"                                          # French national parameter codes
SANDRE_STATION = "https://id.eaufrance.fr/StationMesureEauxSurface"                       # French surface-water stations
SANDRE_INTERVENANT = "https://id.eaufrance.fr/int"                                        # French water-data organisations
EEA_EMITTED = ("ph", "water-temperature", "nitrate")   # the EEA reports phosphate as P, StreamFHIR as PO4: not emitted
CM_EU_WATER = BASE + "/ConceptMap/stream-indicator-to-eu-water"
# StreamFHIR indicator -> the same observed property in EU (EEA WISE) and French (Sandre) water-data vocabularies.
# Codes and labels were read from the publishers on 2026-09-29 (dd.eionet.europa.eu, id.eaufrance.fr).
EU_WATER_CROSSWALK = {
    "ph": ((EEA_OBSERVED_PROPERTY, "EEA_3152-01-0", "pH"), (SANDRE_PARAMETER, "1302", "Potentiel en Hydrogène (pH)")),
    "water-temperature": ((EEA_OBSERVED_PROPERTY, "EEA_3121-01-5", "Water temperature"),
                          (SANDRE_PARAMETER, "1301", "Température de l'Eau")),
    "nitrate": ((EEA_OBSERVED_PROPERTY, "CAS_14797-55-8", "Nitrate"), (SANDRE_PARAMETER, "1340", "Nitrates")),
    "phosphate": ((EEA_OBSERVED_PROPERTY, "CAS_14265-44-2", "Phosphate"), (SANDRE_PARAMETER, "1433", "Orthophosphates (PO4)")),
}
OBS_CATEGORY = "http://terminology.hl7.org/CodeSystem/observation-category"
ACT_REASON = "http://terminology.hl7.org/CodeSystem/v3-ActReason"
OBS_VALUE = "http://terminology.hl7.org/CodeSystem/v3-ObservationValue"
CONFIDENTIALITY = "http://terminology.hl7.org/CodeSystem/v3-Confidentiality"
DATA_OPERATION = "http://terminology.hl7.org/CodeSystem/v3-DataOperation"
PARTICIPANT_TYPE = "http://terminology.hl7.org/CodeSystem/provenance-participant-type"
FLAG_CATEGORY = "http://terminology.hl7.org/CodeSystem/flag-category"
MEDIA_TYPE = "http://terminology.hl7.org/CodeSystem/media-type"
FLAG_DETAIL = "http://hl7.org/fhir/StructureDefinition/flag-detail"
JURISDICTION_WORLD = {"coding": [{"system": "http://unstats.un.org/unsd/methods/m49/m49.htm", "code": "001",
                                  "display": "World"}]}

UNIT_DISPLAY = {"Cel": "°C", "[pH]": "pH", "mg/L": "mg/L", "cm": "cm", "min": "min"}
TEST_DATA = {"system": ACT_REASON, "code": "HTEST", "display": "test health data"}
PSEUDONYMISED = {"system": OBS_VALUE, "code": "PSEUDED", "display": "pseudonymized"}
UNRESTRICTED = {"system": CONFIDENTIALITY, "code": "U", "display": "unrestricted"}
LEVEL_DISPLAY = {HIGH: "High health hazard", VERIFY: "Health hazard - verification requested",
                 MODERATE: "Moderate health hazard", LOW: "Low health hazard"}
LEVEL_DEFINITION = {
    HIGH: "Corroborated hazard points >= 5 in the 14-day window: a FHIR Flag is raised.",
    VERIFY: "Hazard points >= 5 but not enough corroborated by trusted records: verification requested, no Flag.",
    MODERATE: "Hazard points 2-4 in the 14-day window.",
    LOW: "Hazard points 0-1 in the 14-day window."}
PHOTO_EVIDENCE = ("water-colour", "surface", "dead-fish", "bloom-check")
DEVICE_ID = "streamfhir-rule-engine"
NITRATE_UNITS = {"as-NO3": ("mg/L as NO3", "mg{NO3}/L"), "as-N": ("mg/L as N", "mg{N}/L")}   # basis stated on the value itself
NITRATE_LOINC = {"system": LOINC, "code": "9480-5", "display": "Nitrate [Mass/volume] in Water"}


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def narrative(text: str) -> Dict[str, str]:
    """Generated human-readable narrative (satisfies the dom-6 best-practice constraint)."""
    return {"status": "generated", "div": '<div xmlns="http://www.w3.org/1999/xhtml"><p>%s</p></div>' % _xml(text)}


def _plain(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    return "%s" % value


def fhir_id(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9\-.]", "-", text)[:64]


def _logical(system: str, value: str, rtype: Optional[str] = None, display: Optional[str] = None) -> Dict[str, Any]:
    ref: Dict[str, Any] = {"identifier": {"system": system, "value": value}}
    if rtype:
        ref["type"] = rtype
    if display:
        ref["display"] = display
    return ref


class FhirMapper:
    def __init__(self, id_factory: Optional[Callable[[], str]] = None,
                 now_fn: Optional[Callable[[], datetime]] = None, test_data: bool = True, pseudonymous: bool = True):
        """test_data: tag every resource HTEST (synthetic demo data). pseudonymous: observers are pseudonyms
        (citizens); False for real monitoring organisations, whose ids are public."""
        self.test_data = test_data
        self.pseudonymous = pseudonymous
        self._new_id = id_factory or (lambda: str(uuid.uuid4()))
        self._now = now_fn or (lambda: datetime.now(timezone.utc).replace(microsecond=0))

    # ---------- helpers ----------
    def _urn(self) -> str:
        return "urn:uuid:" + self._new_id()

    def _meta(self, profile: Optional[str] = None, pseudonymised: bool = False) -> Dict[str, Any]:
        sec = ([dict(TEST_DATA)] if self.test_data else []) + [dict(UNRESTRICTED)] \
            + ([dict(PSEUDONYMISED)] if pseudonymised and self.pseudonymous else [])
        meta: Dict[str, Any] = {"security": sec}
        if profile:
            meta["profile"] = [profile]
        return meta

    @staticmethod
    def _create(rtype: str, system: str, value: str) -> Dict[str, str]:
        """Conditional create (sites, software): created once, never duplicated."""
        return {"method": "POST", "url": rtype, "ifNoneExist": "identifier=%s|%s" % (system, value)}

    @staticmethod
    def _upsert(rtype: str, system: str, value: str) -> Dict[str, str]:
        """Conditional update: re-sending never duplicates, and a later status change (review) reaches the server."""
        return {"method": "PUT", "url": "%s?identifier=%s" % (rtype, urllib.parse.quote("%s|%s" % (system, value), safe=":/|"))}

    def _location_entry(self, site: Site) -> Dict[str, Any]:
        return {
            "fullUrl": self._urn(),
            "resource": {
                "resourceType": "Location",
                "meta": self._meta(),
                "text": narrative("Stream monitoring site %s (%s)" % (site.name, site.site_id)),
                "identifier": [{"system": SID_SITE, "value": site.site_id}]
                + [{"system": system, "value": value} for system, value in site.identifiers],
                "status": "active",
                "name": site.name,
                "description": site.description or "Citizen-science stream monitoring site on %s" % site.water_body,
                "mode": "instance",
                "physicalType": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/location-physical-type",
                                             "code": "area", "display": "Area"}]},
                "position": {"longitude": site.lon, "latitude": site.lat},
            },
            "request": self._create("Location", SID_SITE, site.site_id),
        }

    def _device_entry(self) -> Dict[str, Any]:
        return {
            "fullUrl": self._urn(),
            "resource": {
                "resourceType": "Device",
                "meta": self._meta(),
                "text": narrative("StreamFHIR mapper and rule engine %s" % VERSION),
                "identifier": [{"system": SID_DEVICE, "value": DEVICE_ID}],
                "deviceName": [{"name": "StreamFHIR mapper and rule engine", "type": "user-friendly-name"}],
                "version": [{"value": VERSION}],
            },
            "request": self._create("Device", SID_DEVICE, DEVICE_ID),
        }

    @staticmethod
    def _value(code: str, value: Any) -> Dict[str, Any]:
        ind = INDICATORS[code]
        if ind.kind in (SCORE, COUNT):
            return {"valueInteger": value}
        if ind.kind == QUANTITY:
            return {"valueQuantity": {"value": value, "unit": UNIT_DISPLAY.get(ind.unit, ind.unit),
                                      "system": UCUM, "code": ind.unit}}
        if ind.kind == CATEGORY:
            return {"valueCodeableConcept": {"coding": [{"system": CS_ANSWER, "code": answer_code(ind, value),
                                                         "display": "%s: %s" % (ind.display, value)}],
                                             "text": value}}
        if ind.kind == BOOLEAN:
            return {"valueBoolean": value}
        raise ValueError(code)

    # ---------- UC-1 ----------
    def assessment_bundle(self, report: ValidationReport, site: Site, decision: Optional[str] = None,
                          basis: Optional[str] = None) -> Optional[Dict[str, Any]]:
        a = report.assessment
        if a is None:
            return None
        if decision == REJECT:
            status = "entered-in-error"
        elif decision == CONFIRM or report.status != REVIEW:
            status = "final"
        else:
            status = "preliminary"
        prefix = "Confirmed by reviewer: " if decision == CONFIRM else "Needs review: "
        record_notes = [prefix + i.message for i in report.issues if not i.field.startswith("values.")]
        performer = {"identifier": {"system": SID_OBSERVER, "value": a.observer},
                     "display": "Pseudonymous citizen scientist" if self.pseudonymous else "Monitoring organisation %s" % a.observer}
        if a.origin == "sandre":
            performer = {"identifier": {"system": SANDRE_INTERVENANT, "value": a.observer},
                         "display": a.observer_name or "Organisation %s" % a.observer}
        if not self.pseudonymous:
            performer["type"] = "Organization"   # real monitoring organisations are Organizations; citizens stay untyped
        loc = self._location_entry(site)
        dev = self._device_entry()
        loc_ref = {"reference": loc["fullUrl"], "display": site.name}
        entries: List[Dict[str, Any]] = [loc, dev]

        media_refs = []
        for n, url in enumerate(a.photos, 1):
            mid = "%s.photo-%d" % (a.record_id, n)
            e = {"fullUrl": self._urn(), "resource": {
                "resourceType": "Media", "meta": self._meta(), "status": "completed",
                "text": narrative("Citizen photo %d of %s, record %s" % (n, site.name, a.record_id)),
                "identifier": [{"system": SID_RECORD, "value": mid}],
                "type": {"coding": [{"system": MEDIA_TYPE, "code": "image", "display": "Image"}]},
                "subject": loc_ref, "createdDateTime": _iso(a.observed_at),
                "content": {"contentType": "image/jpeg", "url": url, "title": "Citizen photo %d" % n}},
                "request": self._upsert("Media", SID_RECORD, mid)}
            entries.append(e)
            media_refs.append({"reference": e["fullUrl"]})

        category = ([{"coding": [{"system": OBS_CATEGORY, "code": "laboratory", "display": "Laboratory"}]}] if a.origin
                    else [{"coding": [{"system": OBS_CATEGORY, "code": "survey", "display": "Survey"}]}])
        member_entries = []

        def codings(code: str) -> List[Dict[str, str]]:
            coding = [{"system": CS_INDICATOR, "code": code, "display": INDICATORS[code].display}]
            as_no3 = code != "nitrate" or a.values.get("nitrate-basis") == "as-NO3"
            if code == "nitrate" and as_no3:
                coding.append(dict(NITRATE_LOINC))
            if code == "phosphate" and a.values.get("phosphate-basis") == "as-P":
                return coding          # the Sandre and EEA pairs in the crosswalk are for PO4 values
            if code in EU_WATER_CROSSWALK and as_no3:
                eu, national = EU_WATER_CROSSWALK[code]
                if code in EEA_EMITTED:
                    coding.append(dict(zip(("system", "code", "display"), eu)))
                if a.origin == "sandre":
                    coding.append(dict(zip(("system", "code", "display"), national)))
            return coding

        below = [(code, {"comparator": "<", "value": limit}) for code, limit in a.below_limit.items() if code not in a.values]
        for code, value in list(a.values.items()) + below:
            ind = INDICATORS[code]
            notes = [prefix + i.message for i in report.warnings_for(code)] + record_notes
            if isinstance(value, dict):
                notes = ["Below the laboratory's quantification limit (< %s %s): the source reports no number, only the limit."
                         % (_plain(value["value"]), UNIT_DISPLAY.get(ind.unit, ind.unit))] + notes
            coding = codings(code)
            oid = "%s.%s" % (a.record_id, code)
            obs: Dict[str, Any] = {
                "resourceType": "Observation",
                "meta": self._meta(PROFILE_OBS, pseudonymised=True),
                "text": narrative("%s: %s%s at %s (%s, record %s)" % (
                    ind.display, _plain(value), (" " + UNIT_DISPLAY.get(ind.unit, ind.unit)) if ind.unit else "",
                    site.name, status, a.record_id)),
                "identifier": [{"system": SID_RECORD, "value": oid}],
                "status": status,
                "category": category,
                "code": {"coding": coding, "text": ind.display},
                "subject": loc_ref,
                "effectiveDateTime": _iso(a.observed_at),
                "performer": [performer],
            }
            if isinstance(value, dict):
                obs["valueQuantity"] = dict(self._value(code, value["value"])["valueQuantity"], comparator=value["comparator"])
            else:
                obs.update(self._value(code, value))
            if code == "nitrate" and a.values.get("nitrate-basis") in NITRATE_UNITS:
                display, ucum = NITRATE_UNITS[a.values["nitrate-basis"]]
                obs["valueQuantity"].update(unit=display, code=ucum)
            if code == "phosphate" and a.values.get("phosphate-basis") == "as-P":
                obs["valueQuantity"].update(unit="mg/L as P", code="mg{P}/L")
            if notes:
                obs["note"] = [{"text": t} for t in notes]
            if media_refs and code in PHOTO_EVIDENCE:
                obs["derivedFrom"] = media_refs
            member_entries.append({"fullUrl": self._urn(), "resource": obs,
                                   "request": self._upsert("Observation", SID_RECORD, oid)})

        panel = {
            "resourceType": "Observation",
            "meta": self._meta(PROFILE_PANEL, pseudonymised=True),
            "text": narrative("%s of %s on %s: %d indicators (%s, record %s)" % (
                "Agency sampling" if a.origin else "Citizen stream assessment", site.name, _iso(a.observed_at),
                len(member_entries), status, a.record_id)),
            "identifier": [{"system": SID_RECORD, "value": a.record_id}],
            "status": status,
            "category": category,
            "code": {"coding": [{"system": CS_INDICATOR, "code": PANEL_CODE, "display": PANEL_DISPLAY}], "text": PANEL_DISPLAY},
            "subject": loc_ref,
            "effectiveDateTime": _iso(a.observed_at),
            "performer": [performer],
            "hasMember": [{"reference": e["fullUrl"]} for e in member_entries],
        }
        if report.issues:
            panel["note"] = [{"text": prefix + i.message} for i in report.issues]
        if media_refs:
            panel["derivedFrom"] = media_refs
        panel_entry = {"fullUrl": self._urn(), "resource": panel,
                       "request": self._upsert("Observation", SID_RECORD, a.record_id)}
        entries += [panel_entry] + member_entries

        targets = [{"reference": e["fullUrl"]} for e in [panel_entry] + member_entries] + media_refs
        agents = [
            {"type": {"coding": [{"system": PARTICIPANT_TYPE, "code": "author", "display": "Author"}]}, "who": performer},
            {"type": {"coding": [{"system": PARTICIPANT_TYPE, "code": "assembler", "display": "Assembler"}]},
             "who": {"reference": dev["fullUrl"], "display": "StreamFHIR %s" % VERSION}},
        ]
        if decision in (CONFIRM, REJECT):
            agents.append({"type": {"coding": [{"system": PARTICIPANT_TYPE, "code": "verifier", "display": "Verifier"}]},
                           "who": _logical(SID_REVIEWER, "reviewer-demo", display="Data reviewer (%s%s)" % (
                               decision, ", basis: " + basis if basis else ""))})
        prov_id = fhir_id("streamfhir-prov-" + a.record_id)
        entries.append({"fullUrl": self._urn(), "resource": {
            "resourceType": "Provenance",
            "id": prov_id,
            "meta": self._meta(pseudonymised=True),
            "text": narrative(("Record %s was imported from %s and assembled into FHIR by StreamFHIR %s%s" % (
                a.record_id, a.source or a.origin, VERSION, "; reviewer decision: " + decision if decision else ""))
                if a.origin else
                "Record %s was reported by a pseudonymous citizen scientist and assembled into FHIR by "
                "StreamFHIR %s%s" % (a.record_id, VERSION, "; reviewer decision: " + decision if decision else "")),
            "target": targets,
            "occurredDateTime": _iso(a.observed_at),
            "recorded": _iso(self._now()),
            "activity": {"coding": [{"system": DATA_OPERATION, "code": "CREATE", "display": "create"}]},
            "agent": agents,
            "entity": [{"role": "source", "what": _logical(SID_RECORD, a.record_id, display=(
                "Original source record (%s)" % (a.source or a.origin)) if a.origin else
                "Original citizen record" + (" (%s)" % a.source if a.source else ""))}],
        }, "request": {"method": "PUT", "url": "Provenance/" + prov_id}})
        return {"resourceType": "Bundle", "type": "transaction", "entry": entries}

    # ---------- UC-2 ----------
    def _flag_bundle(self, risk: SiteRisk, site: Site, active: bool, previous_start: Optional[str] = None,
                     if_match: Optional[str] = None) -> Dict[str, Any]:
        loc = self._location_entry(site)
        dev = self._device_entry()
        hazards = [f for f in risk.fired if f.kind == HAZARD]
        rules = "; ".join("%s %s (%s)" % (f.rule_id, f.title, f.corroboration) for f in hazards) or "none"
        lanes = " / ".join("%s %d" % (k, v) for k, v in risk.lane_points.items())
        evidence = sorted({rid for f in hazards if f.corroborated for rid in f.trusted_record_ids})
        flag_url = self._urn()
        if active:
            start = min(f.first_seen for f in hazards if f.corroborated)
            last = max(f.last_seen for f in hazards if f.corroborated)
            period = {"start": _iso(start), "end": _iso(last + timedelta(days=14))}
            expired = last + timedelta(days=14) < self._now()
            text = "%s at %s (%d corroborated of %d hazard points: %s). Rules: %s. Expires %s unless re-confirmed; expiry means " \
                   "no recent data, not clean water - only 2 trusted clear visits >= 7 days apart clear a hazard." % (
                LEVEL_DISPLAY[risk.level], site.name, risk.confirmed_hazard_points, risk.hazard_points, lanes, rules,
                period["end"][:10])
        else:
            expired = False
            period = {"end": _iso(risk.window_end)}
            if previous_start:
                period = {"start": previous_start, "end": _iso(risk.window_end)}
            cleared = [f.rule_id for f in hazards if not f.corroborated]
            text = "Stood down: no current corroborated health hazard at %s (%s)%s." % (
                site.name, LEVEL_DISPLAY[risk.level],
                "; unconfirmed signals remain: " + ", ".join(cleared) if cleared else "")
        flag = {
            "resourceType": "Flag",
            "meta": self._meta(PROFILE_FLAG),
            "text": narrative(text),
            "extension": [{"url": FLAG_DETAIL, "valueReference": _logical(SID_RECORD, rid, "Observation",
                                                                           "Citizen assessment " + rid)}
                          for rid in evidence] + [
                {"url": EXT_FLAG_RULE, "valueCodeableConcept": {"coding": [
                    {"system": CS_RULE, "code": f.rule_id, "display": f.title}]}}
                for f in hazards if active and f.corroborated],
            "identifier": [{"system": SID_FLAG, "value": site.site_id}],
            "status": "active" if active and not expired else "inactive",
            "category": [{"coding": [{"system": FLAG_CATEGORY, "code": "safety", "display": "Safety"}]}],
            "code": {"coding": [{"system": CS_RISK, "code": risk.level, "display": LEVEL_DISPLAY[risk.level]}],
                     "text": text},
            "subject": {"reference": loc["fullUrl"], "display": site.name},
            "period": period,
            "author": {"reference": dev["fullUrl"], "display": "StreamFHIR %s" % VERSION},
        }
        if expired:
            flag["text"] = narrative("EXPIRED on %s (no trusted report since): %s" % (period["end"][:10], text))
        if not flag["extension"]:
            del flag["extension"]
        prov_id = fhir_id("streamfhir-prov-flag-" + site.site_id)
        prov = {
            "resourceType": "Provenance", "id": prov_id, "meta": self._meta(),
            "text": narrative("Flag for %s computed by StreamFHIR %s from %d citizen records" % (
                site.name, VERSION, len(evidence))),
            "target": [{"reference": flag_url}],
            "recorded": _iso(self._now()),
            "activity": {"coding": [{"system": DATA_OPERATION, "code": "UPDATE" if not active else "CREATE",
                                     "display": "revise" if not active else "create"}]},
            "agent": [{"type": {"coding": [{"system": PARTICIPANT_TYPE, "code": "assembler", "display": "Assembler"}]},
                       "who": {"reference": dev["fullUrl"], "display": "StreamFHIR %s" % VERSION}}],
            "entity": [{"role": "source", "what": _logical(SID_RECORD, rid, "Observation")} for rid in evidence],
        }
        if not prov["entity"]:
            del prov["entity"]
        return {"resourceType": "Bundle", "type": "transaction", "entry": [
            loc, dev,
            {"fullUrl": flag_url, "resource": flag,
             "request": dict(self._upsert("Flag", SID_FLAG, site.site_id),
                             **({"ifMatch": 'W/"%s"' % if_match} if if_match else {}))},
            {"fullUrl": self._urn(), "resource": prov, "request": {"method": "PUT", "url": "Provenance/" + prov_id}},
        ]}

    def risk_bundle(self, risk: SiteRisk, site: Site) -> Optional[Dict[str, Any]]:
        """Active Flag for a site whose corroborated hazard reaches the threshold (else None)."""
        return self._flag_bundle(risk, site, active=True) if risk.needs_flag else None

    def stand_down_bundle(self, risk: SiteRisk, site: Site, previous_start: Optional[str] = None,
                          if_match: Optional[str] = None) -> Dict[str, Any]:
        """Set the site's existing Flag (same identifier) to inactive, keeping its original start.
        if_match = the versionId that was read, so a concurrent change is not overwritten."""
        return self._flag_bundle(risk, site, active=False, previous_start=previous_start, if_match=if_match)


# ---------- conformance resources (published in fhir/) ----------
def _cs(cs_id: str, url: str, name: str, title: str, description: str, concepts: List[Dict[str, str]],
        value_set: Optional[str] = None) -> Dict[str, Any]:
    cs = {"resourceType": "CodeSystem", "id": cs_id, "url": url, "version": VERSION, "name": name, "title": title,
          "status": "draft", "experimental": True, "date": DATE, "publisher": "StreamFHIR hackathon prototype",
          "contact": [{"name": "StreamFHIR team"}],
          "description": description + " Example canonical URL; these are NOT official HL7, LOINC or SNOMED CT codes.",
          "jurisdiction": [JURISDICTION_WORLD], "caseSensitive": True, "content": "complete",
          "count": len(concepts), "concept": concepts}
    if value_set:
        cs["valueSet"] = value_set
    return cs


def codesystem_resources() -> List[Dict[str, Any]]:
    indicators = [{"code": PANEL_CODE, "display": PANEL_DISPLAY,
                   "definition": "Grouping observation for one citizen visit to a stream site."}]
    indicators += [{"code": i.code, "display": i.display, "definition": i.definition} for i in INDICATORS.values()]
    answers = [{"code": answer_code(i, v), "display": "%s: %s" % (i.display, v),
                "definition": "Answer '%s' for indicator %s." % (v, i.code)}
               for i in INDICATORS.values() if i.kind == CATEGORY for v in i.answers]
    levels = [{"code": lvl, "display": LEVEL_DISPLAY[lvl], "definition": LEVEL_DEFINITION[lvl]}
              for lvl in (HIGH, VERIFY, MODERATE, LOW)]
    rules = [{"code": r.rule_id, "display": r.title,
              "definition": "[%s] When %s. %s Advice: %s" % (r.kind, r.condition_text, r.why, r.advice)} for r in RULES]
    return [
        _cs("stream-indicator", CS_INDICATOR, "StreamIndicator", "Citizen stream assessment indicators",
            "Indicators of a representative citizen-science stream assessment.", indicators, VS_INDICATOR),
        _cs("stream-answer", CS_ANSWER, "StreamAnswer", "Citizen stream assessment answers",
            "Answer codes for categorical stream assessment indicators.", answers),
        _cs("onehealth-risk-level", CS_RISK, "OneHealthRiskLevel", "StreamFHIR health hazard levels",
            "Site-level health hazard levels computed by the StreamFHIR rule engine.", levels),
        _cs("onehealth-risk-rule", CS_RULE, "OneHealthRiskRule", "StreamFHIR explainable rules",
            "The printed rules (hazard and ecological condition) used by the StreamFHIR rule engine.", rules),
    ]


def external_fragment_codesystems() -> List[Dict[str, Any]]:
    """Fragments (content = fragment) of the two European water vocabularies, holding only the codes StreamFHIR emits,
    so a FHIR validator knows these systems. The publishers own the codes; they publish no FHIR CodeSystem themselves."""
    def frag(cs_id, url, name, title, owner, index):
        concepts = [{"code": t[index][1], "display": t[index][2]} for t in EU_WATER_CROSSWALK.values()]
        return {"resourceType": "CodeSystem", "id": cs_id, "url": url, "name": name, "title": title, "status": "draft",
                "experimental": True, "date": DATE, "publisher": "StreamFHIR hackathon prototype (codes owned by %s)" % owner,
                "description": "Fragment of %s's vocabulary at %s: only the %d codes StreamFHIR emits, with the labels "
                               "the publisher shows (read 2026-09-29). Not an official FHIR representation." % (owner, url, len(concepts)),
                "jurisdiction": [{"coding": [{"system": "http://unstats.un.org/unsd/methods/m49/m49.htm", "code": "150",
                                              "display": "Europe"}]}], "caseSensitive": True, "content": "fragment",
                "count": len(concepts), "concept": concepts}
    return [frag("eea-wise-observedproperty-fragment", EEA_OBSERVED_PROPERTY, "EeaWiseObservedPropertyFragment",
                 "EEA WISE ObservedProperty (fragment)", "the European Environment Agency", 0),
            frag("sandre-parametre-fragment", SANDRE_PARAMETER, "SandreParametreFragment",
                 "Sandre parameters (fragment)", "Sandre (France)", 1)]


def valueset_resource() -> Dict[str, Any]:
    return {"resourceType": "ValueSet", "id": "stream-indicator", "url": VS_INDICATOR, "version": VERSION,
            "name": "StreamIndicator", "title": "Citizen stream assessment indicators", "status": "draft",
            "experimental": True, "date": DATE, "jurisdiction": [JURISDICTION_WORLD],
            "description": "All codes from the StreamFHIR stream-indicator CodeSystem (example canonical).",
            "compose": {"include": [{"system": CS_INDICATOR}]}}


def hazard_rule_valueset() -> Dict[str, Any]:
    return {"resourceType": "ValueSet", "id": "onehealth-hazard-rule", "url": VS_HAZARD_RULE, "version": VERSION,
            "name": "OneHealthHazardRule", "title": "StreamFHIR health-hazard rules", "status": "draft",
            "experimental": True, "date": DATE, "jurisdiction": [JURISDICTION_WORLD],
            "description": "The StreamFHIR rules that can raise a Flag (health hazard only; ecological-condition rules "
                           "never raise a warning). Example canonical.",
            "compose": {"include": [{"system": CS_RULE, "concept": [
                {"code": r.rule_id, "display": r.title} for r in RULES if r.kind == HAZARD]}]}}


def _common_elements() -> List[Dict[str, Any]]:
    return [
        {"id": "Observation.category", "path": "Observation.category", "min": 1,
         "slicing": {"discriminator": [{"type": "pattern", "path": "$this"}], "rules": "open"}},
        {"id": "Observation.category:survey", "path": "Observation.category", "sliceName": "survey", "min": 0, "max": "1",
         "short": "Citizen observation", "patternCodeableConcept": {"coding": [{"system": OBS_CATEGORY, "code": "survey"}]}},
        {"id": "Observation.category:laboratory", "path": "Observation.category", "sliceName": "laboratory", "min": 0,
         "max": "1", "short": "Agency laboratory sampling",
         "patternCodeableConcept": {"coding": [{"system": OBS_CATEGORY, "code": "laboratory"}]}},
        # exactly one coding from the StreamFHIR indicator ValueSet; other codings (LOINC, EEA WISE, Sandre) are
        # translations of the same concept, allowed by the open slicing
        {"id": "Observation.code.coding", "path": "Observation.code.coding", "min": 1,
         "slicing": {"discriminator": [{"type": "value", "path": "system"}], "rules": "open"}},
        {"id": "Observation.code.coding:streamfhir", "path": "Observation.code.coding", "sliceName": "streamfhir",
         "min": 1, "max": "1", "binding": {"strength": "required", "valueSet": VS_INDICATOR}},
        {"id": "Observation.code.coding:streamfhir.system", "path": "Observation.code.coding.system", "min": 1,
         "fixedUri": CS_INDICATOR},
        {"id": "Observation.code.coding:streamfhir.code", "path": "Observation.code.coding.code", "min": 1},
        {"id": "Observation.subject", "path": "Observation.subject", "min": 1,
         "type": [{"code": "Reference", "targetProfile": ["http://hl7.org/fhir/StructureDefinition/Location"]}],
         "short": "The stream site"},
        {"id": "Observation.effective[x]", "path": "Observation.effective[x]", "min": 1, "type": [{"code": "dateTime"}]},
        {"id": "Observation.performer", "path": "Observation.performer", "min": 1, "max": "1",
         "short": "Pseudonymous observer (logical reference by identifier only)"},
        {"id": "Observation.performer.identifier", "path": "Observation.performer.identifier", "min": 1},
    ]


def _sd(sd_id: str, url: str, name: str, title: str, description: str, extra: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "resourceType": "StructureDefinition", "id": sd_id, "url": url, "version": VERSION, "name": name,
        "title": title, "status": "draft", "experimental": True, "date": DATE, "description": description,
        "fhirVersion": "4.0.1", "kind": "resource", "abstract": False, "type": "Observation",
        "baseDefinition": "http://hl7.org/fhir/StructureDefinition/Observation", "derivation": "constraint",
        "differential": {"element": [{"id": "Observation", "path": "Observation", "short": title, "constraint": [
            {"key": "sio-1", "severity": "error", "source": url,
             "human": "Category is survey (citizen observation) or laboratory (agency sampling)",
             "expression": "category.coding.where(system = '%s' and (code = 'survey' or code = 'laboratory')).exists()"
                           % OBS_CATEGORY}]}]
                         + _common_elements() + extra}}


def flag_rule_extension() -> Dict[str, Any]:
    """Extension that cites, as a code from the onehealth-risk-rule CodeSystem, a rule that raised the Flag."""
    return {
        "resourceType": "StructureDefinition", "id": "flag-rule", "url": EXT_FLAG_RULE, "version": VERSION,
        "name": "FlagRule", "title": "StreamFHIR rule cited by a Flag", "status": "draft", "experimental": True,
        "date": DATE, "description": "A corroborated StreamFHIR health-hazard rule that raised this Flag, coded from the "
        "onehealth-risk-rule CodeSystem. One extension per rule. Example canonical, prototype only.",
        "fhirVersion": "4.0.1", "kind": "complex-type", "abstract": False,
        "context": [{"type": "element", "expression": "Flag"}], "type": "Extension",
        "baseDefinition": "http://hl7.org/fhir/StructureDefinition/Extension", "derivation": "constraint",
        "differential": {"element": [
            {"id": "Extension", "path": "Extension", "short": "Rule that raised the Flag", "min": 0, "max": "*"},
            {"id": "Extension.extension", "path": "Extension.extension", "max": "0"},
            {"id": "Extension.url", "path": "Extension.url", "fixedUri": EXT_FLAG_RULE},
            {"id": "Extension.value[x]", "path": "Extension.value[x]", "min": 1,
             "type": [{"code": "CodeableConcept"}],
             "binding": {"strength": "required", "valueSet": VS_HAZARD_RULE}},
            {"id": "Extension.value[x].coding", "path": "Extension.value[x].coding", "min": 1, "max": "1"},
            {"id": "Extension.value[x].coding.system", "path": "Extension.value[x].coding.system", "min": 1,
             "fixedUri": CS_RULE},
            {"id": "Extension.value[x].coding.code", "path": "Extension.value[x].coding.code", "min": 1},
        ]}}


def risk_level_valueset() -> Dict[str, Any]:
    return {"resourceType": "ValueSet", "id": "onehealth-risk-level", "url": VS_RISK_LEVEL, "version": VERSION,
            "name": "OneHealthRiskLevel", "title": "StreamFHIR health hazard levels", "status": "draft",
            "experimental": True, "date": DATE, "jurisdiction": [JURISDICTION_WORLD],
            "description": "All health hazard levels of the StreamFHIR rule engine (example canonical).",
            "compose": {"include": [{"system": CS_RISK}]}}


def site_flag_profile() -> Dict[str, Any]:
    """Flag profile: a safety warning about a place, citing its evidence and the rules that raised it."""
    return {
        "resourceType": "StructureDefinition", "id": "stream-site-flag", "url": PROFILE_FLAG, "version": VERSION,
        "name": "StreamSiteFlag", "title": "StreamFHIR site warning (Flag)", "status": "draft", "experimental": True,
        "date": DATE, "description": "A One Health safety warning about a stream site (a Location, not a patient). "
        "An active warning must cite at least one corroborated hazard rule. Example canonical, prototype only.",
        "fhirVersion": "4.0.1", "kind": "resource", "abstract": False, "type": "Flag",
        "baseDefinition": "http://hl7.org/fhir/StructureDefinition/Flag", "derivation": "constraint",
        "differential": {"element": [
            {"id": "Flag", "path": "Flag", "short": "Site warning",
             "constraint": [{"key": "ssf-1", "severity": "error",
                             "human": "An active site warning cites at least one hazard rule (flag-rule extension)",
                             "expression": "status != 'active' or extension.where(url = '%s').exists()" % EXT_FLAG_RULE,
                             "source": PROFILE_FLAG}]},
            {"id": "Flag.extension", "path": "Flag.extension",
             "slicing": {"discriminator": [{"type": "value", "path": "url"}], "rules": "open"}},
            {"id": "Flag.extension:evidence", "path": "Flag.extension", "sliceName": "evidence", "min": 0, "max": "*",
             "type": [{"code": "Extension", "profile": [FLAG_DETAIL]}]},
            {"id": "Flag.extension:rule", "path": "Flag.extension", "sliceName": "rule", "min": 0, "max": "*",
             "type": [{"code": "Extension", "profile": [EXT_FLAG_RULE]}]},
            {"id": "Flag.identifier", "path": "Flag.identifier", "min": 1},
            {"id": "Flag.category", "path": "Flag.category", "min": 1,
             "slicing": {"discriminator": [{"type": "pattern", "path": "$this"}], "rules": "open"}},
            {"id": "Flag.category:safety", "path": "Flag.category", "sliceName": "safety", "min": 1, "max": "1",
             "patternCodeableConcept": {"coding": [{"system": FLAG_CATEGORY, "code": "safety"}]}},
            {"id": "Flag.code", "path": "Flag.code", "binding": {"strength": "required", "valueSet": VS_RISK_LEVEL}},
            {"id": "Flag.subject", "path": "Flag.subject",
             "type": [{"code": "Reference", "targetProfile": ["http://hl7.org/fhir/StructureDefinition/Location"]}],
             "short": "The stream site the warning is about"},
            {"id": "Flag.period", "path": "Flag.period", "min": 1},
            {"id": "Flag.period.end", "path": "Flag.period.end", "min": 1, "short": "Expiry (14 days after the last trusted report)"},
            {"id": "Flag.author", "path": "Flag.author", "min": 1},
        ]}}


def structuredefinition_resources() -> List[Dict[str, Any]]:
    return [flag_rule_extension(), site_flag_profile(),
        _sd("stream-assessment-panel", PROFILE_PANEL, "StreamAssessmentPanel", "Citizen stream assessment panel",
            "One citizen visit to a stream site: groups the indicator Observations via hasMember and carries no value. "
            "Example canonical, prototype only.",
            [{"id": "Observation.value[x]", "path": "Observation.value[x]", "max": "0"},
             {"id": "Observation.hasMember", "path": "Observation.hasMember", "min": 1}]),
        _sd("stream-indicator-observation", PROFILE_OBS, "StreamIndicatorObservation",
            "Citizen stream indicator observation",
            "One indicator (score, reading or observation) from a citizen visit to a stream site. "
            "Example canonical, prototype only.",
            [{"id": "Observation.value[x]", "path": "Observation.value[x]", "min": 1},
             {"id": "Observation.hasMember", "path": "Observation.hasMember", "max": "0"}]),
    ]


def capability_statement() -> Dict[str, Any]:
    """What StreamFHIR needs from a receiving FHIR server (kind = requirements)."""
    return _with_text(_capability_statement())


def _capability_statement() -> Dict[str, Any]:
    def res(rtype, interactions, params=(), **flags):
        r = {"type": rtype, "interaction": [{"code": i} for i in interactions]}
        r.update(flags)
        if params:
            r["searchParam"] = [{"name": n, "type": t} for n, t in params]
        return r
    return {
        "resourceType": "CapabilityStatement", "id": "streamfhir-client-requirements",
        "url": BASE + "/CapabilityStatement/streamfhir-client-requirements", "version": VERSION,
        "name": "StreamFHIRClientRequirements", "title": "What StreamFHIR needs from a FHIR R4 server",
        "status": "draft", "experimental": True, "date": DATE, "kind": "requirements", "fhirVersion": "4.0.1",
        "format": ["json"],
        "description": "A receiving FHIR R4 server must accept transaction Bundles with conditional create "
                       "(ifNoneExist on identifier) for Location and Device, conditional update by identifier for Observation, Media and Flag, "
                       "and update-as-create for Provenance. Consumers find "
                       "warnings with Flag?category=safety&status=active or subscribe to them (see Subscription example).",
        "rest": [{"mode": "server", "interaction": [{"code": "transaction"}], "resource": [
            res("Location", ["create", "read", "search-type"], (("identifier", "token"),), conditionalCreate=True),
            res("Device", ["create", "read"], (("identifier", "token"),), conditionalCreate=True),
            res("Media", ["update", "read"], (("identifier", "token"),), conditionalUpdate=True),
            res("Observation", ["update", "read", "search-type"],
                (("identifier", "token"), ("subject", "reference"), ("code", "token")), conditionalUpdate=True),
            res("Provenance", ["update", "read"], updateCreate=True),
            res("Flag", ["update", "read", "search-type"],
                (("identifier", "token"), ("category", "token"), ("status", "token"), ("subject", "reference")),
                conditionalUpdate=True),
        ]}]}


def subscription_example() -> Dict[str, Any]:
    return _with_text({
        "resourceType": "Subscription", "id": "streamfhir-safety-flags", "status": "requested",
        "reason": "Notify a public-health or environment system when StreamFHIR raises or changes a site warning.",
        "criteria": "Flag?category=http://terminology.hl7.org/CodeSystem/flag-category|safety",
        "channel": {"type": "rest-hook", "endpoint": "https://health-system.example.org/fhir-notify",
                    "payload": "application/fhir+json"}})


def _with_text(r: Dict[str, Any]) -> Dict[str, Any]:
    if "text" in r:
        return r
    label = r.get("title") or r.get("reason") or r["id"]
    out = {k: v for k, v in r.items() if k in ("resourceType", "id")}
    out["text"] = narrative("%s: %s" % (r["resourceType"], label))
    out.update({k: v for k, v in r.items() if k not in ("resourceType", "id")})
    return out


def concept_map() -> Dict[str, Any]:
    """StreamFHIR indicators -> EEA WISE determinands and French Sandre parameters (what the mapper adds as extra codings)."""
    def group(target_index, target_system, target_version):
        els = []
        for code, targets in EU_WATER_CROSSWALK.items():
            system, tcode, display = targets[target_index]
            t = {"code": tcode, "display": display, "equivalence": "equivalent"}
            if code == "nitrate":
                t["dependsOn"] = [{"property": CS_INDICATOR + "#nitrate-basis", "system": CS_ANSWER, "value": "as-NO3"}]
                t["comment"] = "Only readings expressed as NO3; readings as N are not mapped (1 mg/L as N = 4.43 mg/L as NO3)."
            if code == "phosphate" and target_index == 0:
                t["equivalence"] = "inexact"
                t["comment"] = ("Same substance, different basis: EEA Waterbase reports this determinand as mg{P}/L, StreamFHIR "
                                "as mg PO4/L (1 mg/L as P = 3.066 mg/L as PO4). StreamFHIR therefore does not add this code "
                                "to Observations; convert before pooling.")
            elif code == "phosphate":
                t["comment"] = "Both as PO4 (Sandre 1433 is orthophosphate, reported in mg(PO4)/L)."
            els.append({"code": code, "display": INDICATORS[code].display, "target": [t]})
        g = {"source": CS_INDICATOR, "target": target_system, "element": els}
        if target_version:
            g["targetVersion"] = target_version
        return g
    return {
        "resourceType": "ConceptMap", "id": "stream-indicator-to-eu-water", "url": CM_EU_WATER, "version": VERSION,
        "name": "StreamIndicatorToEuWater", "title": "StreamFHIR indicators to EEA WISE determinands and Sandre parameters",
        "status": "draft", "experimental": True, "date": DATE, "publisher": "StreamFHIR (hackathon prototype)",
        "jurisdiction": [JURISDICTION_WORLD],
        "description": "Maps the four measured StreamFHIR indicators to the codes European water agencies already report with: "
                       "EEA WISE ObservedProperty (Waterbase, EU-wide) and Sandre parameters (France). StreamFHIR adds these "
                       "codes to each Observation, so a health system can query citizen and agency readings with one code.",
        "sourceUri": VS_INDICATOR,
        "group": [group(0, EEA_OBSERVED_PROPERTY, None), group(1, SANDRE_PARAMETER, None)],
    }


def conformance_resources() -> List[Dict[str, Any]]:
    return [_with_text(r) for r in codesystem_resources() + external_fragment_codesystems()
            + [valueset_resource(), hazard_rule_valueset(), risk_level_valueset()] + structuredefinition_resources()
            + [concept_map()]]


def conformance_bundle() -> Dict[str, Any]:
    """Transaction that PUTs the prototype CodeSystems, ValueSet and profiles under fixed server ids."""
    entries = []
    for r in conformance_resources():
        rid = "streamfhir-" + r["id"]
        entries.append({"fullUrl": "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, r["url"])),
                        "resource": dict(r, id=rid),
                        "request": {"method": "PUT", "url": "%s/%s" % (r["resourceType"], rid)}})
    return {"resourceType": "Bundle", "type": "transaction", "entry": entries}
