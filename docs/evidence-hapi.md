# Live evidence — public HAPI FHIR R4 test server (2026-09-29, ~14:50–14:55 KST)

Server: `https://hapi.fhir.org/baseR4` (public test server; content may be purged at any time).
Only synthetic demo data was sent. All resources carry `meta.security = v3-ActReason#HTEST` (test health data).

| Step | Command | Result |
|---|---|---|
| 1. Base R4 validation of a generated Observation (no profile) | `python3 -m streamfhir validate-remote SYN-001` | 0 errors; 1 warning: example CodeSystem unknown to server (expected before step 2) |
| 2. Publish prototype CodeSystems, ValueSet, profile | `python3 -m streamfhir publish-conformance --live` | `CodeSystem/streamfhir-stream-indicator`, `CodeSystem/streamfhir-stream-answer`, `CodeSystem/streamfhir-onehealth-risk`, `ValueSet/streamfhir-stream-indicator`, `StructureDefinition/streamfhir-stream-assessment-observation` (all `_history/1`) |
| 3. Validate against the StreamFHIR profile | `python3 -m streamfhir validate-remote SYN-001 --with-profile` (also SYN-010) | `information: No issues detected during validation` |
| 3b. Negative control: unknown code + missing performer | ad-hoc script (see SPEC §9) | 3 errors: `Observation.performer: minimum required = 1`, `Code is not found in CodeSystem`, `None of the codings provided are in the value set ... required` — the profile and binding are really enforced |
| 4. POST transaction Bundle for SYN-001 | `python3 -m streamfhir send SYN-001 --live --flags` | HTTP 200; `Location/47839`, `Device/47840`, `Media/47820`, `Observation/47821`–`47837` (17), `Provenance/47838` |
| 4b. Flags for the two high-risk sites | (same command, `--flags`) | `Flag/47842` → `Location/47843` (Alder Brook below outfall); `Flag/47844` → `Location/47845` (Mill Pond inlet) |
| 5. Idempotency: second record at same site | `python3 -m streamfhir send SYN-002 --live` | HTTP 200; Location resolved to existing `Location/47839` (conditional create, no duplicate site) |
| 6. Consumer-side query | `GET Observation?subject=Location/47839&_summary=count` | `33` (17 + 16 Observations from SYN-001 and SYN-002) |
| 6b. Consumer-side query | `GET Flag?identifier=https://example.org/fhir/streamfhir/sid/flag\|` | 2 Flags, both `high`, each pointing at its Location |

## v0.2 re-run (2026-09-29, ~15:10–15:20 KST)

| Step | Command | Result |
|---|---|---|
| 7. Publish v0.2 conformance (4 CodeSystems, ValueSet, 2 profiles) | `python3 -m streamfhir publish-conformance --live` | `CodeSystem/streamfhir-stream-indicator/_history/2`, `…-stream-answer/_history/2`, `…-onehealth-risk-level/_history/1`, `…-onehealth-risk-rule/_history/1`, `ValueSet/streamfhir-stream-indicator/_history/2`, `StructureDefinition/streamfhir-stream-assessment-panel/_history/1`, `StructureDefinition/streamfhir-stream-indicator-observation/_history/1` |
| 8. Indicator Observation vs indicator profile | `validate-remote SYN-001 --with-profile` | `No issues detected during validation` |
| 8b. Panel Observation vs panel profile | ad-hoc (panel with `hasMember`) | `No issues detected during validation` |
| 8c. Negative control: panel with a value | ad-hoc | error `Observation.value[x]: max allowed = 0, but found 1 (from …stream-assessment-panel\|0.2.0)` |
| 8d. Flag (with `flag-detail` extensions) vs base R4 | ad-hoc | `No issues detected during validation` |
| 9. Idempotency: send SYN-001 twice | `send SYN-001 --live` ×2 | both HTTP 200, 24 entries; same `Location/47839`, `Media/47820`, `Observation/47821…`; only `Provenance/streamfhir-prov-SYN-001` got a new version (`_history/2`). `GET Observation?identifier=…/sid/record\|SYN-001#ph` → **1** copy. Note: conditional create keeps the first stored copy, so resources first sent by v0.1 keep their v0.1 content. |
| 10. Flags with stand-down | `send SYN-003 --live --flags --stand-down` | active: `Flag/49203` (S-ALDER-DN), `Flag/49205` (S-MILL); inactive: `Flag/49201` (S-ALDER-UP), `Flag/49209` (S-WILLOW), `Flag/49212` (S-OAK); one Provenance per Flag (`Provenance/streamfhir-prov-flag-<site>`) |
| 11. Re-send Flags | `send SYN-006 --live --flags` | `Flag?identifier=…/sid/flag\|S-MILL` → **1** match, `Flag/49205` updated in place to version 2 (period 2026-09-18 → 2026-10-02, `flag-detail` → SYN-006) |

