"""Adapter: domain objects -> HL7 FHIR R4 JSON (transaction Bundles + conformance resources).

All StreamFHIR-specific codes live in CodeSystems under an *example* canonical base
(https://example.org/...). They are prototype codes, not HL7, LOINC or SNOMED CT codes.
External codes used: core HL7 terminology, UCUM, and exactly one LOINC code
(9480-5 "Nitrate [Mass/volume] in Water", confirmed with a $lookup on tx.fhir.org,
LOINC 2.82) - added only when the reading is expressed as NO3.
"""
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from ..domain.indicators import (BOOLEAN, CATEGORY, COUNT, INDICATORS, PANEL_CODE, PANEL_DISPLAY, QUANTITY, SCORE,
                                 answer_code)
from ..domain.risk import CONFIRM, HAZARD, HIGH, LOW, MODERATE, REJECT, RULES, VERIFY, SiteRisk
from ..domain.sites import Site
from ..domain.validation import REVIEW, ValidationReport

VERSION = "0.2.0"
DATE = "2026-09-29"
BASE = "https://example.org/fhir/streamfhir"   # example canonical - replace when published
CS_INDICATOR = BASE + "/CodeSystem/stream-indicator"
CS_ANSWER = BASE + "/CodeSystem/stream-answer"
CS_RISK = BASE + "/CodeSystem/onehealth-risk-level"
CS_RULE = BASE + "/CodeSystem/onehealth-risk-rule"
VS_INDICATOR = BASE + "/ValueSet/stream-indicator"
PROFILE_PANEL = BASE + "/StructureDefinition/stream-assessment-panel"
PROFILE_OBS = BASE + "/StructureDefinition/stream-indicator-observation"
SID_SITE = BASE + "/sid/site"
SID_OBSERVER = BASE + "/sid/observer"
SID_REVIEWER = BASE + "/sid/reviewer"
SID_RECORD = BASE + "/sid/record"
SID_DEVICE = BASE + "/sid/device"
SID_FLAG = BASE + "/sid/flag"

