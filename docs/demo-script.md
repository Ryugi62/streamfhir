# StreamFHIR — demo video script (target 3:40, English)

Setup before recording: `python3 -m streamfhir serve`, browser at `http://127.0.0.1:8000` in a 390 px-wide window (or phone emulation), a terminal next to it. All data shown is synthetic.

| Time | Screen | Narration |
|---|---|---|
| 0:00–0:20 | Sites screen (`#/`) — "5 stream sites, 2 need action" | "Citizens can see what is happening in urban streams: a sewage smell, a green scum, dogs in the water. But their reports rarely reach the systems that protect public health. StreamFHIR fixes that by speaking the standard health systems already use: HL7 FHIR." |
| 0:20–0:45 | Scroll the site cards; point at the big number and the three lanes | "Each card starts with one number: risk points. Every point comes from a printed rule, split into three One Health lanes — environment, animals and people. Five synthetic sites: two high, two moderate, one low." |
| 0:45–1:25 | Tap **Mill Pond inlet** (`#/site/S-MILL`) | "Mill Pond scores 15. Why? Poor habitat and nutrient enrichment, a possible toxic algal bloom seen by two people, dogs in that water — and one dead-fish report. Notice the tag: 'Single report — verify'. We never hide uncertainty; we show which rules are confirmed by two observers or a photo." |
| 1:25–1:45 | Scroll to **Citizen checks**; open "All 9 rules" | "Below are the citizen checks behind the score, including two that need a quick look — one has no photo, one was taken 400 metres from the site. And here are all nine rules. No black-box model, no accuracy claims." |
| 1:45–2:05 | Open "FHIR Flag for health systems"; press **Send alert to FHIR server (dry run)** | "Because the site is high risk, StreamFHIR emits a FHIR Flag whose subject is the site Location — DetectedIssue and RiskAssessment in R4 can only point at patients, a Flag can point at a place. The button is a dry run by default." |
| 2:05–2:40 | Tap **Check** tab → choose `SYN-010` → **Check this record** | "Now a single record. A citizen at Willow Creek entered pH 4.2. That is possible but unusual, so the status is 'Needs a quick look'. The value is kept exactly as reported and shared as preliminary, with a note. 19 FHIR resources are ready: the site, one Observation per indicator grouped by a panel, the photo, and Provenance for the pseudonymous observer." |
| 2:40–2:55 | Choose `SYN-005` → **Check this record** | "This one says pH 15 — impossible. 'Can't share yet', with one plain sentence on what to fix. Nothing is sent." |
| 2:55–3:10 | Open "See the FHIR data" on SYN-010 briefly | "Codes are published as CodeSystems under an example canonical — we did not invent LOINC codes — with a ValueSet and a minimal profile." |
| 3:10–3:30 | Terminal: `docs/evidence-hapi.md` (or re-run `python3 -m streamfhir validate-remote SYN-001 --with-profile`) | "We tested it live on the public HAPI FHIR R4 server with synthetic data: the transaction was accepted, a second visit reused the same Location, and the generated Observations validate against our profile with no issues — while a broken one is rejected." |
| 3:30–3:40 | Back to Sites screen | "StreamFHIR: citizen observations that a health system can already read, trust-check and act on. Open source, MIT, 46 tests. Thank you." |

Recording notes: keep the cursor still while speaking; zoom the browser to 110 % on desktop; total length must stay between 3 and 5 minutes.
