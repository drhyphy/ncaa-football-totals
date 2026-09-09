# Movement-history preflight plan

**Seven planned authenticated reads; eight maximum.** This plan selects two games and a small set of endpoint checks without loading scores, winners, weather classifications or betting results. No network call was made to prepare it. It does not alter the live policies or frozen weather-revision pilot. The machine-readable [plan and source hashes](movement_history_preflight_plan.json) pin the selections and request arguments; its internal plan hash is `4446aedf08bf2448044ab17ff9861238bc0dfb24021e31e3a3fc04aef5da5886`.

| Purpose | Selected matchup | Kickoff UTC | Identity and original receipt |
|---|---|---|---|
| Upcoming movement schema | Villanova Wildcats at Louisville Cardinals | September 11, 2026, 23:00 | ESPN `401858215`; Odds-API.io `70900784`; first paired quote receipt September 9, 03:25:00.935212 UTC |
| Older movement retention | North Carolina Tar Heels at TCU Horned Frogs | August 29, 2026, 16:00 | ESPN `401856766`; Odds-API.io ID needs discovery; earliest retained receipt August 20, 13:01:09 UTC |

The upcoming selection uses the first pilot capture by actual start: `local-20260909T032436108097Z-1`, started September 9 at 03:24:36.108097 UTC. Among its 51 games with both selected books, choose earliest kickoff then numeric ESPN game ID. The frozen cohort and original quote-body hashes were checked. The chosen provider ID is already supported by that actual `/odds/multi` receipt; no fresh identity search is needed unless the selected event fails validation, in which case record the mismatch rather than substituting a game.

The older selection uses original captured pregame quotes with kickoff on or before September 2 at 00:00 UTC, at least seven days before the plan's September 9 UTC reference. Among eight eligible games, sort recorded receipt, then kickoff, numeric ESPN ID and book. The resulting TCU–North Carolina event has home/away ESPN IDs `2628`/`153`. Its The Odds API event ID is `2c288580da16e12d35355f2548721be6`; this is **not** an Odds-API.io ID. The original `ncaaf_20260820T130109Z.json` SHA-256 is `d7d706a0c31a9a53aa4c23f66e4080ba0f33b13bca6a8b4524cd484522ed0d95`. Its sidecar's stated receipt and raw hash match. This is local timestamp evidence, not independent attestation; see [archive provenance](ARCHIVED_2026_QUOTES.md).

The following original pairs provide schema comparison points. Prices are Over/Under decimal odds, preserving the actual payout; no probability calculation is intended.

| Event / original receipt | DraftKings | FanDuel |
|---|---|---|
| Louisville–Villanova, September 9 03:25:00.935212 UTC | 56.5; 1.95 / 1.86 | 56.5; 1.909 / 1.909 |
| TCU–North Carolina, August 20 13:01:09 UTC | 47.5; 1.9090909090909092 / 1.9090909090909092 | 47.5; 1.8695652173913042 / 1.9523809523809523 |

**Fixed call sequence.** All paths use `https://api.odds-api.io/v3`; authentication remains outside public request URLs and artifacts.

| Call | Endpoint and public parameters | Purpose |
|---|---|---|
| 1 | `/odds/multi`, `eventIds=70900784`, `bookmakers=DraftKings,FanDuel` | Fresh explicit Over/Under fields and quota headers. Keep movement lines fixed at the earlier captured 56.5 even if today's full-state line differs. |
| 2 | `/odds/movements`, `eventId=70900784`, `bookmaker=DraftKings`, `market=Totals`, `marketLine=56.5` | Upcoming DraftKings schema. |
| 3 | Same, `bookmaker=FanDuel` | Upcoming FanDuel schema. |
| 4 | `/historical/events`, `sport=american-football`, `league=usa-college`, `from=2026-08-29T00:00:00Z`, `to=2026-08-30T00:00:00Z` | Resolve the fixed older event by exact participant identity/order and kickoff. Do not inspect score fields or match a different event after failure. |
| 5 | `/historical/odds`, resolved older `eventId`, `bookmakers=DraftKings,FanDuel`, `markets=Totals` | Check explicit `hdp`/`over`/`under` closing-price schema. |
| 6 | `/odds/movements`, older ID, `bookmaker=DraftKings`, `market=Totals`, the line selected below | Older DraftKings retention probe. |
| 7 | Same, `bookmaker=FanDuel`, that book's selected line | Older FanDuel retention probe. |

For calls 6–7, choose each book's smallest positive finite half-point **full-game Totals** line in call 5 with one unambiguous pair of explicit decimal Over/Under prices greater than one. Record the selected line and its source body before requesting movements. If a book's line or the provider identity is unavailable/ambiguous, skip its dependent calls. Do not guess generic movement `home`/`away` fields mean Over/Under. Symmetric prices, such as the earlier FanDuel 1.909/1.909 pair, cannot resolve side mapping by themselves. Endpoint contracts and remaining ambiguities are detailed in the [documentation review](../../docs/ODDS_MOVEMENT_FEASIBILITY.md), [movement reference](https://docs.odds-api.io/api-reference/odds/get-odds-movements), and [historical guide](https://docs.odds-api.io/guides/historical).

**The older closing-line choice is solely a retention/schema probe.** It does not replace the original captured 47.5 reference, identify a historical main line, or define a backtest sample. Conditioning later research on which final line retained movement history would use later market information. One successful event/book/line says nothing about complete historical line coverage.

The eighth call is reserved only for a source-documented continuation needed to complete the same exact-day historical discovery, if explicitly returned. Otherwise it remains unused. There is no request grid, automatic retry, alternate event/date/line search, bulk closing-lines purchase, subscription expansion or bookmaker change. After call 1, require fresh remaining-quota metadata sufficient for seven possible further requests plus the existing 20-request reserve; recheck quota after every response and stop before crossing the reserve or on HTTP 401/403/429. A failed endpoint is a recorded finding, not authorization to buy access.

Archive original bodies, sanitized request arguments, actual request/receipt times, HTTP status/headers and hashes under a separate preflight directory. Derived output should contain identity checks, field names, record counts, explicit side/price matches and timestamp-unit/ordering observations. Keep provider-reported historical times separate from the present receipt. Do not infer pregame role, uninterrupted availability, suspension handling, exhaustive retention or an executable price from timestamps alone. Do not extract outcomes from historical event bodies, join weather, calculate returns/EV, or promote a signal. Record schema limitations and stop after the bounded checks.
