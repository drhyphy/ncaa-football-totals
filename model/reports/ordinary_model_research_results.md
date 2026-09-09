# Ordinary-stat ridge and tree comparison

Fixed models, prior-only game features and annual expanding training on repaired historical reference totals. All outcomes are reused development data.

Selection on 2021–2024 MSE chose **opponent_adjusted_ridge**. Every configuration is retained in the 2025 check.

| Period | Configuration | Games | MSE | RMSE | MAE |
|---|---|---:|---:|---:|---:|
| 2021–2024 selection | market_only | 3061 | 251.4935 | 15.8585 | 12.5162 |
| 2021–2024 selection | opponent_adjusted_ridge | 3061 | 250.4281 | 15.8249 | 12.4990 |
| 2021–2024 selection | ordinary_ridge | 3061 | 255.8828 | 15.9963 | 12.6652 |
| 2021–2024 selection | ordinary_hgb | 3061 | 259.8281 | 16.1192 | 12.7845 |
| 2025 reused check | market_only | 852 | 239.8744 | 15.4879 | 12.4754 |
| 2025 reused check | opponent_adjusted_ridge | 852 | 240.9862 | 15.5237 | 12.5615 |
| 2025 reused check | ordinary_ridge | 852 | 242.0733 | 15.5587 | 12.5691 |
| 2025 reused check | ordinary_hgb | 852 | 246.9417 | 15.7144 | 12.7018 |

Negative paired differences favor the new model. MSE differences are in squared points; the intervals measure forecast-loss differences, not betting returns.

| Period | New model | Reference | MSE difference | Descriptive 95% interval | Four-comparison 98.75% interval |
|---|---|---|---:|---|---|
| 2021–2024 selection | ordinary_ridge | market_only | +4.3893 | +0.9617 to +7.8957 | +0.0670 to +9.0598 |
| 2021–2024 selection | ordinary_ridge | opponent_adjusted_ridge | +5.4547 | +2.7003 to +8.2434 | +1.9438 to +9.0841 |
| 2021–2024 selection | ordinary_hgb | market_only | +8.3346 | +3.9828 to +12.8502 | +2.6542 to +14.0183 |
| 2021–2024 selection | ordinary_hgb | opponent_adjusted_ridge | +9.4000 | +5.7704 to +13.1443 | +4.8581 to +14.0564 |
| 2025 reused check | ordinary_ridge | market_only | +2.1989 | -2.6457 to +7.5903 | -3.7823 to +9.2829 |
| 2025 reused check | ordinary_ridge | opponent_adjusted_ridge | +1.0871 | -1.1715 to +3.5020 | -1.6748 to +4.2060 |
| 2025 reused check | ordinary_hgb | market_only | +7.0673 | +0.8045 to +12.8000 | -1.2776 to +14.2522 |
| 2025 reused check | ordinary_hgb | opponent_adjusted_ridge | +5.9555 | +1.8091 to +9.7055 | +0.4775 to +10.6778 |

The JSON includes every annual/source breakdown, all four paired comparisons, descriptive 95% and four-comparison 98.75% MSE intervals, and fitting/coverage diagnostics.

- All 2020–2025 outcomes have already been reused elsewhere in this project; this is neither prospective performance nor a pristine holdout.
- Historical market sources establish pregame provider role, not exact morning quote timing, paired prices or accepted wagers.
- Game statistics use a kickoff-plus-six-hours availability proxy and retrospective source revisions; cutoff checks do not certify historical publication.
- Week intervals are descriptive. Four-comparison sensitivity does not account for the entire earlier search, shared-team cross-week dependence or regime changes.
- No probabilities, EV filter, hypothetical ROI, live candidate or bankroll allocation is produced. A lower point-prediction loss alone cannot establish profitable betting.
