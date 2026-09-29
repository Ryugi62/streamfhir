# StreamFHIR — demo video script (target 3:50–4:00, English)

Setup: `python3 -m streamfhir serve`, open `http://127.0.0.1:8000` in a 390 px-wide window (phone emulation), with a terminal beside it. **Restart the server just before recording** so no reviewer decisions are left over. All data is synthetic, and the demo date is fixed at 29 Sep 2026.

| Time | Screen | Narration |
|---|---|---|
| 0:00–0:20 | Sites (`#/`): "5 stream sites, 3 need action", map | "A volunteer sees green scum at a pond and dogs swimming. Today that report stays inside one app. StreamFHIR turns it into standard health data, HL7 FHIR, and raises a warning only when the evidence holds up." |
| 0:20–0:40 | Scroll: cards sorted by urgency, big number, three lanes, condition line | "Each card leads with one number: health-hazard points, split into environment, animals and people. That is One Health on one card. Ecological condition is tracked separately and never raises an alarm on its own." |
| 0:40–1:15 | Tap **Mill Pond inlet** | "Mill Pond is High. A possible cyanobacterial bloom is backed by a trusted report with a photo, and dogs are in the water. Each rule shows why it fired and what to do: keep dogs out and report it for testing. The dead-fish report has no photo, so it is marked 'not yet corroborated' and doesn't count toward the warning." |
| 1:15–1:30 | Open "Warning record for health systems (FHIR Flag)" | "The warning is a FHIR Flag on the site Location. In R4, DetectedIssue and RiskAssessment can only point at patients; a Flag can point at a place. It links its evidence, expires after 14 days, and is updated in place, never duplicated." |
| 1:30–2:05 | Back → **Willow Creek** ("Needs verification") → **Confirm · I visited** on SYN-015 | "Willow Creek has serious signs from one report with no photo, so StreamFHIR asks for verification instead of sounding an alarm. A reviewer confirms it, and has to say on what basis; here, a site visit. Now it's High, and the Flag is ready. Undo puts it back. This is the human in the loop." |
| 2:05–2:40 | **New check** tab → pick site → colour *green*, surface *algal scum* → Next through the steps → **Check my record** | "Volunteers enter a check one question at a time, in plain words, with no JSON. Readings are optional. With no photo attached, the result is 'Needs a quick look': it will be shared as preliminary, and nothing the volunteer entered is changed." |
| 2:40–3:05 | **Samples** → SYN-010 → Check; then SYN-005 → Check | "Here a pH of 4.2 is possible but unusual, so it's kept and flagged. And pH 15 is impossible, so it can't be shared. The record becomes 22 FHIR resources: the site, a panel with one Observation per indicator, the photo, and Provenance for the pseudonymous observer." |
| 3:05–3:35 | Terminal: `docs/evidence-hapi.md` (or `python3 -m streamfhir validate-remote SYN-001 --with-profile`, **after** `publish-conformance --live`) | "We tested it live on the public HAPI FHIR server, with synthetic data only. Transactions are accepted, re-sending creates zero duplicates, our profiles validate with no issues, and a broken resource is rejected. The codes are published as CodeSystems under an example URL. We invented no LOINC codes, and the one we use was checked on a terminology server." |
| 3:35–3:50 | (optional) `python3 -m streamfhir --data data/real-wqp overview`, then back to Sites | "And it isn't only synthetic data. Run on a real public snapshot, 63 sampling visits from 45 US stream stations, it caught four pH values the source had labelled 'Molar'. StreamFHIR: citizen observations a health system can read, trust-check and act on. MIT licensed, 78 tests. Thank you." |

Recording notes:
- Keep the cursor still while speaking.
- On desktop, zoom the browser to 110 %.
- Total length must stay between 3 and 5 minutes.
