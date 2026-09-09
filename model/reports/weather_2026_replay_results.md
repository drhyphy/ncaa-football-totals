# 2026 archived-price weather replay

**No games met the frozen weather rule.** The replay therefore supplies no estimate of the rule's win rate or profitability. Its zero hypothetical stake and zero profit must not be displayed as a 0% measured ROI or as evidence of an edge.

| Cohort | Weather-covered games | Also price eligible and settled | Weather-rule bets | Weather-rule ROI |
|---|---:|---:|---:|---|
| Primary: TheOddsAPI DraftKings + FanDuel | 50 | 35 | 0 | Unavailable |
| Sensitivity: ESPN DraftKings100, explicit pregame state | 54 | 35 | 0 | Unavailable |

The cohort union contains 54 games on September 4–5, 2026: one calendar week. The two sources overlap, so neither rows nor results may be pooled as independent observations. All eight forecast batches were available. No game failed forecast coverage after venue eligibility.

For context only, taking every price-eligible Under on the same covered settled games would have returned:

| Matched baseline | Wins–losses–pushes | Hypothetical profit, one unit risk per bet | ROI |
|---|---:|---:|---:|
| Primary all-Under | 15–20–0 | −6.1850 units | −17.67% |
| ESPN sensitivity all-Under | 15–20–0 | −6.0873 units | −17.39% |

These are comparator returns, not weather-rule bets. They do not establish that the weather filter avoided losses predictively. Week-based confidence intervals are unavailable for this one-week check.

The plan was frozen before this weather retrieval and return evaluation, under SHA256 `30a34892cd467ddab0dfa1436608ed69ca73364869043949e2e893dd291d8277`. Strict thresholds, GFS previous_day2, four-hour wind aggregation, the first eligible capture and the original Under line/price rule were unchanged. Primary receipts were about 09:01 Eastern; ESPN about 09:01–09:02. They were not exactly 06:30 executions. Every included venue/roof record predated its selected quote.

An independent implementation recomputed all 54 game forecasts, 104 cohort weather rows and 70 overlapping cohort-price settlements directly from saved requests, forecast arrays, original normalized quote captures and final scores. It matched every flag, feature value and reported return. It also verified every selected receipt against the earliest valid cohort capture: **zero later-price substitutions**. The audit imports none of the weather evaluator. [Machine-readable audit](weather_2026_replay_audit.json), [audit code](../../scripts/audit_weather_replay_2026.py).

All 104 cohort forecast rows use a historical availability proxy: forecasts were retrieved after these games, and original dissemination receipts are absent. Local old quote metadata and hashes support chronology but do not independently attest capture times. The hypothesis was registered within this project after these games, so this is retrospective validation, not a prospective betting record. The historical 2024–2025 development result remains separate; this 2026 sample adds zero qualifying bets to its evidence.

Reproduce from the repository root:

```bash
cd model
python -m ncaaf_model.weather_replay_2026 --evaluate
cd ..
python scripts/audit_weather_replay_2026.py
```

Evaluation completed 2026-09-09 UTC. A string/numeric game-ID join error was repaired before successful evaluation; it changed no rules, quote choices or source data. The regression suite contains 18 passing tests.
