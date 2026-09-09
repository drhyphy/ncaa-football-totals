# Movement-history schema preflight results

Audit date: September 9, 2026. This bounded probe demonstrated movement-endpoint access for one upcoming game and historical paired prices for one older game. It did **not** demonstrate a usable historical movement series. The two older movement errors apply only to their exact event/book/market/line requests.

This report independently checks the original archived responses offline. No additional requests, outcome analysis, weather joins, returns, model fitting, or production-policy changes were made. Full credential-safe receipt metadata, request parameters, body hashes, and observations are in [the machine-readable results](movement_history_preflight_results.json). Original bodies remain in the ignored local archive; they are not bulk-published.

## Plan and execution integrity

The [original plan](MOVEMENT_HISTORY_PREFLIGHT_PLAN.md), committed at `4e6eb95`, selected the future game from the first pilot cohort by earliest kickoff, then numeric game ID, requiring both books. It selected the older game from existing pregame captures without using outcomes. The plan JSON's internal SHA-256 remains `4446aedf08bf2448044ab17ff9861238bc0dfb24021e31e3a3fc04aef5da5886`.

The first full-state response omitted quota headers. The [operational amendment](MOVEMENT_HISTORY_PREFLIGHT_AMENDMENT.md) used the reserved eighth call for a read-only selected-bookmakers request; that response also omitted them. Before any movement or history request, [correction `55fd698`](MOVEMENT_QUOTA_REQUIREMENT_CORRECTION.md) removed the unsupported requirement that those responses supply quota metadata. The original game, book, line-selection rules, and eight-call maximum remained fixed. No purchases or account mutations occurred.

Exactly eight calls were archived: six HTTP 200 responses and two HTTP 404 responses. All eight decoded-body hashes, byte counts, receipt content addresses, and request/receipt chronology checks passed; decoded bodies total 35,871 bytes. The first two responses did not certify remaining quota. The subsequent six reported limit 100 and remaining counts 98, 97, 96, 95, 94, and 93. Missing headers did not establish quota exhaustion.

All receipt times below are UTC on September 9, 2026; complete precision and hashes are retained in the JSON.

| Call | Endpoint and target | Received | HTTP | Observation |
| --- | --- | --- | --- | --- |
| 1 | `/odds/multi`, future event, both books | 03:58:00.671954 | 200 | One event; one total pair per book |
| 2 | `/bookmakers/selected` | 04:00:22.598403 | 200 | DraftKings and FanDuel; no quota headers |
| 3 | `/odds/movements`, future DraftKings 56.5 | 04:02:05.927002 | 200 | One tick; identical opening |
| 4 | `/odds/movements`, future FanDuel 56.5 | 04:02:06.086158 | 200 | One tick; identical opening |
| 5 | `/historical/events`, August 29 UTC | 04:02:06.252187 | 200 | 69 entries; one exact older-game match |
| 6 | `/historical/odds`, older event, both books, Totals | 04:02:56.189283 | 200 | DraftKings one pair; FanDuel 61 pairs |
| 7 | `/odds/movements`, older DraftKings 46.5 | 04:03:30.127512 | 404 | No data for this requested line |
| 8 | `/odds/movements`, older FanDuel 17.5 | 04:03:30.302708 | 404 | No data for this requested line |

## Upcoming-game schema and timestamp evidence

The future event is Villanova Wildcats at Louisville Cardinals, kickoff September 11 at 23:00 UTC: ESPN game `401858215`, provider event `70900784`. Both requested 56.5 lines were fixed from the first pilot's actual receipt at September 9, 03:25:00.935212 UTC. The fresh full-state pairs matched that earlier capture.

Full-state totals use explicit `hdp`, `over`, and `under` fields. Movement responses instead contain `eventid`, `bookmaker`, `movements`, and `opening`; each returned one tick with `hdp`, integer `timestamp`, `home`, and `away`. Each `opening` equals its sole tick. Neither response contains `latest`.

| Book | Full-state Over / Under decimal prices | Tick interpreted as Unix milliseconds | Full-state `updatedAt` | Difference |
| --- | --- | --- | --- | --- |
| DraftKings | 1.95 / 1.86 | September 8, 03:35:45.794 UTC | September 8, 03:35:45.803 UTC | 9 ms |
| FanDuel | 1.909 / 1.909 | September 7, 06:05:27.002 UTC | September 7, 06:05:27.014 UTC | 12 ms |

The millisecond interpretation is supported empirically by these close matches; this does not independently certify timestamp units, timestamp meaning, or original publication availability. The asymmetric DraftKings tuple is consistent with movement `home = Over` and `away = Under` for this observation. FanDuel's equal prices leave orientation ambiguous. A universal mapping across books or market types is not established.

One tick and its duplicate opening do not demonstrate a revision sequence, complete line history, or how suspended or withdrawn offers are represented. Source timestamps precede the project's receipt and must remain distinct from actual project observation times.

## Older-game identity and exact-query results

The older selection is North Carolina Tar Heels at TCU Horned Frogs, kickoff August 29 at 16:00 UTC, ESPN game `401856766`. Its earliest retained pregame capture was August 20 at 13:01:09 UTC, with both books at 47.5. Those existing local receipts are separate from the September 9 historical retrieval and are not independent timestamp attestations.

Discovery uniquely matched the exact participants and kickoff to provider event `70894628`. Provider participant IDs `4303` and `4307` are not ESPN team IDs `2628` and `153`. The historical full-state response matched that identity.

| Book | Valid, numerically unique paired rows | Returned line range | Frozen minimum-line probe | Explicit Over / Under decimal prices |
| --- | --- | --- | --- | --- |
| DraftKings | 1 | 46.5 | 46.5 | 1.92 / 1.89 |
| FanDuel | 61 | 17.5–77.5 | 17.5 | 1.002 / 36.000 |

Both totals markets report `updatedAt = 2026-08-29T15:59:36.155Z`, 23.845 seconds before the recorded kickoff. These historical closing-price records were retrieved September 9; their reported update time is not an original project receipt. The frozen rule selected the smallest valid returned line per book solely for a retention/schema probe. FanDuel 17.5 is the lowest line in an alternate-line ladder and is not asserted to be its main total.

The exact error messages were `No data found for market Totals with line 46.5` for DraftKings and `No data found for market Totals with line 17.5` for FanDuel. These establish no returned data for those two requests. Other lines, books, and dates remain untested; no book-wide or general historical-retention conclusion follows.

A line selected from a historical final-state response cannot define a decision-time evaluation cohort or establish which offers were available earlier. This probe therefore supplies schema evidence only. Outcome values are withheld from both derived reports. Complete history, settlement conventions, suspension handling, and original availability remain unresolved.
