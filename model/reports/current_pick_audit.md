# Current primary picks: independent audit

**Arithmetic and temporal reconstruction pass. Calibration remains unvalidated.** These are legitimate paper experiments; this audit provides no high-confidence profitability claim.

Snapshot: 2026-09-09T00:32:43Z; model fingerprint `cf764f803b38ecfd`; Normal residual sigma 15.9113 points.

| Matchup | Forecast / line | Raw win probability | Modeled EV | Stressed EV |
|---|---:|---:|---:|---:|
| Iowa State Cyclones at Iowa Hawkeyes | 42.568 / 40.5 | 55.36% | 6.29% | 1.56% |
| Sacramento State Hornets at Fresno State Bulldogs | 47.275 / 45.5 | 54.51% | 6.41% | 1.43% |

Both forecasts reconstruct exactly. Both use 5,610 training rows with latest availability of 2026-09-07 at 02:00 UTC, before the Monday 04:00 UTC cutoff. Neither target game appears in training. Exact decimal odds reproduce the displayed EV; the repaired model and distribution fingerprints match.

The low-total regression term contributes +2.0445 points to Iowa’s total +2.0676 point adjustment, and +1.2665 to Fresno’s +1.7752. The picks primarily express a learned low-total correction. The original opponent observations are included, but this decomposition does not support presenting the edge as independent matchup confirmation.

## Comparable probability band

Conditional over probabilities 54–58%; observed outcomes exclude pushes. The band was selected after inspecting the current picks and is a descriptive audit, not a fresh hypothesis test or a new betting policy. All years have already been used in development.

| Test season | Games | Predicted over | Observed over | Over / under outcomes |
|---|---:|---:|---:|---:|
| 2021 | 128 | 54.98% | 50.00% | 64 / 64 |
| 2022 | 86 | 55.36% | 55.81% | 48 / 38 |
| 2023 | 31 | 55.13% | 41.94% | 13 / 18 |
| 2024 | 328 | 55.50% | 56.71% | 186 / 142 |
| 2025 | 354 | 55.58% | 51.13% | 181 / 173 |

Pooled: 927 games, predicted 55.43%, observed 53.07%. For the exploratory 2025 subset with market total ≤46: 80 games, predicted 56.35%, observed 41.25%. This narrower illustration is not independent validation and does not justify a newly selected subgroup rule.

## Interpretation

The one-point adverse mean shift plus sigma/tail variation does not capture uncertainty in estimated calibration or structural mean bias. Positive stressed EV must not be called a statistical lower confidence bound. A fresh provider response is evidence of price observation, not evidence that a sportsbook would accept the stake; the market-change timestamps remain separately visible.

Suggested board note: These are paper experiments using raw model probabilities. Both current over selections rely strongly on a low-total correction; comparable 2025 forecasts were overconfident. Stressed EV is a sensitivity estimate, not a confidence bound. No profitable edge has been established.
