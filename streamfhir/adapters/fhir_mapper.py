"""Adapter: domain objects -> HL7 FHIR R4 JSON (transaction Bundles + conformance resources).

All StreamFHIR-specific codes live in CodeSystems under an *example* canonical base
(https://example.org/...). They are prototype codes, not HL7, LOINC or SNOMED CT codes.
The only external code systems used are core HL7 terminology and UCUM, where the codes
are part of the FHIR R4 specification itself.
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from ..domain.indicators import BOOLEAN, CATEGORY, INDICATORS, PANEL_CODE, PANEL_DISPLAY, QUANTITY, SCORE, answer_code
from ..domain.risk import HIGH, LOW, MODERATE, RULES, SiteRisk
from ..domain.sites import Site
from ..domain.validation import REVIEW, ValidationReport

VERSION = "0.1.0"
BASE = "https://example.org/fhir/streamfhir"   # example canonical - replace when published
CS_INDICATOR = BASE + "/CodeSystem/stream-indicator"
CS_ANSWER = BASE + "/CodeSystem/stream-answer"
CS_RISK = BASE + "/CodeSystem/onehealth-risk"
VS_INDICATOR = BASE + "/ValueSet/stream-indicator"
PROFILE_OBS = BASE + "/StructureDefinition/stream-assessment-observation"
SID_SITE = BASE + "/sid/site"
SID_OBSERVER = BASE + "/sid/observer"
SID_RECORD = BASE + "/sid/record"
SID_DEVICE = BASE + "/sid/device"
SID_FLAG = BASE + "/sid/flag"

UCUM = "http://unitsofmeasure.org"
OBS_CATEGORY = "http://terminology.hl7.org/CodeSystem/observation-category"
ACT_REASON = "http://terminology.hl7.org/CodeSystem/v3-ActReason"
DATA_OPERATION = "http://terminology.hl7.org/CodeSystem/v3-DataOperation"
PARTICIPANT_TYPE = "http://terminology.hl7.org/CodeSystem/provenance-participant-type"
FLAG_CATEGORY = "http://terminology.hl7.org/CodeSystem/flag-category"

UNIT_DISPLAY = {"Cel": "°C", "[pH]": "pH", "mg/L": "mg/L", "cm": "cm"}
TEST_DATA = {"system": ACT_REASON, "code": "HTEST", "display": "test health data"}
LEVEL_DISPLAY = {HIGH: "High One Health risk", MODERATE: "Moderate One Health risk", LOW: "Low One Health risk"}
DEVICE_ID = "streamfhir-rule-engine"


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


class FhirMapper:
    def __init__(self, id_factory: Optional[Callable[[], str]] = None,
                 now_fn: Optional[Callable[[], datetime]] = None):
        self._new_id = id_factory or (lambda: str(uuid.uuid4()))
        self._now = now_fn or (lambda: datetime.now(timezone.utc).replace(microsecond=0))

    # ---------- helpers ----------
    def _urn(self) -> str:
        return "urn:uuid:" + self._new_id()

    @staticmethod
    def _meta(profile: Optional[str] = None) -> Dict[str, Any]:
        meta: Dict[str, Any] = {"security": [dict(TEST_DATA)]}
        if profile:
            meta["profile"] = [profile]
        return meta

    def _location_entry(self, site: Site) -> Dict[str, Any]:
        return {
            "fullUrl": self._urn(),
            "resource": {
                "resourceType": "Location",
                "meta": self._meta(),
                "text": narrative("Stream monitoring site %s (%s)" % (site.name, site.site_id)),
                "identifier": [{"system": SID_SITE, "value": site.site_id}],
                "status": "active",
                "name": site.name,
                "description": site.description or "Citizen-science stream monitoring site on %s" % site.water_body,
                "mode": "instance",
                "position": {"longitude": site.lon, "latitude": site.lat},
            },
            "request": {"method": "POST", "url": "Location",
                        "ifNoneExist": "identifier=%s|%s" % (SID_SITE, site.site_id)},
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
            "request": {"method": "POST", "url": "Device",
                        "ifNoneExist": "identifier=%s|%s" % (SID_DEVICE, DEVICE_ID)},
        }

    @staticmethod
    def _value(code: str, value: Any) -> Dict[str, Any]:
        ind = INDICATORS[code]
        if ind.kind == SCORE:
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
    def assessment_bundle(self, report: ValidationReport, site: Site) -> Optional[Dict[str, Any]]:
        a = report.assessment
        if a is None:
            return None
        status = "preliminary" if report.status == REVIEW else "final"
        record_notes = ["Needs review: " + i.message for i in report.issues if not i.field.startswith("values.")]
        performer = {"identifier": {"system": SID_OBSERVER, "value": a.observer},
                     "display": "Pseudonymous citizen scientist"}
        loc = self._location_entry(site)
        dev = self._device_entry()
        loc_ref = {"reference": loc["fullUrl"], "display": site.name}
        entries: List[Dict[str, Any]] = [loc, dev]

        media_refs = []
        for n, url in enumerate(a.photos, 1):
            e = {"fullUrl": self._urn(), "resource": {
                "resourceType": "Media", "meta": self._meta(), "status": "completed",
                "text": narrative("Citizen photo %d of %s, record %s" % (n, site.name, a.record_id)),
                "identifier": [{"system": SID_RECORD, "value": "%s#photo-%d" % (a.record_id, n)}],
                "subject": loc_ref, "createdDateTime": _iso(a.observed_at),
                "content": {"contentType": "image/jpeg", "url": url, "title": "Citizen photo %d" % n}},
                "request": {"method": "POST", "url": "Media"}}
            entries.append(e)
            media_refs.append({"reference": e["fullUrl"]})

        member_entries = []
        for code, value in a.values.items():
            ind = INDICATORS[code]
            notes = ["Needs review: " + i.message for i in report.warnings_for(code)] + record_notes
            obs: Dict[str, Any] = {
                "resourceType": "Observation",
                "meta": self._meta(PROFILE_OBS),
                "text": narrative("%s: %s%s at %s (%s, record %s)" % (
                    ind.display, _plain(value), (" " + UNIT_DISPLAY.get(ind.unit, ind.unit)) if ind.unit else "",
                    site.name, status, a.record_id)),
                "identifier": [{"system": SID_RECORD, "value": "%s#%s" % (a.record_id, code)}],
                "status": status,
                "category": [{"coding": [{"system": OBS_CATEGORY, "code": "survey", "display": "Survey"}]}],
                "code": {"coding": [{"system": CS_INDICATOR, "code": code, "display": ind.display}], "text": ind.display},
                "subject": loc_ref,
                "effectiveDateTime": _iso(a.observed_at),
                "performer": [performer],
            }
            obs.update(self._value(code, value))
            if notes:
                obs["note"] = [{"text": t} for t in notes]
            if media_refs:
                obs["derivedFrom"] = media_refs
            member_entries.append({"fullUrl": self._urn(), "resource": obs,
                                   "request": {"method": "POST", "url": "Observation"}})

        panel = {
            "resourceType": "Observation",
            "meta": self._meta(PROFILE_OBS),
            "text": narrative("Citizen stream assessment of %s on %s: %d indicators (%s, record %s)" % (
                site.name, _iso(a.observed_at), len(member_entries), status, a.record_id)),
            "identifier": [{"system": SID_RECORD, "value": a.record_id}],
            "status": status,
            "category": [{"coding": [{"system": OBS_CATEGORY, "code": "survey", "display": "Survey"}]}],
            "code": {"coding": [{"system": CS_INDICATOR, "code": PANEL_CODE, "display": PANEL_DISPLAY}], "text": PANEL_DISPLAY},
            "subject": loc_ref,
            "effectiveDateTime": _iso(a.observed_at),
            "performer": [performer],
            "hasMember": [{"reference": e["fullUrl"]} for e in member_entries],
        }
        if report.issues:
            panel["note"] = [{"text": "Needs review: " + i.message} for i in report.issues]
        panel_entry = {"fullUrl": self._urn(), "resource": panel, "request": {"method": "POST", "url": "Observation"}}
        entries += [panel_entry] + member_entries

        targets = [{"reference": e["fullUrl"]} for e in [panel_entry] + member_entries] + media_refs
        entries.append({"fullUrl": self._urn(), "resource": {
            "resourceType": "Provenance",
            "meta": self._meta(),
            "text": narrative("Record %s was reported by a pseudonymous citizen scientist and assembled into FHIR by StreamFHIR %s" % (a.record_id, VERSION)),
            "target": targets,
            "occurredDateTime": _iso(a.observed_at),
            "recorded": _iso(self._now()),
            "activity": {"coding": [{"system": DATA_OPERATION, "code": "CREATE", "display": "create"}]},
            "agent": [
                {"type": {"coding": [{"system": PARTICIPANT_TYPE, "code": "author", "display": "Author"}]},
                 "who": performer},
                {"type": {"coding": [{"system": PARTICIPANT_TYPE, "code": "assembler", "display": "Assembler"}]},
                 "who": {"reference": dev["fullUrl"], "display": "StreamFHIR %s" % VERSION}},
            ]}, "request": {"method": "POST", "url": "Provenance"}})
        return {"resourceType": "Bundle", "type": "transaction", "entry": entries}

    # ---------- UC-2 ----------
    def risk_bundle(self, risk: SiteRisk, site: Site) -> Optional[Dict[str, Any]]:
        if not risk.needs_flag:
            return None
        loc = self._location_entry(site)
        dev = self._device_entry()
        rules = ", ".join("%s %s%s" % (f.rule_id, f.title, "" if f.corroborated else " (single report - verify)")
                          for f in risk.fired)
        lanes = " / ".join("%s %d" % (k, v) for k, v in risk.lane_points.items())
        flag = {
            "resourceType": "Flag",
            "meta": self._meta(),
            "text": narrative("%s at %s: %d points. Rules: %s." % (LEVEL_DISPLAY[risk.level], site.name, risk.total_points, rules)),
            "identifier": [{"system": SID_FLAG, "value": "%s-%s" % (site.site_id, risk.window_end.date().isoformat())}],
            "status": "active",
            "category": [{"coding": [{"system": FLAG_CATEGORY, "code": "safety", "display": "Safety"}]}],
            "code": {"coding": [{"system": CS_RISK, "code": risk.level, "display": LEVEL_DISPLAY[risk.level]}],
                     "text": "%s at %s (%d points: %s). Rules: %s."
                             % (LEVEL_DISPLAY[risk.level], site.name, risk.total_points, lanes, rules)},
            "subject": {"reference": loc["fullUrl"], "display": site.name},
            "period": {"start": _iso(risk.window_end)},
            "author": {"reference": dev["fullUrl"], "display": "StreamFHIR %s" % VERSION},
        }
        return {"resourceType": "Bundle", "type": "transaction", "entry": [
            loc, dev, {"fullUrl": self._urn(), "resource": flag, "request": {"method": "POST", "url": "Flag"}}]}


# ---------- conformance resources (published in fhir/) ----------
def _cs(cs_id: str, url: str, name: str, title: str, description: str, concepts: List[Dict[str, str]]) -> Dict[str, Any]:
    return {"resourceType": "CodeSystem", "id": cs_id, "url": url, "version": VERSION, "name": name, "title": title,
            "status": "draft", "experimental": True, "publisher": "StreamFHIR hackathon prototype",
            "description": description + " Example canonical URL; these are NOT official HL7, LOINC or SNOMED CT codes.",
            "caseSensitive": True, "content": "complete", "count": len(concepts), "concept": concepts}


def codesystem_resources() -> List[Dict[str, Any]]:
    indicators = [{"code": PANEL_CODE, "display": PANEL_DISPLAY,
                   "definition": "Grouping observation for one citizen visit to a stream site."}]
    indicators += [{"code": i.code, "display": i.display, "definition": i.definition} for i in INDICATORS.values()]
    answers = [{"code": answer_code(i, v), "display": "%s: %s" % (i.display, v),
                "definition": "Answer '%s' for indicator %s." % (v, i.code)}
               for i in INDICATORS.values() if i.kind == CATEGORY for v in i.answers]
    risk = [{"code": lvl, "display": LEVEL_DISPLAY[lvl], "definition": d} for lvl, d in (
        (HIGH, "Total >= 6 points or any One Health lane >= 4 points in the 14-day window."),
        (MODERATE, "Total 3-5 points in the 14-day window."),
        (LOW, "Total 0-2 points in the 14-day window."))]
    risk += [{"code": r.rule_id, "display": r.title, "definition": "When %s. %s" % (r.condition_text, r.why)} for r in RULES]
    return [
        _cs("stream-indicator", CS_INDICATOR, "StreamIndicator", "Citizen stream assessment indicators",
            "Indicators of a representative citizen-science stream assessment.", indicators),
        _cs("stream-answer", CS_ANSWER, "StreamAnswer", "Citizen stream assessment answers",
            "Answer codes for categorical stream assessment indicators.", answers),
        _cs("onehealth-risk", CS_RISK, "OneHealthRisk", "StreamFHIR One Health risk levels and rules",
            "Risk levels and explainable rules used by the StreamFHIR rule engine.", risk),
    ]


def valueset_resource() -> Dict[str, Any]:
    return {"resourceType": "ValueSet", "id": "stream-indicator", "url": VS_INDICATOR, "version": VERSION,
            "name": "StreamIndicator", "title": "Citizen stream assessment indicators", "status": "draft",
            "experimental": True, "description": "All codes from the StreamFHIR stream-indicator CodeSystem (example canonical).",
            "compose": {"include": [{"system": CS_INDICATOR}]}}


def structuredefinition_resource() -> Dict[str, Any]:
    return {
        "resourceType": "StructureDefinition", "id": "stream-assessment-observation", "url": PROFILE_OBS,
        "version": VERSION, "name": "StreamAssessmentObservation", "title": "Citizen stream assessment Observation",
        "status": "draft", "experimental": True,
        "description": "Minimal profile: an Observation about a stream site (Location), made by a pseudonymous citizen "
                       "scientist, coded from the StreamFHIR indicator value set. Example canonical, prototype only.",
        "fhirVersion": "4.0.1", "kind": "resource", "abstract": False, "type": "Observation",
        "baseDefinition": "http://hl7.org/fhir/StructureDefinition/Observation", "derivation": "constraint",
        "differential": {"element": [
            {"id": "Observation", "path": "Observation", "short": "Citizen stream assessment observation"},
            {"id": "Observation.category", "path": "Observation.category", "min": 1,
             "short": "Always includes observation-category#survey"},
            {"id": "Observation.code", "path": "Observation.code",
             "binding": {"strength": "required", "valueSet": VS_INDICATOR}},
            {"id": "Observation.subject", "path": "Observation.subject", "min": 1,
             "type": [{"code": "Reference", "targetProfile": ["http://hl7.org/fhir/StructureDefinition/Location"]}],
             "short": "The stream site"},
            {"id": "Observation.effective[x]", "path": "Observation.effective[x]", "min": 1,
             "type": [{"code": "dateTime"}]},
            {"id": "Observation.performer", "path": "Observation.performer", "min": 1, "max": "1",
             "short": "Pseudonymous observer (logical reference by identifier only)"},
        ]},
    }


def conformance_bundle() -> Dict[str, Any]:
    """Transaction that PUTs the prototype CodeSystems, ValueSet and profile under fixed server ids."""
    resources = codesystem_resources() + [valueset_resource(), structuredefinition_resource()]
    entries = []
    for r in resources:
        rid = "streamfhir-" + r["id"]
        entries.append({"fullUrl": "%s/%s" % (r["resourceType"], rid),
                        "resource": dict(r, id=rid),
                        "request": {"method": "PUT", "url": "%s/%s" % (r["resourceType"], rid)}})
    return {"resourceType": "Bundle", "type": "transaction", "entry": entries}
