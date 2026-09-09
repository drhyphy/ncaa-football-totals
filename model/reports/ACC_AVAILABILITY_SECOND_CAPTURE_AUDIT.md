# ACC availability: independent second-capture audit

**The second capture passes the independent original-byte audit, including actual DraftKings and FanDuel paired prices. The report remains Pending.** This verifies the report/price collection path; it provides no completed player-status information, probability estimate or betting edge.

The separate manual capture ran **September 9, 2026, 08:23:39.963125–08:23:41.615384 UTC** under collector version 2, frozen at commit `5997cfeaf35ace09c1100faa37b23a57cc4b331e`. All seven HTTP requests returned 200. One report matched California–Syracuse, ESPN game `401858216`, with kickoff September 12 at 19:30 UTC / 15:30 Eastern. An official context recheck after receiving the report confirmed the same teams, event and kickoff. Both source team-row arrays were empty; their completion remains unverified.

The [original manifest](../data/runtime/availability/runs/local-20260909T082339963125Z-1.json) has SHA-256 `b8cd87096b4952e8a326e0b31e247dfe9e59fc4b74d29abe7d02b53d819bd511`. The [independent machine audit](ACC_AVAILABILITY_SECOND_CAPTURE_AUDIT.json) records the exact source, quote, quota and receipt details.

| Book | Total | Under decimal odds | Over decimal odds | Original market change time, UTC |
|---|---:|---:|---:|---|
| DraftKings | 56.5 | 1.950 | 1.860 | September 8, 22:29:46.520 |
| FanDuel | 56.5 | 1.877 | 1.943 | September 8, 14:26:59.848 |

The current report arrived at **08:23:40.923602 UTC**. The odds request began at **08:23:41.494747** and its response arrived at **08:23:41.611681**, a report-to-price receipt gap of **0.688079 seconds**. The audit independently checked the full order: report request/receipt, official context request/receipt, odds request/receipt, then future kickoff. Both exact prices came from the newly received full-state response. Their older market-change timestamps remain separate; they are not substituted for receipt times. An observed provider offer does not prove that a sportsbook accepted a wager or continuously held the price.

The script rehashed all seven original bodies and their immutable receipts, reconstructed the frozen seven-day official cohort and unordered team-name match, and checked exact raw bookmaker/event/line/price fields against the retained quote records. It verified collector, protocol, alias, shared-helper and amendment hashes against the **recorded Git commit**. It imports no project collector, source, forecasting or scoring code and performs no HTTP requests, roster lookups or outcome analysis.

The [quota amendment](COLLECTION_QUOTA_METADATA_AMENDMENT.md) permits only the pre-existing bounded calls when headers are absent, while preserving reported-low-allowance and rejection stops. In this second actual capture, `/events` supplied limit 100 and remaining 98; the single odds batch supplied remaining 97. Only the selected-books response lacked allowance headers. Thus the live run verifies price capture and amendment provenance, while the **absent-inventory-header continuation itself is demonstrated by synthetic tests, not this particular run**. It used three provider calls, below the four-call cap. No remaining reserve is guaranteed when the provider omits its current allowance.

The source body hash is still `7f16dceda02779e726ddb15f2392d2a65543e90ac2b02789127e09106cc4b6fc`, identical to the first capture and earlier pending preflight. The source's literal September 9 / `20:00:00` / `ET` labels and caching policy remain intact. This is another observation of the pending body, not a verified status revision or first publication. The 8 p.m. policy deadline uses game-location local time; the metadata match here uses the feed's literal ET label for this Syracuse matchup.

The [first-capture audit](ACC_AVAILABILITY_FIRST_CAPTURE_AUDIT.md) and its six-request, zero-price partial manifest are preserved unchanged. These new prices belong only to the second receipt; they were not attached retroactively to the earlier attempt. There are two manual captures of one game, not two independent games or two bets. Both verified-completion and model-fit counts remain zero. No movement, final outcome, EV or profitability test was run.

Reconstruction: [audit_availability_capture.py](../../scripts/research/audit_availability_capture.py). Audit passage establishes integrity and linkage for these observations, not complete market coverage, report dissemination speed, player participation or profitable predictions.