## v0.3 re-run (2026-09-29, ~15:20–15:25 KST)
Identifiers changed from `record#code` to `record.code` (no `#` in request URLs), and Observation/Media are now conditional updates.

| Step | Command | Result |
|---|---|---|
| 12. Publish v0.3 conformance (category slicing, contact) | `publish-conformance --live` | HTTP 200, 7 resources updated (e.g. `StructureDefinition/streamfhir-stream-indicator-observation/_history/2`) |
| 13. Profile checks | `validate-remote SYN-001 --with-profile` + ad-hoc | indicator: `No issues detected`; with an extra partner category: 0 errors (open slice); without the survey category: error `Slice 'Observation.category:survey': a matching slice is required, but not found`; Location with `physicalType=area`: `No issues detected` |
| 14. Review decisions reach the server | `send SYN-010 --live`, then `send SYN-010 --live --review reject`, then `--review confirm --basis lab-result` | `Observation?identifier=…\|SYN-010.ph` → `Observation/49361` `preliminary` (v1) → `entered-in-error` (v2) → `final` (v3) |
| 15. Idempotency with conditional update | `send SYN-001 --live` ×2 | both HTTP 200, 24 entries, same ids; `SYN-001.ph` → **1** copy (`Observation/49380`, still version 1: an unchanged re-send creates no new version) |
| 16. Flag lifecycle | `send SYN-015 --live --review confirm --basis site-visit --flags`, then (new process, no decision) `send SYN-001 --live --flags --stand-down` | Willow `Flag/49209`: `active` v2 (period 2026-09-27 → 2026-10-11) → `inactive` v3, original start kept; S-OAK `Flag/49212` (inactive since the v0.2 run) untouched — stand-down no longer creates Flags for calm sites |
| 17. Stand-down with optimistic locking | confirm SYN-015 + `--flags`, then `send SYN-001 --live --flags --stand-down` | Willow `Flag/49209` active v4 → inactive v5; the PUT carried `ifMatch: W/"4"` (the version read) and was accepted; text now says "Stood down … unconfirmed signals remain: R3, R7" |
| 18. Coded rule citation on the Flag (2026-09-29) | `publish-conformance --live`, then ad-hoc `Flag/$validate` (subject `Location/47845`) | `StructureDefinition/streamfhir-flag-rule/_history/1` created; Flag with `flag-rule` = R4, R6: `No issues detected during validation`; negative control with a wrong CodeSystem in the extension: error "Value is 'https://example.org/wrong' but is fixed to '…/CodeSystem/onehealth-risk-rule' in the profile" |
| 19. Required binding to the hazard rules (2026-09-29) | `publish-conformance --live`, then ad-hoc `Flag/$validate` ×3 | `ValueSet/streamfhir-onehealth-hazard-rule/_history/1`, `StructureDefinition/streamfhir-flag-rule/_history/2`; valid Flag: `No issues detected during validation`; code `R99`: error "Code is not found in CodeSystem" + error "None of the codings provided are in the value set 'StreamFHIR health-hazard rules'"; ecological-condition rule `R1`: error "None of the codings provided are in the value set" (condition rules can never be cited as a warning reason) |

Note on leftovers: the v0.1/v0.2 runs stored Observations with `record#code` identifiers; v0.3 uses `record.code`, so those older copies remain on the test server as separate resources (query the new ones with `identifier=…|SYN-001.ph`). Flags created as `inactive` by the v0.2 stand-down (S-ALDER-UP, S-OAK) also remain; v0.3 never creates Flags for calm sites.

## v0.4 — real European data and EU water vocabularies (2026-09-29, ~21:45–22:10 KST)
Real public data from France's Hub'Eau was **validated only** (`$validate`, nothing stored). Only synthetic data was written.

