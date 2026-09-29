# StreamFHIR — SPEC (v0.7, 2026-09-29)

## 0. One line
StreamFHIR turns a **citizen-science stream check** into **HL7 FHIR R4 data plus an explainable One Health risk flag** that an environmental or public-health system can consume without re-keying.
Essence: *not* "another water-quality app", but "a citizen observation that a health information system can already read, trust-check and act on".

## 1. Success criteria (numbers) · deadline (constant) · non-goals
- Reference: the HL7 FHIR R4 base spec (Observation / Location / Provenance / Flag) and the public HAPI FHIR R4 test server (`https://hapi.fhir.org/baseR4`) — the integration target every health-IT judge recognises.
- Success (all measurable in this repo):
  - S1: 100 % of synthetic sample records (≥10 records, ≥3 sites) receive a validation status (`ok` / `review` / `blocked`) with ≥1 plain-language message for every non-`ok` record.
  - S2: 100 % of non-`blocked` records map to a FHIR R4 `transaction` Bundle containing 1 `Location`, 1 panel `Observation`, ≥1 indicator `Observation`, 1 `Provenance`; 0 `blocked` records are exported.
  - S3: every indicator code emitted exists in `fhir/CodeSystem-stream-indicator.json` (test-enforced); no LOINC/SNOMED code is used.
  - S4: every site gets a hazard level (`low`/`moderate`/`verify`/`high`) and a separate ecological condition with the fired rule IDs listed; every `high` site (>= 5 *corroborated* hazard points) yields exactly 1 FHIR `Flag` with `subject` = that `Location`; 0 Flags are raised from uncorroborated reports.
  - S8: re-sending the same record to a FHIR server creates 0 duplicate resources (conditional create/update), measured live.
  - S5: ≥25 automated tests pass (`python3 -m pytest -q`), 0 network calls in tests.
  - S6: dry-run is the default for sending; live POSTs to HAPI carry synthetic data, or public open data under its licence (Hub'Eau, Licence Ouverte) with its source in Provenance; response IDs are recorded in docs/evidence-hapi.md.
  - S7: UI renders at 390 px and 1280 px with no horizontal scroll; one primary action per screen.
- Deadline: Devpost submission 2026-10-04 21:00 PDT (extended). Internal code freeze: 2026-09-30.
- Non-goals: no ML model, no accuracy claims; real data only as import tests (agency data; FreshWater Watch volunteer records); no user accounts; no claim that the record schema is the official OneAquaHealth app schema; no production hosting; not a regulatory water-quality compliance tool.

## 2. Constraints
- Hackathon rules (quoted): "All projects must include a public code repository (e.g., GitHub) with source code and documentation"; "Projects must be original and developed during the hackathon period".
- Cost: 0. Python 3.9+ standard library only at runtime; `pytest` for tests.
- Privacy: observers are pseudonymous IDs only; no names, emails or device IDs are stored or sent.

## 3. Ubiquitous language (code names 1:1)
| Term | Meaning | Code name |
|---|---|---|
| Site | A fixed stream reach that citizens revisit | `Site` |
| Stream assessment record | One citizen visit: time, place, observer pseudonym, photos, indicator values | `StreamAssessment` (raw dict = "record") |
| Indicator | One thing a citizen scores, measures or notices | `Indicator`, `INDICATORS` |
| Issue | A validation finding with severity `error` or `warning` | `Issue` |
| Validation status | `ok` / `review` (human must look) / `blocked` (cannot be shared) | `ValidationReport.status` |
| Rule | An explainable One Health risk rule with lane and points | `Rule`, `RULES` |
| Lane | One Health dimension: `environment`, `animal`, `human` | `LANES` |
| Corroborated | A fired rule supported by ≥2 distinct observers or ≥1 photo | `FiredRule.corroborated` |
| Site risk | Aggregated risk for a site over the recent window | `SiteRisk` |
| Bundle | FHIR transaction Bundle | `assessment_bundle`, `risk_bundle` |

## 4. Domain model
- Entities: `Site`, `StreamAssessment`. Value objects: `Issue`, `ValidationReport`, `FiredRule`, `SiteRisk`, `Indicator`.
- Domain services: `validate_record(raw, sites, now)`, `evaluate_site(site, reports)`.
- Ports: `SiteRepository`, `RecordRepository`, `FhirTranslator`, `FhirServer`, `Clock`.

## 5. Use cases (application)
| UC | Input | Output | Rules |
|---|---|---|---|
| UC-1 CheckRecord | raw record dict | report + Bundle (or none if blocked) | never alter values; `review` → Observation.status `preliminary` + note |
| UC-2 SiteRiskOverview | all stored records | per-site `SiteRisk` + Flag bundle for `high` | 14-day window ending at site's latest record; `blocked` records excluded |
| UC-3 ShareBundle | Bundle, live flag | dry-run summary or server response | default dry-run; live only via explicit flag |
| UC-4 ReviewRecord | record id, confirm/reject/undo | decision | only `review` records; decisions change corroboration and FHIR status |

## 6. Acceptance criteria (each → ≥1 test)
- AC-1 Given a complete, plausible record, When validated, Then status is `ok` and there are 0 issues.
- AC-2 Given a record without observer or without coordinates or with a future timestamp, When validated, Then status is `blocked`.
- AC-3 Given pH 15 (outside 0–14), Then `blocked`; Given pH 4.2 (possible but implausible), Then `review` with a plausibility warning, and the value is kept unchanged.
- AC-4 Given a dead-fish or algal-scum report with no photo, Then `review` with "photo needed".
- AC-5 Given coordinates > 250 m from the registered site, Then `review` with a location-mismatch warning.
- AC-6 Given an unknown indicator code or a score outside 1–10, Then `blocked`.
- AC-7 Given a `review` record, When mapped, Then every indicator Observation has status `preliminary` and a note quoting the warning.
- AC-8 Given an `ok` record, When mapped, Then the Bundle is `type: transaction`, the Location uses conditional create on the site identifier, Observations reference the Location via `urn:uuid`, and Provenance targets every Observation.
- AC-9 Given any mapped Bundle, Then every coding in the StreamFHIR system exists in the published CodeSystem file.
- AC-10 Given a site with sewage odour and people in the water reported by 2 observers, When evaluated, Then level is `high`, rules R4 and R6 fire, and the rule is corroborated.
- AC-11 Given a site with only good scores, Then level is `low` and no Flag is emitted.
- AC-12 Given a `high` site, When the risk bundle is built, Then it contains exactly 1 Flag with `subject` referencing the site Location and text listing fired rule IDs.
- AC-13 Given dry-run (default), When sharing, Then no HTTP request is made and a summary with resource counts is returned.
- AC-14 Given a live share with a fake transport returning a transaction-response, Then the created resource locations are returned.
- AC-15 Given the domain and application packages, Then they import nothing from adapters/infrastructure (architecture test).
- AC-16 Given the web server, When `POST /api/check` with a sample record, Then the JSON response has `report.status` and `bundle`.

### v0.2 additions (after mock judging, see win-gate notes)
- AC-17 Given hazard signals reported only by untrusted (review) or single photo-less reports, When evaluated, Then level is `verify` and no Flag is raised.
- AC-18 Given a `review` record, When a reviewer confirms it, Then it counts as corroborated (site may become `high`); When rejected, Then it never counts and maps to `entered-in-error`; only `review` records can be decided.
- AC-19 (v0.3) Given a hazard, Then it clears only after 2 trusted visits >= 7 days apart that observed ALL the rule's clear keys without the signal; a condition rule clears after 1.
- AC-20 Given an evaluation date (`as_of`), Then only records in the 14 days up to it count; the demo uses a fixed `demo_as_of`.
- AC-21 Given nitrate without a basis, Then `review`; Given nitrate as N, Then thresholds use NO3 = N x 4.43; LOINC 9480-5 is only added for readings as NO3.
- AC-22 Given a jar/stick test pointing to green or filamentous algae, Then the bloom rule (R3) does not fire.
- AC-23 Given any mapped Bundle, Then every Observation and Media is a conditional update (PUT by record identifier, no '#' in URLs) and Provenance uses PUT with a deterministic id. (v0.3: was conditional create)
- AC-24 Given a `high` site, Then the Flag links its trusted evidence via `flag-detail`, has `period.start`/`period.end`, its own Provenance, and is written by conditional update on the site identifier; a stand-down Bundle sets the same Flag `inactive`.

### v0.3 additions (after round-2 judging)
- AC-25 Given a reviewer confirmation without a basis (site-visit / photo-checked / lab-result), Then it is refused; the basis is recorded in Provenance.
- AC-26 Given a still-water site, Then stream methods R1 and R10 do not fire.
- AC-27 Given a record entered now from the form, Then it is stamped with the service clock (`stamp_now`), not the device clock.
- AC-28 Given stand-down, Then an inactive Flag is only produced for sites whose Flag is active on the server, keeping its `period.start`.
- AC-29 Given a partial later visit (not all clear keys observed), Then it does not clear a signal.
- AC-31 Given a US Water Quality Portal Result+Station CSV, When imported, Then there is one record per sampling activity with real coordinates, source oddities (duplicates, odd units, missing time) become `review` notes, and the resources are not tagged HTEST/PSEUDED.
### v0.5 additions (real European data, EU water vocabularies)
- AC-32 Given a Hub'Eau (France) station + analysis snapshot, When imported, Then there is one record per station and sampling time (Europe/Paris offset), nitrate (Sandre 1340, the NO3- ion) gets basis as-NO3, results below the quantification limit are counted and listed but not turned into numbers, and a result the producer did not qualify as 'Correcte' goes to a person.
- AC-33 Given a French station that France also reports to the EEA, Then its Location keeps both the Sandre station code and the EEA euMonitoringSiteCode.
- AC-34 Given pH, water temperature, nitrate as NO3 or phosphate, Then the Observation carries the EEA WISE ObservedProperty code; French records also carry the Sandre parameter code; nitrate as N gets neither the EEA nor the LOINC nitrate code.
- AC-35 Given the ConceptMap, Then it maps exactly what the mapper emits (nitrate only as NO3, via dependsOn).
- AC-36 Given EEA Waterbase aggregated rows, Then the coverage check counts sites and the latest year per quantity.
- AC-37 Given a monitoring dataset flagged `evaluate_each_site_at_its_latest_visit`, Then each site is evaluated at its own latest sampling; a condition rule alone never makes the health hazard "assessed".
- AC-38 Given a real dataset, Then the UI leads with ecological condition, colours the map by condition, and labels condition rules as agency lab values (no corroboration wording).
- AC-39 Given any Flag, Then it claims the stream-site-flag profile (Location subject, safety category, expiry, level from a required ValueSet, invariant ssf-1: an active Flag cites a hazard rule).
- AC-40 Given an agency record, Then its Observations are `laboratory`, the performer is the organisation (Sandre code for French data), and no narrative calls it a citizen record.
- AC-41 Given a result below the laboratory's quantification limit, Then it becomes an Observation with `comparator "<"` and the limit, never a number.
- AC-42 Given a trusted agency record, Then its condition rules count as agency results, not as unverified reports.
- AC-43 Given a site where no hazard input was observed, Then the API level is `not-assessed`, never `low`.
- AC-44 Given `interop-demo`, Then a real agency reading and a synthetic (HTEST) citizen reading at the same station carry the same EEA nitrate code.
- AC-45 Given FreshWater Watch (Earthwatch) volunteer records, Then only fields with a matching meaning are scored (blue-green scum, oily sheen, foam, colour, litter, animal access, swimming, photo), other colours become "other", kit bands stay notes, implausible scum ticks, slurry and discharging outfalls go to a person, animal access stays a note, and for real citizen data a photo counts only after a reviewer's check.
- AC-46 Given the Ghent (VMM) and Benevento (ARPAC) open exports, Then the same record shape results: nitrate keeps its N basis, phosphorus as P stays a note, values below a limit or `n.d.` are never turned into numbers, and records are laboratory data.
- AC-47 Given a Flag whose period.end is before build time, Then it is `inactive` (expired); nitrate values state their basis in the UCUM unit (`mg{NO3}/L`, `mg{N}/L`).
- AC-48 Given orthophosphate reported as P, Then the value and unit (mg{P}/L) are kept and R2 compares its PO4 equivalent (x 3.066).
- AC-49 Given photo_needs_review, Then an ok report with a photo can be confirmed (photo checked) or rejected by a reviewer.
- AC-50 Given R4 Flag has no category/status search parameter, Then StreamFHIR ships SearchParameters flag-category and flag-status and the CapabilityStatement references them.
- AC-30 Given the UI file, Then it has a viewport meta, no external resources, one fixed CTA >= 52 px, folded evidence and live-region status.

## 7. Architecture (Clean)
```
streamfhir/domain/ ← application/ ← adapters/ (fhir_mapper, hapi_client, json_repository, presenter) ← infrastructure/ (container, cli, web, static UI)
```
FHIR is treated as an external format, so the mapper lives in `adapters/`; the domain knows nothing about FHIR.

## 8. UI acceptance criteria (Toss-style checklist, concretised)
1. Mobile first: 390 px no horizontal scroll; 1280 px centred column.
2. One question per screen: three screens (Sites, Site detail, Check a record); each has one primary button.
3. Type scale: headings ≥22 px bold, body 16 px, helper 13 px.
4. Spacing ≥24 px between sections, card radius 16 px, ≤1 shadow level.
5. Primary CTA fixed at bottom, ≥52 px tall, full width.
6. Number first: site card leads with the risk score (points) ≥28 px, verdict line below.
7. Evidence folded: rules, FHIR JSON in `<details>` closed by default.
8. Plain words: "Needs a quick look", "Can't share yet", "Ready to share"; FHIR terms get a one-line gloss.
9. White background, one blue (#3182F6), status colours green/amber/red always paired with text.
10. No external fonts/CDNs; only own API requests.

## 9. Physical verification
- Run the server, capture PNG screenshots at 390 px and 1280 px (headless Chromium if available).
- POST one synthetic Bundle to HAPI R4 once (live), record returned IDs; run `$validate` on one generated Observation and record the OperationOutcome summary.

## 10. Change log
- v0.7 2026-09-29 (night): FreshWater Watch citizen records (Toulouse, Coimbra region); Ghent (VMM) and Benevento (ARPAC) agency feeds.
- v0.5 2026-09-29 (night): real EU data (Hub'Eau, Toulouse area, 52 stations), EEA WISE + Sandre codings, ConceptMap + fragment CodeSystems, profile slices code.coding (one StreamFHIR coding required, translations allowed), per-site evaluation for monitoring data, EEA coverage check for the 5 OAH cities.
- v0.1 2026-09-29 first version.
- v0.4 2026-09-29 after rounds 3-4: real public data import (WQP), 'not assessed' instead of good-by-default, Flag ifMatch, static preview notice, Organization performer for real data, nitrate unit check.
- v0.3 2026-09-29 after round-2 judging: conditional update for Observation/Media (review propagates), confirm basis, 2-visit clearing, still-water sites, safe stand-down, category slicing, stamp_now, static review replay.
- v0.2 2026-09-29 after 3 mock judges: hazard vs ecological condition split, corroboration from trusted reports only, reviewer confirm/reject, as-of window + clearing, nitrate basis, bloom jar/stick test, invertebrate sampling effort, idempotent FHIR writes, traceable/expiring Flag, 2 profiles, CapabilityStatement, Subscription example, citizen step form, map, static demo.