UCUM = "http://unitsofmeasure.org"
LOINC = "http://loinc.org"
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
                 now_fn: Optional[Callable[[], datetime]] = None):
        self._new_id = id_factory or (lambda: str(uuid.uuid4()))
        self._now = now_fn or (lambda: datetime.now(timezone.utc).replace(microsecond=0))

    # ---------- helpers ----------
    def _urn(self) -> str:
        return "urn:uuid:" + self._new_id()

    @staticmethod
    def _meta(profile: Optional[str] = None, pseudonymised: bool = False) -> Dict[str, Any]:
        sec = [dict(TEST_DATA), dict(UNRESTRICTED)] + ([dict(PSEUDONYMISED)] if pseudonymised else [])
        meta: Dict[str, Any] = {"security": sec}
        if profile:
            meta["profile"] = [profile]
        return meta

    @staticmethod
    def _create(rtype: str, system: str, value: str) -> Dict[str, str]:
        """Conditional create: re-sending the same record never duplicates resources."""
        return {"method": "POST", "url": rtype, "ifNoneExist": "identifier=%s|%s" % (system, value)}

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
    def assessment_bundle(self, report: ValidationReport, site: Site,
                          decision: Optional[str] = None) -> Optional[Dict[str, Any]]:
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
                     "display": "Pseudonymous citizen scientist"}
        loc = self._location_entry(site)
        dev = self._device_entry()
        loc_ref = {"reference": loc["fullUrl"], "display": site.name}
        entries: List[Dict[str, Any]] = [loc, dev]

        media_refs = []
        for n, url in enumerate(a.photos, 1):
            mid = "%s#photo-%d" % (a.record_id, n)
            e = {"fullUrl": self._urn(), "resource": {
                "resourceType": "Media", "meta": self._meta(), "status": "completed",
                "text": narrative("Citizen photo %d of %s, record %s" % (n, site.name, a.record_id)),
                "identifier": [{"system": SID_RECORD, "value": mid}],
                "type": {"coding": [{"system": MEDIA_TYPE, "code": "image", "display": "Image"}]},
                "subject": loc_ref, "createdDateTime": _iso(a.observed_at),
                "content": {"contentType": "image/jpeg", "url": url, "title": "Citizen photo %d" % n}},
                "request": self._create("Media", SID_RECORD, mid)}
            entries.append(e)
            media_refs.append({"reference": e["fullUrl"]})

        category = [{"coding": [{"system": OBS_CATEGORY, "code": "survey", "display": "Survey"}]}]
        member_entries = []
        for code, value in a.values.items():
            ind = INDICATORS[code]
            notes = [prefix + i.message for i in report.warnings_for(code)] + record_notes
            coding = [{"system": CS_INDICATOR, "code": code, "display": ind.display}]
            if code == "nitrate" and a.values.get("nitrate-basis") == "as-NO3":
                coding.append(dict(NITRATE_LOINC))
            oid = "%s#%s" % (a.record_id, code)
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
            obs.update(self._value(code, value))
            if notes:
                obs["note"] = [{"text": t} for t in notes]
            if media_refs and code in PHOTO_EVIDENCE:
                obs["derivedFrom"] = media_refs
            member_entries.append({"fullUrl": self._urn(), "resource": obs,
                                   "request": self._create("Observation", SID_RECORD, oid)})

        panel = {
            "resourceType": "Observation",
            "meta": self._meta(PROFILE_PANEL, pseudonymised=True),
            "text": narrative("Citizen stream assessment of %s on %s: %d indicators (%s, record %s)" % (
                site.name, _iso(a.observed_at), len(member_entries), status, a.record_id)),
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
                       "request": self._create("Observation", SID_RECORD, a.record_id)}
        entries += [panel_entry] + member_entries

        targets = [{"reference": e["fullUrl"]} for e in [panel_entry] + member_entries] + media_refs
        agents = [
            {"type": {"coding": [{"system": PARTICIPANT_TYPE, "code": "author", "display": "Author"}]}, "who": performer},
            {"type": {"coding": [{"system": PARTICIPANT_TYPE, "code": "assembler", "display": "Assembler"}]},
             "who": {"reference": dev["fullUrl"], "display": "StreamFHIR %s" % VERSION}},
        ]
        if decision in (CONFIRM, REJECT):
            agents.append({"type": {"coding": [{"system": PARTICIPANT_TYPE, "code": "verifier", "display": "Verifier"}]},
                           "who": _logical(SID_REVIEWER, "reviewer-demo", display="Data reviewer (%s)" % decision)})
        prov_id = fhir_id("streamfhir-prov-" + a.record_id)
        entries.append({"fullUrl": self._urn(), "resource": {
            "resourceType": "Provenance",
            "id": prov_id,
            "meta": self._meta(pseudonymised=True),
            "text": narrative("Record %s was reported by a pseudonymous citizen scientist and assembled into FHIR by "
                              "StreamFHIR %s%s" % (a.record_id, VERSION,
                                                   "; reviewer decision: " + decision if decision else "")),
            "target": targets,
            "occurredDateTime": _iso(a.observed_at),
            "recorded": _iso(self._now()),
            "activity": {"coding": [{"system": DATA_OPERATION, "code": "CREATE", "display": "create"}]},
            "agent": agents,
            "entity": [{"role": "source", "what": _logical(SID_RECORD, a.record_id, display="Original citizen record")}],
        }, "request": {"method": "PUT", "url": "Provenance/" + prov_id}})
        return {"resourceType": "Bundle", "type": "transaction", "entry": entries}

    # ---------- UC-2 ----------
    def _flag_bundle(self, risk: SiteRisk, site: Site, active: bool) -> Dict[str, Any]:
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
            text = "%s at %s (%d corroborated of %d hazard points: %s). Rules: %s. Expires %s unless re-confirmed." % (
                LEVEL_DISPLAY[risk.level], site.name, risk.confirmed_hazard_points, risk.hazard_points, lanes, rules,
                period["end"][:10])
        else:
            period = {"end": _iso(risk.window_end)}
            text = "No current corroborated health hazard at %s (%s)." % (site.name, LEVEL_DISPLAY[risk.level])
        flag = {
            "resourceType": "Flag",
            "meta": self._meta(),
            "text": narrative(text),
            "extension": [{"url": FLAG_DETAIL, "valueReference": _logical(SID_RECORD, rid, "Observation",
                                                                           "Citizen assessment " + rid)}
                          for rid in evidence],
            "identifier": [{"system": SID_FLAG, "value": site.site_id}],
            "status": "active" if active else "inactive",
            "category": [{"coding": [{"system": FLAG_CATEGORY, "code": "safety", "display": "Safety"}]}],
            "code": {"coding": [{"system": CS_RISK, "code": risk.level, "display": LEVEL_DISPLAY[risk.level]}],
                     "text": text},
            "subject": {"reference": loc["fullUrl"], "display": site.name},
            "period": period,
            "author": {"reference": dev["fullUrl"], "display": "StreamFHIR %s" % VERSION},
        }
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
             "request": {"method": "PUT", "url": "Flag?identifier=%s|%s" % (SID_FLAG, site.site_id)}},
            {"fullUrl": self._urn(), "resource": prov, "request": {"method": "PUT", "url": "Provenance/" + prov_id}},
        ]}

    def risk_bundle(self, risk: SiteRisk, site: Site) -> Optional[Dict[str, Any]]:
        """Active Flag for a site whose corroborated hazard reaches the threshold (else None)."""
        return self._flag_bundle(risk, site, active=True) if risk.needs_flag else None

    def stand_down_bundle(self, risk: SiteRisk, site: Site) -> Dict[str, Any]:
        """Set the site's Flag (same identifier) to inactive when the hazard is gone."""
        return self._flag_bundle(risk, site, active=False)