| Step | Command | Result |
|---|---|---|
| 20. Publish v0.4 conformance (+ ConceptMap, + 2 fragment CodeSystems, code.coding slicing) | `publish-conformance --live` | HTTP 200, 12 resources, e.g. `ConceptMap/streamfhir-stream-indicator-to-eu-water/_history/2`, `CodeSystem/streamfhir-eea-wise-observedproperty-fragment/_history/1`, `CodeSystem/streamfhir-sandre-parametre-fragment/_history/1`, `StructureDefinition/streamfhir-stream-indicator-observation/_history/5` |
| 21. `$translate` on the server | `GET ConceptMap/$translate?url=…/ConceptMap/stream-indicator-to-eu-water&system=…/stream-indicator&code=ph` (and nitrate, phosphate; `targetsystem` EEA or Sandre) | `result = true`: ph → `EEA_3152-01-0` pH and Sandre `1302`; nitrate → `CAS_14797-55-8` and Sandre `1340`; phosphate → `CAS_14265-44-2`; all `equivalent` (**v0.5.1: phosphate → EEA changed to `inexact`**, because the EEA reports it as P and StreamFHIR as PO4; confirmed live by `$translate`) |
| 22. Real Toulouse pH Observation (StreamFHIR + EEA + Sandre codings) vs indicator profile | `python3 -m streamfhir --data data/real-eu-toulouse validate-remote FR-05157100-2024-12-18T1343 --with-profile --code ph` | **0 errors, 0 warnings**, 2 information ("does not match any known slice" = the two translations, allowed by the open slice) |
| 22b. Same record, nitrate (adds LOINC 9480-5) | `… --code nitrate` | 0 errors; 1 warning: the public HAPI server has no LOINC loaded ("CodeSystem is unknown: http://loinc.org"); 9480-5 was confirmed on tx.fhir.org |
| 22c. Negative controls (ad-hoc) | unknown code `zzz`; only EEA/Sandre codings; two StreamFHIR codings | 2 errors "Code is not found in CodeSystem"; 1 error "Slice 'Observation.code.coding:streamfhir': a matching slice is required"; 1 error "Observation.code.coding:streamfhir: max allowed = 1, but found 2" |
| 22d. Real Location with its Sandre station identifier | ad-hoc `Location/$validate` | 0 errors, 0 warnings |
| 23. A health system finds a citizen reading by the EU code | `send SYN-001 --live`, then `GET Observation?code=http://dd.eionet.europa.eu/vocabulary/wise/ObservedProperty\|EEA_3152-01-0&identifier=…/sid/record\|SYN-001.ph` | HTTP 200 (24 entries); search returns **1** match: `Observation/49380` version 2, codings `ph` + `EEA_3152-01-0` |

