# Opponent-adjusted development experiment

The repaired-data ridge candidate returned +4.62% across 490 hypothetical bets, with a descriptive 95% week-bootstrap interval of -4.72% to +13.15%. In 2025, it returned **-4.55%** across 202 bets. These results do not establish a profitable betting edge.

The comparison uses documented CFBD fields and verified non-live ESPN bookmaker providers. Exact closing times, 06:30 ET availability, and offered total-side prices are unverified. Every return assumes −110. All 2019–2025 periods have been reused in research; none is an untouched test. Earlier results using the compromised scalar odds archive remain quarantined.

## Pooled results

| Candidate | Games | MAE | MAE change vs market | Bets | W–L–P | ROI at assumed −110 | Descriptive 95% ROI interval |
|---|---:|---:|---:|---:|---|---:|---|
| Market baseline | 3,913 | 12.507 | +0.000 | 0 | 0–0–0 | — | — |
| Opponent-adjusted ridge | 3,913 | 12.513 | +0.005 | 490 | 268–221–1 | +4.62% | -4.72% to +13.15% |
| Opponent-adjusted structural | 3,913 | 12.518 | +0.011 | 200 | 101–97–2 | -2.59% | -17.89% to +13.08% |

## Results by season

Each season fits only earlier seasons. Both candidates and all evaluated years appear below, including losing periods. A positive single-season result is not an independent validation after repeated research use.

| Season | Candidate | Games | Bets | W–L–P | ROI at assumed −110 | Descriptive 95% ROI interval | MAE change vs market |
|---|---|---:|---:|---|---:|---|---:|
| 2021 | Opponent-adjusted ridge | 734 | 41 | 23–18–0 | +7.10% | -17.56% to +34.52% | +0.024 |
| 2021 | Opponent-adjusted structural | 734 | 23 | 11–12–0 | -8.70% | -45.45% to +32.83% | +0.013 |
| 2022 | Opponent-adjusted ridge | 734 | 72 | 41–30–1 | +10.10% | -18.94% to +36.26% | +0.029 |
| 2022 | Opponent-adjusted structural | 734 | 48 | 25–21–2 | +3.60% | -28.68% to +32.63% | +0.098 |
| 2023 | Opponent-adjusted ridge | 795 | 15 | 3–12–0 | -61.82% | -100.00% to -18.18% | -0.035 |
| 2023 | Opponent-adjusted structural | 795 | 66 | 26–40–0 | -24.79% | -42.05% to -5.82% | +0.008 |
| 2024 | Opponent-adjusted ridge | 798 | 160 | 100–60–0 | +19.32% | +7.17% to +31.09% | -0.080 |
| 2024 | Opponent-adjusted structural | 798 | 42 | 28–14–0 | +27.27% | -13.23% to +56.82% | -0.085 |
| 2025 | Opponent-adjusted ridge | 852 | 202 | 101–101–0 | -4.55% | -20.76% to +6.61% | +0.086 |
| 2025 | Opponent-adjusted structural | 852 | 21 | 11–10–0 | +0.00% | -43.85% to +45.10% | +0.025 |

## Specification and limits

Ratings use a shared league intercept, offensive team and opposing-defense effects, partial pooling, recency decay, and offseason decay. Separate regressions estimate scoring, points per drive, possessions and duration, pass/rush yards, and passing share. The two existing specifications remain fixed: a strongly regularized residual model and a simple structural shrinkage model. No EPA, annual source-file features, or publisher power ratings enter these candidates.

Team observations must precede the weekly Monday cutoff; kickoff plus six hours is the recorded availability proxy. Each test season estimates residual uncertainty from earlier seasons. The fixed research rule requires modeled EV ≥3%, stressed EV ≥1% at assumed −110, and at least five prior games. Historical data cannot reproduce current quote-presence, execution, or bookmaker-account checks.

Week-bootstrap intervals are descriptive, do not correct for all previous model searches, and do not capture every data-quality or execution risk. Improved coverage and repaired market roles do not create an untouched holdout. Current forecasts remain experimental until prospective records support a stronger conclusion.

Specification: `opponent-adjusted-v3-pregame-provider`. Data fingerprint: `cf764f803b38ecfd`.

Generated from `opponent_adjusted_development.json` by `python scripts/publish_research.py --root .`. Re-run after changing research reports; the generator does not fit models or modify their artifacts.
