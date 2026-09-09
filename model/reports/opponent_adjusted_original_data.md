> **Quarantined:** The historical scalar odds input includes live-game lines. These results are invalid as a closing-line profitability test. Retained for audit only.

# Opponent-adjusted development experiment

All ratings train on prior completed game observations, including games without historical odds. No EPA or publisher ratings. Two fixed specifications; no tuning on the 2025 results. The whole 2019–2025 corpus has already been reused in this project.

| Candidate | Games | MAE | MAE change vs market | Bets at assumed -110 | ROI | 95% week bootstrap |
|---|---:|---:|---:|---:|---:|---|
| market_only | 2442 | 12.550 | +0.000 | 0 | — | None |
| opponent_adjusted_ridge | 2442 | 12.542 | -0.008 | 868 | 3.59% | [-0.03191983980495431, 0.09337430618767661] |
| opponent_adjusted_structural | 2442 | 12.538 | -0.012 | 473 | 2.11% | [-0.07391161403777048, 0.10714701070281006] |

Ratings use a shared league intercept, offensive team effect and opposing defense effect, partial pooling, recency decay and offseason decay. Separate regressions estimate scoring, points per drive, possession count/duration, pass/rush yards and passing share. A strongly regularized residual model combines these signals with the line, spread and known clock-rule eras.

The fixed signal rule is modeled EV ≥3% at assumed -110 and at least five prior games. It does not impose a point threshold that a shrunk model cannot reach. These are research signals, not established profitable bets.
