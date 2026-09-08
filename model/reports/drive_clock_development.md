# Drive/clock development study

Two fixed challengers; expanding prior-year fits; reused historical development periods. Neither is approved for live betting.

| Candidate | Games | MAE | MAE difference vs market | Bets | Assumed -110 ROI | Week bootstrap ROI interval |
|---|---:|---:|---:|---:|---:|---|
| drive_clock_ridge | 2597 | 12.495 | -0.022 | 0 | — | — |
| drive_clock_shrink | 2597 | 12.493 | -0.024 | 0 | — | — |
| market_only | 2597 | 12.517 | +0.000 | 0 | — | — |

The shared game sample requires five prior drive-covered games per team. Positive MAE difference means worse forecasting than the market.

Historical prices are resolved closing totals with assumed -110 odds. These results cannot reconstruct a morning line-shopping strategy.

Features use earlier completed-game drive counts, offensive TD/FG/turnover rates, clock per drive/play and separate other points. Fixed shrink: 25% of clock-model discrepancy, clipped to four points. Ridge: 13 standardized features, alpha 500, intercept also regularized, adjustment capped at eight points.

The fixed diagnostic gate is six points and 3% modeled EV. The shrink candidate is consequently a forecast-only comparator: its four-point maximum cannot qualify. The gate was not reduced after seeing these results.

- 2019–2025 already informed earlier model development; these are not untouched tests.
- Only 18 pre-2023 games have real totals and five prior drive-covered games for both teams; 2023 ridge stays at market, with fixed 16-point prior sigma.
- No archived publication timestamps: six-hour game-availability proxy and retrospectively corrected public data.
- Clock pace is game-state dependent; no snap-level neutral-score filter in this bounded candidate.
- Offensive TD values approximate XP as seven; separate other-points form absorbs XP, return points and overtime.
- Fixed weak priors and no field-position feature; sparse histories revert to market.
- Week bootstrap intervals do not establish future profitability or adjust for earlier candidate searches.

Source: https://cfbfastr.sportsdataverse.org/ and the existing hashed SportsDataverse raw files.
