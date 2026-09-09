# Frozen weather rule: statistical robustness

The point return is resilient to single-week deletion, but the positive lower confidence bound is fragile. The unchanged 85 selections produce 55 wins, 30 losses and +20.00 units (+23.53% ROI) at assumed −110. The weekly cluster-t 99% interval and four-strategy Bonferroni 95% sensitivity both include zero. This is development evidence for a forward paper experiment, not a proven edge.

## Dependence and uncertainty

| Method | Clusters / df | Standard error | 95% interval | 99% interval |
|---|---|---:|---|---|
| Calendar-week score sandwich | 34 / 33 | +10.48% | +2.21% to +44.85% | -5.11% to +52.17% |
| Active betting weeks only | 21 / 20 | +10.58% | +1.47% to +45.59% | -6.57% to +53.62% |

The originally published percentile week-bootstrap 95% interval is +1.42% to +43.92%. Of 34 covered weeks, 13 contain no bets. Bet-count concentration gives an inverse-HHI equivalent of 11.63 equally sized weeks; this is a concentration diagnostic, not substituted degrees of freedom.

For weekly selected counts N and profits P, estimate theta = sum(P)/sum(N), score U = P − theta × N, and SE = sqrt[G/(G−1) × sum(U²)] / sum(N). Intervals use t(G−1). This preserves per-bet weighting. The t reference and small-sample factor are approximations, requiring independent weeks; they do not handle persistent cross-week team effects. Zero-bet weeks contain no score information, hence the active-week sensitivity.

