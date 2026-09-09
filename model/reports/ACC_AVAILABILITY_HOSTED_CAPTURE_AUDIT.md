# ACC availability: independent hosted-capture audit

**The hosted manual capture passes the independent original-byte audit.** GitHub run `34329726247`, attempt `1`, used `workflow_dispatch`; it was not a scheduled observation and does not establish scheduler reliability. The [original manifest](../data/runtime/availability/runs/34329726247-1.json) remains unchanged, with SHA-256 `b9ec913a7646b051d7eb5c1a55fa1f8ed1a70f599ebfa97acc340a05ce4fc862`.

The capture ran **September 9, 2026, 08:35:06.750438–08:35:08.271901 UTC**. All seven HTTP requests returned 200. One explicitly Pending California–Syracuse report matched ESPN game `401858216`, kickoff September 12 at 19:30 UTC / 15:30 Eastern. The subsequent official summary recheck confirmed its identity and kickoff. Both player-row arrays remained empty; no completed report or player availability was inferred.

| Book | Total | Under decimal odds | Over decimal odds |
|---|---:|---:|---:|
| DraftKings | 56.5 | 1.950 | 1.860 |
| FanDuel | 56.5 | 1.877 | 1.943 |

The report arrived at **08:35:07.538867 UTC**; the odds request began at **08:35:08.144182** and returned at **08:35:08.269334**. The independently reconstructed report-to-price receipt gap is **0.730467 seconds** for both books. Report receipt, official context recheck and quote request/receipt occur in the required order before kickoff. Original market-change times remain separate: September 8 at 22:29:46.520 UTC for DraftKings and 14:26:59.848 UTC for FanDuel. Newly observed provider prices are not proof of sportsbook acceptance.

The selected-books response lacked allowance headers. The provider inventory reported limit 100 / remaining 87, and the odds batch reported remaining 86. Thus this run verifies the hosted price path with a known inventory allowance; it does not itself exercise continuation without inventory quota headers. It used three provider calls, below the four-call cap, with no recorded request failure. The versioned amendment and synthetic absent-header tests remain distinct evidence.

The [machine audit](ACC_AVAILABILITY_HOSTED_CAPTURE_AUDIT.json) verifies all seven original body and receipt hashes, reconstructs the frozen official cohort and unordered exact team-name match, and checks raw event/book/line/prices and receipt sequencing. Source pins match capture checkout commit `620b77dcc762b3d370e8a18b20ae60cda0aae1d6`. The workflow file's hash also matches dispatch commit `fcb86733affe6e29075638f2581ad6504f379421`; the differing checkout and dispatch commits were checked separately.

The current-report body still has SHA-256 `7f16dceda02779e726ddb15f2392d2a65543e90ac2b02789127e09106cc4b6fc`, identical to the earlier Pending observations. Its source publication labels and caching policy do not certify completed reporting, first publication or a status revision. This is another observation of the same game, not another independent game or bet.

The [first partial](ACC_AVAILABILITY_FIRST_CAPTURE_AUDIT.md) and [second local](ACC_AVAILABILITY_SECOND_CAPTURE_AUDIT.md) audits are preserved. The existing [independent helper](../../scripts/research/audit_availability_capture.py) made no HTTP requests or project-code imports. No roster, movement, outcome, model fit, EV or profitability analysis was performed. Audit passage establishes integrity and linkage of these observations, not a betting edge.
