# Independent audit of the fixed weather rule

Audit date: 2026-09-09 UTC. Read-only audit of the completed 2024–25 study; no thresholds, selections, model inputs, or results were changed.

No arithmetic, outcome alignment, artificial balancing, or forecast-window leakage defect was found. An independent replay read every archived JSON weather response directly, reconstructed kickoff temperature/humidity and the fixed four-hour wind mean, and applied wind >7.78 mph, temperature <64.81°F, and relative humidity >56.8%. All 1,160 feature rows and all 85 selections matched. Game IDs were unique; location indexes and stadium/grid proximity matched; market totals, provider IDs, and outcomes matched the repaired source for every row.

| Period | Covered games | Covered under wins/losses | Selected wins/losses | Selected ROI at assumed −110 |
|---|---:|---:|---:|---:|
| 2024 | 566 | 283 / 283 | 24 / 16 | +14.55% |
| 2025 | 594 | 297 / 297 | 31 / 14 | +31.52% |
| Both | 1,160 | 580 / 580 | 55 / 30 | +23.53% |

The exact covered-game balances are present in direct score-versus-line comparisons, with no balancing transformation in the reviewed selection code. The full repaired source is unbalanced: 2024 has 439 under wins and 481 losses; 2025 has 477 wins and 466 losses. The covered balance appears coincidental. Every covered market line is a half-point, explaining the absence of pushes.

The covered market providers are ESPN BET 58 (1,156 games) and DraftKings 100 (4); provider 59 is absent. Twelve selected games sampled across both years were checked against the cached primary ESPN provider objects. Their exact non-live provider identities and scalar totals match the study. Opening totals are not substituted. These checks establish the selected provider role, not the time the quote was offered.

All weather inputs use the GFS `previous_day2` product. [Open-Meteo's primary documentation](https://open-meteo.com/en/docs/previous-runs-api) states that this suffix represents forecasts 48 hours before valid time. The last nominal reference time used for any game's wind window, plus the six-hour publication buffer, precedes its 06:30 Eastern decision by at least 22.5 hours. The plan was saved at 00:39:02 UTC on September 9, before the first archived response at 00:39:44. The response receipts are from 2026; they do not themselves prove the original public dissemination timestamps.

All 1,160 research rows retain null individual win probabilities and EV, with `bet_eligible=false`. The 55/85 group win rate must not become an individual matchup probability. The reported week bootstrap uses 34 calendar-week clusters and appropriately remains conditional on these data and assumptions.

The remaining material limits are unverified historical bookmaker quote/close/06:30 availability, assumed −110 prices, current roof metadata applied to prior years, reused overall development outcomes, and uncertainty from a small selected sample and previous research searches. The result supports a separate forward paper experiment; it does not establish a future profitable edge. This bounded audit did not reproduce the original paper or verify every historical roof configuration.