Method background: [Cameron and Miller, A Practitioner's Guide to Cluster-Robust Inference](https://cameron.econ.ucdavis.edu/research/Cameron_Miller_JHR_2015_February.pdf).

## Multiplicity sensitivity

All intervals below use the calendar-week estimate and two-sided alpha=0.05/m. The four-strategy case is the requested current comparison; larger families are illustrative. The complete prior adaptive search count is unknown, so these are not retroactive familywise-valid discovery claims.

| Hypothetical comparisons | Per-interval confidence | Bonferroni 95% family sensitivity interval | Adjusted two-sided p |
|---:|---:|---|---:|
| 1 | 95.000% | +2.21% to +44.85% | 0.0315 |
| 4 | 98.750% | -4.15% to +51.21% | 0.1261 |
| 10 | 99.500% | -7.99% to +55.05% | 0.3154 |
| 25 | 99.800% | -11.64% to +58.69% | 0.7884 |
| 100 | 99.950% | -16.91% to +63.97% | 1.0000 |

## Week concentration and deletion

Leave-one-week-out ROI ranges from +16.53% to +28.05%. The recalculated 95% cluster-t lower bound remains above zero in 29 of 34 deletions (including zero-bet weeks). These are influence diagnostics, not alternative rules selected for deployment.

The most profitable week, 2025-11-24, has 8 wins and 0 losses, contributing +7.27 units (36.36% of net profit). Without it, ROI is +16.53%, with 95% cluster-t interval -1.90% to +34.96%.

| Eastern Monday week | Covered | Bets | W–L–P | Profit units | ROI after removing week | 95% cluster-t after removal |
|---|---:|---:|---|---:|---:|---|
| 2024-08-19 | 2 | 0 | 0–0–0 | +0.00 | +23.53% | +2.18% to +44.88% |
| 2024-08-26 | 52 | 0 | 0–0–0 | +0.00 | +23.53% | +2.18% to +44.88% |
| 2024-09-02 | 49 | 1 | 0–1–0 | -1.00 | +25.00% | +3.67% to +46.33% |
| 2024-09-09 | 34 | 1 | 1–0–0 | +0.91 | +22.73% | +1.14% to +44.31% |
| 2024-09-16 | 37 | 0 | 0–0–0 | +0.00 | +23.53% | +2.18% to +44.88% |
| 2024-09-23 | 38 | 5 | 4–1–0 | +2.64 | +21.70% | -0.75% to +44.16% |
| 2024-09-30 | 36 | 0 | 0–0–0 | +0.00 | +23.53% | +2.18% to +44.88% |
| 2024-10-07 | 35 | 1 | 1–0–0 | +0.91 | +22.73% | +1.14% to +44.31% |
| 2024-10-14 | 38 | 1 | 1–0–0 | +0.91 | +22.73% | +1.14% to +44.31% |
| 2024-10-21 | 39 | 1 | 1–0–0 | +0.91 | +22.73% | +1.14% to +44.31% |
| 2024-10-28 | 34 | 3 | 0–3–0 | -3.00 | +28.05% | +8.20% to +47.90% |
| 2024-11-04 | 37 | 2 | 1–1–0 | -0.09 | +24.21% | +2.42% to +46.00% |
| 2024-11-11 | 38 | 6 | 5–1–0 | +3.55 | +20.83% | -1.56% to +43.22% |
| 2024-11-18 | 43 | 14 | 8–6–0 | +1.27 | +26.38% | +1.89% to +50.86% |
| 2024-11-25 | 48 | 4 | 1–3–0 | -2.09 | +27.27% | +6.46% to +48.08% |
| 2024-12-02 | 2 | 0 | 0–0–0 | +0.00 | +23.53% | +2.18% to +44.88% |
| 2024-12-16 | 4 | 1 | 1–0–0 | +0.91 | +22.73% | +1.14% to +44.31% |
| 2025-08-18 | 1 | 0 | 0–0–0 | +0.00 | +23.53% | +2.18% to +44.88% |
| 2025-08-25 | 55 | 0 | 0–0–0 | +0.00 | +23.53% | +2.18% to +44.88% |
| 2025-09-01 | 58 | 0 | 0–0–0 | +0.00 | +23.53% | +2.18% to +44.88% |
| 2025-09-08 | 45 | 0 | 0–0–0 | +0.00 | +23.53% | +2.18% to +44.88% |
| 2025-09-15 | 39 | 0 | 0–0–0 | +0.00 | +23.53% | +2.18% to +44.88% |
| 2025-09-22 | 36 | 0 | 0–0–0 | +0.00 | +23.53% | +2.18% to +44.88% |
| 2025-09-29 | 36 | 1 | 1–0–0 | +0.91 | +22.73% | +1.14% to +44.31% |
| 2025-10-06 | 38 | 3 | 1–2–0 | -1.09 | +25.72% | +4.16% to +47.28% |
| 2025-10-13 | 44 | 2 | 1–1–0 | -0.09 | +24.21% | +2.42% to +46.00% |
| 2025-10-20 | 39 | 4 | 2–2–0 | -0.18 | +24.92% | +2.77% to +47.06% |
| 2025-10-27 | 35 | 5 | 4–1–0 | +2.64 | +21.70% | -0.75% to +44.16% |
| 2025-11-03 | 34 | 5 | 3–2–0 | +0.73 | +24.09% | +1.46% to +46.72% |
| 2025-11-10 | 40 | 13 | 9–4–0 | +4.18 | +21.97% | -3.06% to +47.00% |
| 2025-11-17 | 46 | 4 | 2–2–0 | -0.18 | +24.92% | +2.77% to +47.06% |
| 2025-11-24 | 41 | 8 | 8–0–0 | +7.27 | +16.53% | -1.90% to +34.96% |
| 2025-12-01 | 3 | 0 | 0–0–0 | +0.00 | +23.53% | +2.18% to +44.88% |
| 2025-12-15 | 4 | 0 | 0–0–0 | +0.00 | +23.53% | +2.18% to +44.88% |

## Team and venue concentration

The selections span 49 venues and 88 teams. The largest venue contributes 5.88% of bets; the five busiest venues contribute 23.53%. Removing any one venue leaves ROI from +21.06% to +27.27%. Removing every game involving one team, across both home and away roles, leaves ROI from +21.06% to +27.27%.

| Venue (top 10 by count) | Bets | W–L | Associated profit | ROI without venue |
|---|---:|---|---:|---:|
| Kelly/Shorts Stadium | 5 | 4–1 | +2.64 | +21.70% |
| Memorial Stadium (Norman, OK) | 4 | 2–2 | -0.18 | +24.92% |
| Donald W. Reynolds Razorback Stadium | 4 | 1–3 | -2.09 | +27.27% |
| Spartan Stadium | 4 | 3–1 | +1.73 | +22.56% |
| Bill Snyder Family Stadium | 3 | 2–1 | +0.82 | +23.39% |
| Kidd Brewer Stadium | 3 | 3–0 | +2.73 | +21.06% |
| Notre Dame Stadium | 3 | 3–0 | +2.73 | +21.06% |
| Scheumann Stadium | 3 | 3–0 | +2.73 | +21.06% |
| Yager Stadium | 3 | 3–0 | +2.73 | +21.06% |
| Camp Randall Stadium | 2 | 2–0 | +1.82 | +21.91% |

| Team (top 10 by involvement) | Games | Home / away | W–L | Associated profit | ROI without team |
|---|---:|---|---|---:|---:|
| Central Michigan Chippewas | 5 | 5 / 0 | 4–1 | +2.64 | +21.70% |
| Michigan State Spartans | 4 | 4 / 0 | 3–1 | +1.73 | +22.56% |
| Ohio State Buckeyes | 4 | 2 / 2 | 2–2 | -0.18 | +24.92% |
| Oklahoma Sooners | 4 | 4 / 0 | 2–2 | -0.18 | +24.92% |
| App State Mountaineers | 4 | 3 / 1 | 3–1 | +1.73 | +22.56% |
| Ball State Cardinals | 4 | 3 / 1 | 3–1 | +1.73 | +22.56% |
| Eastern Michigan Eagles | 4 | 2 / 2 | 3–1 | +1.73 | +22.56% |
| Northern Illinois Huskies | 4 | 2 / 2 | 1–3 | -2.09 | +27.27% |
| Toledo Rockets | 4 | 1 / 3 | 3–1 | +1.73 | +22.56% |
| Western Michigan Broncos | 4 | 2 / 2 | 3–1 | +1.73 | +22.56% |

Each game has two team appearances. Associated profit sums double-count the portfolio; every individual team-deletion calculation removes each game only once.

Not computed: ordinary home-role/away-role two-way clustering misses dependence when the same team switches roles. Concentration and deletion are descriptive, not a replacement covariance estimator.

## Further evidence needed

- Preserve the frozen rule, price filter, and version before observing new outcomes; retain unavailable games and abstentions.
- Evaluate prospectively recorded game-day forecasts and accepted/observed line prices, with a separate deduplicated ledger.
- Accumulate more independent active weeks and seasons across teams/venues; report both nominal and multiplicity-aware uncertainty on a prespecified review schedule.
- Do not infer an individual matchup probability or stake optimization from the 55/85 historical group record.

## Limits

- Calendar-week clusters are assumed independent; persistent teams, weather regimes, seasons, or bookmaker-source errors can violate this.
- Only two seasons and 21 active betting weeks; unequal cluster sizes and the t reference do not give exact finite-sample coverage.
- Bonferroni sensitivities do not retroactively establish familywise control for the unknown cumulative adaptive research search.
- All 2024-25 outcomes are reused development data; every interval and deletion analysis is descriptive.
- Archived non-live line timing and actual historical prices are unavailable; returns assume -110 and the historical roof/forecast-provenance limitations remain.
- A positive leave-one-group-out point estimate does not establish calibration, future profitability, or validity of a confidence interval.

Reproduce with `python scripts/weather_robustness.py --root .`. Full weekly, seasonal, home/away team, venue, and multiplicity results with input hashes are in `weather_robustness.json`. The frozen rule, probabilities, runtime, and source artifacts are unchanged.
