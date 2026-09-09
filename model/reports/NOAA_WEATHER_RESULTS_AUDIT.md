# Independent NOAA classification and return audit

**Both audits passed. The earlier-period NOAA experiment does not confirm a profitable weather edge.** The frozen rule selected 130 of 1,747 games: 68 wins, 62 losses, no pushes, and −0.181818 hypothetical units at assumed −110. ROI is **−0.14%**, with a descriptive calendar-week bootstrap 95% interval of **−16.12% to +14.26%**.

This result is a separate 2021–2023 forecast-source replication. It does not change the live weather policy or justify combining its returns with the 2024–2025 Open-Meteo experiment.

## Raw forecast reconstruction

The [classification audit script](../../scripts/audit_noaa_weather_classifications.py) imports no project planning, fetching, or evaluation module. It verifies the frozen input/receipt hashes and independently decodes every original GRIB into a full two-dimensional grid. The study used sparse element extraction. The audit constructs a different corner ordering and uses rectangular interpolation algebra, then independently computes four-hour scalar wind, kickoff-hour temperature/humidity, UTC/Eastern availability timing, and the strict fixed three-condition intersection.

All **3,572 fields**, **17,470 game-field associations**, and **1,747 game classifications** match. No field or game is unavailable. Retained raw corner values match exactly; the largest feature discrepancy is `2.14e-13`, consistent with floating-point arithmetic. The fixed rule matches 35 games in 2021, 56 in 2022, and 39 in 2023. No outcome was read by this script. Its detailed receipt is [noaa_weather_classification_audit.json](noaa_weather_classification_audit.json).

The audited classification SHA-256 is `132874fe46c150121fad2b40d7aefed8b17fb5593f6318b3db3f062ea31d8951`. The original request and inventory hashes, historical venue restrictions, and S3 timestamp qualification are documented in the [source audit](NOAA_WEATHER_SOURCE_AUDIT.md).

## Independent settlement and uncertainty

After the fixed evaluator completed, the separate [results audit script](../../scripts/audit_noaa_weather_results.py) rebuilt the cohort from independently reconstructed weather and the original repaired ESPN, validated CFBD, and schedule files. It checked one-to-one game identity, season and team agreement, repaired-source precedence, source labels, final status, score sums, and exact historical reference totals. No later line or favorable source was substituted.

All 1,747 games survive the independent join, with no outcome exclusions. The audit reconstructs integer win/loss/push accounting, all-Under and nonselected benchmarks, and all nine pooled/season/source summaries. It independently accumulates the frozen 10,000 week draws as week multiplicities, checks both 95% and 99% paired intervals, verifies the active-week ratio-score t calculation, and reproduces every leave-one-week-out result. See [noaa_weather_results_audit.json](noaa_weather_results_audit.json).

| Period | Rule W–L–P | Rule bets | Assumed −110 ROI |
| --- | ---: | ---: | ---: |
| 2021 | 21–14–0 | 35 | +14.55% |
| 2022 | 31–25–0 | 56 | +5.68% |
| 2023 | 16–23–0 | 39 | −21.68% |
| Pooled | 68–62–0 | 130 | −0.14% |

Across the same 1,747-game coverage, every-Under settlement is 850–884–13 and **−6.37% ROI**. The primary selected-minus-all-Under contrast is **+6.23 percentage points**, with descriptive 95% interval **−9.33 to +20.09 points** and 99% interval **−14.97 to +23.53 points**. Neither interval establishes an improvement. The rule's own 99% ROI interval is **−21.68% to +18.11%**.

There are 45 covered Eastern Monday–Sunday weeks and 30 weeks containing selected bets. No pooled bootstrap draw has zero selected stakes. The active-week cluster-t 95% interval is **−16.18% to +15.90%**. Removing one covered week at a time yields rule ROI from **−4.55% to +3.68%**.

## Interpretation

The profitable 2021 and 2022 point estimates do not rescue the pooled null result. All 39 selected 2023 games belong to the ESPN nonlive provider-58 source group, so era and source are entangled; this is not evidence for switching to a favorable historical source. One small Bovada group contains only one selected win. Its degenerate bootstrap interval reflects the single observed selected result, not high-confidence profitability, and should not be used as a source recommendation.

Week resampling cannot fully address cross-week team dependence or the project's unknown wider research search. Original forecast grids, interpolation, era and market sources differ from the later Open-Meteo study. Historical outcomes were previously reused elsewhere, venue/roof metadata were retrieved retrospectively, and S3 timestamps remain an availability proxy. The archived totals have no certified executable morning price. These limits apply in addition to the wide statistical uncertainty.

No threshold, source policy, model, prospective registry, or locked forecast was changed by this audit. The scripts test the frozen experiment; they do not search for another weather strategy.

Reproduce from the repository root after the immutable classification and results exist:

```bash
python scripts/audit_noaa_weather_classifications.py
python scripts/audit_noaa_weather_results.py
```
