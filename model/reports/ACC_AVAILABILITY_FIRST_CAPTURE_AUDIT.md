# ACC availability: independent first-capture audit

**The archived first capture passes the independent byte, identity and timing checks. It did not capture any prices.** This is verification of a partial collection attempt, not completed player reporting, a forecasting result or betting evidence.

The manual capture ran **September 9, 2026, 08:16:30.214455–08:16:31.872356 UTC**, using frozen collector commit `65f7fb377b1b938338eaf6b19aa8ebea960e698e`. The [original manifest](../data/runtime/availability/runs/local-20260909T081630214455Z-1.json) remains unchanged, with SHA-256 `07e69ca80cd720c072ab878e6bfd369c944b877eee94ad431fb270252ed3b334`.

| Observed fact | Independently verified result |
|---|---|
| HTTP requests | 6; all returned 200; no HTTP/transport failure |
| Report | 1, explicitly `Report Pending` |
| Source key | Opaque `1182`; not used as a game ID |
| Teams | California and Syracuse, matched without assuming block order |
| Official game | ESPN `401858216`; kickoff September 12 at 19:30 UTC / 15:30 Eastern |
| Raw player-row arrays | Two arrays, both length 0; no availability interpretation |
| Official context recheck | Passed after the current-report receipt |
| Paired prices / two-book games | 0 / 0 |
| Models, outcomes or performance evaluated | None |

The script independently reads the original response bodies, verifies their SHA-256 and byte counts, verifies each content-addressed receipt, and checks source hashes against the recorded Git commit rather than assuming the current working tree is unchanged. It reconstructs the complete frozen seven-day official cohort, exact official team-name variants, report-to-game identity and literal date/time agreement. It checks that the inventory was durably frozen before the public POSTs and that the explicit public-access response preceded the current-report request. It imports no collector, source, model or scoring code and makes no HTTP requests.

The current report was received at **08:16:31.175451 UTC**. Its body SHA-256 is `7f16dceda02779e726ddb15f2392d2a65543e90ac2b02789127e09106cc4b6fc`, identical to the earlier pending preflight body. Its literal source publication labels remain September 9, `20:00:00`, `ET`; these are not rewritten as an actual issue time. The source allows 60-second caching and stale service. The later receipt and changed HTTP date therefore do not certify a new report or first publication. Pending empty rows do not mean that either team has no unavailable players.

The run stopped before `/odds/multi`. The selected-books and provider-event endpoints returned HTTP 200, but both archived responses lacked rate-limit headers. The original inherited collector required inventory allowance metadata, so it recorded `quota_reserve_or_metadata_unavailable`, with `remaining=null` and one required odds batch. This is an operational refusal caused by missing metadata, **not** evidence that the account had exhausted its allowance or that no sportsbook offered the game. The partial status and failure reason are preserved in the [machine audit](ACC_AVAILABILITY_FIRST_CAPTURE_AUDIT.json).

Because no odds response was requested, this attempt supplies no actual report-to-price latency, same-book price pair or live validation of price pairing. Synthetic tests cover that path; a subsequent separately versioned capture must demonstrate it with actual receipts. An operational correction must not attach later prices to this earlier report receipt or turn the first attempt into a successful price observation.

Reconstruction is available in [audit_availability_capture.py](../../scripts/research/audit_availability_capture.py). The JSON retains compact report metadata, original source/cache times, quota receipts, counts and hashes; it contains no player descriptions, movement labels or game outcomes. Audit passage establishes faithful reconstruction of this partial attempt. It does not verify a completed player schema, a quarterback identity, report dissemination speed, accepted sportsbook prices or a profitable edge.