Why the slicing (v0.4): with the old required binding on the whole `Observation.code`, the HAPI validator reported "None of the codings provided are in the value set" as soon as a second coding (LOINC for nitrate, or an EU code) was present. The profile now requires exactly one coding from the StreamFHIR ValueSet (slice `streamfhir`, required binding) and leaves other codings open, which is how translations are normally allowed.
| 24. Flag profile `stream-site-flag` + risk-level ValueSet (v0.4) | `publish-conformance --live`, then ad-hoc `Flag/$validate` ×6 (S-MILL Flag, subject `Location/47839`) | `StructureDefinition/streamfhir-stream-site-flag/_history/1`, `ValueSet/streamfhir-onehealth-risk-level/_history/1`; valid active Flag: **0 errors, 0 warnings**; negative controls, each rejected: active Flag without a rule citation → "Constraint failed: ssf-1"; subject a Patient → "Invalid Resource target type. Found Patient, but expected one of ([Location])"; no safety category → "Slice 'Flag.category:safety': a matching slice is required"; no expiry → "Flag.period.end: minimum required = 1"; level `extreme` → "Code is not found in CodeSystem" + "None of the codings provided are in the value set" |
| 25. Agency data as laboratory data (v0.4, after mock judging) | `publish-conformance --live`; ad-hoc `Observation/$validate` | indicator profile `_history/6` (category: survey **or** laboratory, invariant `sio-1`); real Toulouse pH with `category = laboratory`, performer = Sandre organisation code: **0 errors, 0 warnings**; a result below the lab's quantification limit as `valueQuantity.comparator = "<"`, value 0.02 (FR-05158800-2024-12-17T1255, orthophosphate): **0 errors, 0 warnings**; negative control with category `exam` only: error "Constraint failed: sio-1" |
| 26. **One query returns agency and citizen nitrate** | `python3 -m streamfhir --data data/real-eu-toulouse interop-demo --live` (sends the latest real sampling at La Rivel, Baziège, and one **synthetic** citizen check at the same station) | both transactions HTTP 200, one shared `Location/54630`; `GET Observation?code=http://dd.eionet.europa.eu/vocabulary/wise/ObservedProperty\|CAS_14797-55-8&subject=Location/54630` → **2** results: `Observation/54635` (laboratory, 62.0 mg/L, real Hub'Eau data, Licence Ouverte) and `Observation/54640` (survey, 50 mg/L, `HTEST` synthetic citizen check). The public server may purge data; the command re-creates it. |
| 27. Synthetic citizen checks raise a Flag on a real station (v0.5.1; v0.7: the Flag is now `inactive` because its period ended on 1 Jan 2025 — status follows expiry, AC-47) | `python3 -m streamfhir --data data/real-eu-toulouse interop-demo --live` | agency sampling + 2 **synthetic** citizen checks (two observers, scum + dogs in the water, photos kept for review, each Observation noted "SYNTHETIC citizen check created for an interoperability demo…", `HTEST`) at La Rivel, Baziège (`Location/54630`); `Observation?code=<EEA nitrate>&subject=Location/54630` → `Observation/54635` (laboratory, 62.0), `Observation/54687` and `/54697` (survey, 50, synthetic); `Flag/54705` active, level `high`, rules R3 + R7, code text "SYNTHETIC DEMO – …". An earlier synthetic record with the agency's exact timestamp was deleted from the test server. **Captured response:** [`data/real-eu-toulouse/interop-query.json`](../data/real-eu-toulouse/interop-query.json) (for when the server has purged it). |

**Negative controls, all rejected as expected (15):** steps 3b, 8c, 13, 18, 19 (×2), 22c (×3), 24 (×5), 25.

## v0.6 — real citizen-science records (2026-09-29, ~22:45 KST)
| Step | Command | Result |
|---|---|---|
| 28. Real FreshWater Watch volunteer record FWW-304472 (Rio Vouga, Aveiro) | `python3 -m streamfhir import-fww`, then ad-hoc `$validate` (validation only, nothing stored; the subject was pointed at an existing Location, `Location/54630` in Toulouse, only so the reference resolves — the site is in Portugal) | panel and `surface` Observations vs profiles: **0 errors, 0 warnings**. Superseded in v0.7: this record now goes to a reviewer (blue-green scum ticked on flowing water; outfall discharging) and raises no Flag; its photo shows no scum. |

## v0.7 — two more OneAquaHealth cities' open agency feeds (2026-09-29, ~23:00 KST)
| Step | Command | Result |
|---|---|---|
| 29. Ghent (VMM) and Benevento (ARPA Campania) samplings through the same pipeline | `python3 -m streamfhir import-ghent`, `import-benevento`, then ad-hoc `$validate` (validation only) | Ghent `BE-VMM-OW168900-2026-09-09T1209` nitrate (as N) and pH Observations: **0 errors, 0 warnings**; Benevento `IT-ARPAC-Ta3-2025-05-27T1130` nitrate, nitrate basis, pH and temperature Observations: **0 errors, 0 warnings** |

## v0.7.2 — Flag search parameters (2026-09-29, ~23:50 KST, found by a mock judge)
| Step | Command | Result |
|---|---|---|
| 30. `Flag?category=…|safety&status=active` | GET on HAPI | error **HAPI-0524 "Unknown search parameter category for resource type Flag"**: R4 Flag defines neither `category` nor `status` as search parameters, so the query documented until v0.7.1 did not work on a standard R4 server. Fix: `SearchParameter/streamfhir-flag-category` and `…-flag-status` published (`_history/1`) and referenced from the CapabilityStatement; the public test server still answers HAPI-0524 after 3 minutes (it does not activate custom SearchParameters), so on it consumers use `Flag?identifier=https://example.org/fhir/streamfhir/sid/flag|S-ALDER-DN` (1 active safety Flag) or `Flag?subject=Location/…` |