# ---------- conformance resources (published in fhir/) ----------
def _cs(cs_id: str, url: str, name: str, title: str, description: str, concepts: List[Dict[str, str]],
        value_set: Optional[str] = None) -> Dict[str, Any]:
    cs = {"resourceType": "CodeSystem", "id": cs_id, "url": url, "version": VERSION, "name": name, "title": title,
          "status": "draft", "experimental": True, "date": DATE, "publisher": "StreamFHIR hackathon prototype",
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


def valueset_resource() -> Dict[str, Any]:
    return {"resourceType": "ValueSet", "id": "stream-indicator", "url": VS_INDICATOR, "version": VERSION,
            "name": "StreamIndicator", "title": "Citizen stream assessment indicators", "status": "draft",
            "experimental": True, "date": DATE, "jurisdiction": [JURISDICTION_WORLD],
            "description": "All codes from the StreamFHIR stream-indicator CodeSystem (example canonical).",
            "compose": {"include": [{"system": CS_INDICATOR}]}}


def _common_elements() -> List[Dict[str, Any]]:
    return [
        {"id": "Observation.category", "path": "Observation.category", "min": 1, "max": "1",
         "patternCodeableConcept": {"coding": [{"system": OBS_CATEGORY, "code": "survey"}]}},
        {"id": "Observation.code", "path": "Observation.code",
         "binding": {"strength": "required", "valueSet": VS_INDICATOR}},
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
        "differential": {"element": [{"id": "Observation", "path": "Observation", "short": title}]
                         + _common_elements() + extra}}


def structuredefinition_resources() -> List[Dict[str, Any]]:
    return [
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
    def res(rtype, interactions, params=()):
        r = {"type": rtype, "interaction": [{"code": i} for i in interactions],
             "conditionalCreate": True}
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
                       "(ifNoneExist on identifier) and conditional update of Flag by identifier. Consumers find "
                       "warnings with Flag?category=safety&status=active or subscribe to them (see Subscription example).",
        "rest": [{"mode": "server", "interaction": [{"code": "transaction"}], "resource": [
            res("Location", ["create", "read", "search-type"], (("identifier", "token"),)),
            res("Device", ["create", "read"], (("identifier", "token"),)),
            res("Media", ["create", "read"], (("identifier", "token"),)),
            res("Observation", ["create", "read", "search-type"],
                (("identifier", "token"), ("subject", "reference"), ("code", "token"))),
            res("Provenance", ["update", "read"]),
            dict(res("Flag", ["update", "read", "search-type"],
                     (("identifier", "token"), ("category", "token"), ("status", "token"), ("subject", "reference"))),
                 conditionalUpdate=True),
        ]}]}


def subscription_example() -> Dict[str, Any]:
    return {
        "resourceType": "Subscription", "id": "streamfhir-safety-flags", "status": "requested",
        "reason": "Notify a public-health or environment system when StreamFHIR raises or changes a site warning.",
        "criteria": "Flag?category=http://terminology.hl7.org/CodeSystem/flag-category|safety",
        "channel": {"type": "rest-hook", "endpoint": "https://health-system.example.org/fhir-notify",
                    "payload": "application/fhir+json"}}


def conformance_resources() -> List[Dict[str, Any]]:
    return codesystem_resources() + [valueset_resource()] + structuredefinition_resources()


def conformance_bundle() -> Dict[str, Any]:
    """Transaction that PUTs the prototype CodeSystems, ValueSet and profiles under fixed server ids."""
    entries = []
    for r in conformance_resources():
        rid = "streamfhir-" + r["id"]
        entries.append({"fullUrl": "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, r["url"])),
                        "resource": dict(r, id=rid),
                        "request": {"method": "PUT", "url": "%s/%s" % (r["resourceType"], rid)}})
    return {"resourceType": "Bundle", "type": "transaction", "entry": entries}
