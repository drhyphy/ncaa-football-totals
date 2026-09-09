# Independent 2026 replay audit

Audit passed 2026-09-09 UTC. `scripts/audit_weather_replay_2026.py` uses independent calculations, without importing the weather evaluator.

- Verified frozen plan/input hashes, all eight request/payload hashes, model, fields, units, grid proximity and chronological availability policy.
- Recomputed 54 distinct forecasts and 104 overlapping cohort rows from raw hourly arrays: every numeric feature and strict three-condition flag matched.
- Re-selected the first coherent archived source/book capture from the original normalized quote data, independently of weather and outcomes. All selected receipts and price choices matched; no later-price substitutions.
- Recomputed 70 overlapping cohort settlements from raw final scores and actual archived payout, including the integer-push rule. Aggregate returns matched.
- Confirmed all included venue/roof evidence predates its quote; all 104 cohort forecast rows require a late-retrieved archive availability proxy.

There were no weather-rule bets in either cohort. ROI is undefined, and no profitability confidence follows. The successful audit establishes internal data/calculation consistency; it does not independently authenticate historical public dissemination, certify bet execution, or convert a retrospective hypothesis into a preregistered strategy. [Detailed audit](weather_2026_replay_audit.json), [results](weather_2026_replay_results.md).
