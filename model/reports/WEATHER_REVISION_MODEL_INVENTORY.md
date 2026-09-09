# Label-free weather-revision model inventory

2 archived captures: 0 scheduled and 2 manual. They contain 1 distinct requested GFS initialization(s). There are **0 designated primary games**, 0 source/receipt-verified probability-input pairs and 0 source/receipt-verified movement-input pairs. No model is fitted and no edge is estimated.

The observable selection rule is the first archived scheduled capture starting 24–48 hours before the pre-weather cohort kickoff. It is selected before weather, quotes or audit availability are checked. The immediately preceding archived scheduled capture must be 4–8 hours older, with a requested initialization exactly six hours earlier and unchanged context, coordinates, request options and valid hours. Missing data excludes that designated game; a later successful capture or an older successful predecessor cannot replace it.

| Capture start (UTC) | Profile / trigger | Requested initialization (UTC) | Cohort | Weather available | Both books | Weather + both books | Integrity |
|---|---|---|---:|---:|---:|---:|---|
| 2026-09-09T03:24:36.108097Z | pilot / manual_local | 2026-09-08T18:00:00Z | 86 | 62 | 51 | 37 | verified |
| 2026-09-09T03:33:35.961981Z | pilot / workflow_dispatch | 2026-09-08T18:00:00Z | 86 | 46 | 51 | 27 | verified |

Across adjacent archived captures, 46 games have comparable same-initialization weather metadata: 46 changed response-body hashes and 0 unchanged hashes. There are 0 comparable six-hour initialization pairs. These overlapping response observations are not independent training samples; body hashes do not measure the size or direction of a weather revision.

2 capture manifests pass source/receipt-integrity checks; 2 also match an existing independent audit's original SHA-256. This run verified 598 unique receipt envelopes and available opaque response bodies, and checked protocol, writer, collector, parser, team aliases and catalog against their recorded Git commits. It never decoded raw response bodies or read numeric weather measurements, game outcomes or future line changes. The machine report retains each original manifest/cohort hash, audit link/hash and recorded source reference.

A valid two-capture input contract also requires unique full-game DraftKings pairs at both captures, FanDuel at the decision, weather followed by successful context recheck followed by new book receipts, and a decision receipt still 24–48 hours before kickoff. Half-point lines alone qualify for the probability design. Integer-line input pairs can qualify for the separate movement design, whose next-capture target availability remains unexamined in this label-free inventory.

Limitations:

- First archived scheduled capture is observable; missing entire scheduler invocations cannot be inferred from this archive. No nominal slot is reconstructed.
- Designations precede forecast/quote availability and cannot be replaced by later successful captures. An absent immediate predecessor is not replaced by an older successful row.
- This inventory verifies metadata links, opaque response hashes and recorded Git sources. It does not independently reconstruct numeric weather or quote fields from raw bodies. Matching independent semantic audits are separately counted extra evidence, not a prerequisite for future source/receipt-verified inputs. A known audited manifest that changes is rejected.
- Requested initialization and receipt timestamps do not independently certify model vintage, first publication, sportsbook acceptance or continuous offer availability.
- Same-initialization body changes are response-provenance diagnostics. Counts describe overlapping game pairs, not independent weather revisions or football weeks.
- Movement input coverage is not a completed movement-target dataset: no next-capture price change or final outcome was read or calculated.
- Pilot and separately authorized season collection profiles retain their original windows and protocol identities; legacy archives without a profile are pilot. Neither collection changes the four active policies.
- No training/test boundaries are frozen by this inventory. The design requires at least 60 distinct games and two completed weeks before experimental fitting, followed by separately registered evaluation. This operational minimum does not establish confidence, profitability or eligibility for promotion.

Reproduce without network access:

```sh
python scripts/weather_revision_model_inventory.py --root .
```

References: [unfitted design](WEATHER_REVISION_MODEL_DESIGN.md), [capture protocol](WEATHER_REVISION_CAPTURE_PROTOCOL.md), [first capture audit](WEATHER_REVISION_FIRST_CAPTURE_AUDIT.md), [hosted capture audit](WEATHER_REVISION_HOSTED_CAPTURE_AUDIT.md), [machine inventory](weather_revision_model_inventory.json).
