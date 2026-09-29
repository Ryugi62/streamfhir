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
