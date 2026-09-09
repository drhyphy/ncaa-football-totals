# Historical market-source sensitivity

The same two fixed opponent models were trained on prior seasons of the repaired primary market archive. Test games were repriced with independently published CFBD-derived lines. No model parameters, feature definitions, gates, or thresholds were chosen from these results. The derivative CSV lacks provider identities and timestamps; every ROI below assumes -110 rather than recorded total-side prices. These are reused development diagnostics.

Both model specifications have positive point-estimate returns under both line sources on shared games, but every 95% week-bootstrap ROI interval includes zero. Mean absolute-error improvements are small or absent. This supports continued prospective evaluation, not a high-confidence profitability claim.

| Cohort/source | Candidate | Games | MAE change vs own market | Bets | Assumed ROI | 95% week bootstrap ROI |
|---|---|---:|---:|---:|---:|---|
| cfbd_derivative_all | market_only | 1552 | +0.000 | 0 | — | — |
| cfbd_derivative_all | opponent_adjusted_ridge | 1552 | +0.032 | 352 | 9.58% | −1.12% to +19.56% |
| cfbd_derivative_all | opponent_adjusted_structural | 1552 | -0.010 | 52 | 13.81% | −16.00% to +37.32% |
| cfbd_derivative_shared | market_only | 1523 | +0.000 | 0 | — | — |
| cfbd_derivative_shared | opponent_adjusted_ridge | 1523 | +0.023 | 351 | 9.35% | −1.52% to +19.38% |
| cfbd_derivative_shared | opponent_adjusted_structural | 1523 | -0.014 | 51 | 12.30% | −17.45% to +36.36% |
| verified_provider_shared | market_only | 1523 | +0.000 | 0 | — | — |
| verified_provider_shared | opponent_adjusted_ridge | 1523 | -0.002 | 349 | 7.22% | −2.94% to +16.88% |
| verified_provider_shared | opponent_adjusted_structural | 1523 | -0.032 | 57 | 23.92% | −4.55% to +46.37% |

`shared` rows compare exactly the same games; the all-derivative cohort also includes games excluded by primary-provider availability. Do not compare all-derivative and primary-shared as if composition were identical.

The underlying source chooses CFBD lines[0] and discards provider identity. It drops games without season-end advanced statistics or spread, so coverage selection is imperfect. Its annual advanced statistics have lookahead and were excluded entirely. Neither source reproduces a 06:30 ET available quote or offered payout. A difference in line alone can change both model features and which wagers cross the fixed gate.

All 2019–2025 periods have been reused in research; none is an untouched prospective test. Individual bootstrap intervals do not correct for all research selection.
