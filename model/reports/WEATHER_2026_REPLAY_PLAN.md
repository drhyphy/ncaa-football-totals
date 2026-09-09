# Frozen 2026 replay at archived prices

Plan frozen before 2026 weather retrieval or outcome evaluation. SHA256: `30a34892cd467ddab0dfa1436608ed69ca73364869043949e2e893dd291d8277`. Machine-readable rules, game identities, selected prices, receipts, venue provenance and exact forecast requests are in `weather_2026_replay_plan.json`; subsequent reads verify the plan and source hashes. Replanning over the existing artifact is prohibited.

| Cohort | First qualifying captures | Exact-catalog outdoor/nonneutral games | Also passes Under price policy |
|---|---:|---:|---:|
| Primary: TheOddsAPI DraftKings + FanDuel | 74 | 50 | 35 |
| Sensitivity: ESPN DraftKings100 explicit pregame state | 97 | 54 | 35 |

The two cohorts overlap and must not be pooled. Their union contains 54 games requiring eight public GFS request batches. All included venue/roof metadata was received before its selected quote; no later roof assumption was needed. Exclusions across cohort records: 62 outside the frozen 100-ID coordinate catalog, two neutral/unknown-neutral, three missing exact event/team/kickoff venue context. Price-ineligible first captures remain passes; no later price was searched to replace them.

All requested games occurred September 4–5, 2026: **one calendar week**. Primary receipts were at 09:01 Eastern and ESPN receipts at 09:01–09:02, approximately 2.97–13.47 hours before kickoff. This is not a reconstruction of exactly 06:30 execution. Each primary bookmaker's market-update timestamp must be between zero and 60 minutes before the common archived receipt. The separate ESPN cohort has no market-update timestamp guarantee.

At the first qualifying capture, take the highest Under total at least as high as the cohort-books' median, provided decimal payout is at least `1+100/110`; then choose the best price and deterministic bookmaker-key tiebreak. The forecast criterion is unchanged: wind strictly above 7.78 mph, temperature strictly below 64.81°F and humidity strictly above 56.8%, all simultaneously. Use GFS previous_day2, kickoff-hour temperature/humidity, and the mean of four consecutive hourly wind forecasts starting at kickoff's floored hour. The original six-hour publication buffer remains tested against game-day06:30 Eastern; quote selection retains actual receipt time.

Stages, run from the `model` directory:

```bash
python -m ncaaf_model.weather_replay_2026 --fetch
python -m ncaaf_model.weather_replay_2026 --evaluate
```

Evaluation first loads final-score columns after forecast classification. It reports each cohort separately at its actual archived payouts, integer pushes refunding a flat one-unit hypothetical stake; the matched baseline takes every price-eligible Under on the same weather-covered settled games. Unavailable forecasts and unsettled outcomes remain excluded with an explicit denominator. Week-bootstrap confidence intervals are suppressed below eight covered calendar weeks, so this one-week replay cannot establish high-confidence profitability.

Local archive hashes and metadata support receipt chronology but are not independent timestamp attestation. Previous Runs history supplies a forecast-lead availability proxy, not original public dissemination receipts. This hypothesis was fixed after these games, before this particular weather/return evaluation; the replay must not be described as prospectively registered. No probabilities, EV estimates, live ledger entries or executed bets are produced.
