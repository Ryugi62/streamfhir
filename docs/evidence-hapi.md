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
