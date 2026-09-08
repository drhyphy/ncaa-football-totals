# Data and temporal audit — 2026-09-08

The source project was preserved. Corrections and refits are in this separate project. Machine-readable findings are in `reports/data_audit_v3.json`.

## Confirmed defects corrected

1. **Future FPI data.** The source `snapshot_is_contemporaneous` flag means computed during the season, not before the game. For example, week-1 2024 Indiana FPI was updated December 15 and previously joined to its September 6 game. The previous pipeline attached future FPI to 85 home rows in 2023, 78 in 2024 and 83 in 2025. Both timestamp and out-of-sequence gates now apply. The [upstream flag implementation](https://github.com/sportsdataverse/cfbfastR-cfb-data/blob/main/python/cfb_data_build/fpi.py) documents these separate semantics.
2. **Synthetic historical lines.** `odds_source=default` contains fallback 55.5 totals and ±2.5 spreads. Those rows are excluded even when a total looks realistic, because the spread may still be synthetic. Counts were 7, 20, 9, 6, 3, 10 and 12 for seasons 2019–2025. The [upstream release builder](https://github.com/sportsdataverse/cfbfastR-cfb-data/blob/main/R/espn_cfb_09_betting_creation.R) retains this provenance.
3. **Historical/live feature mismatch.** The old live state used static prior-season form and always set current-season games to zero. Live state now uses the same shifted, seasonal-blending transform as the historical feature builder and consumes current-season completed games.
4. **Unaudited roster availability.** Historical annual returning-production and team-talent tables have retrieval times in 2026, not verified preseason publication times. They remain archived but are excluded from scoring and refits. Prior-season derived ratings remain available.
5. **Uncertainty understated.** The old threshold score subtracted only 0.75 standard errors and its intervals independently resampled individual bets. Threshold gating now uses week-cluster uncertainty and adjustment across 30 choices. Every public candidate abstains after the audited refit.

## Current sample and limitations

There are 3,152 final games with identified-source totals across 2019–2025: 109, 30, 113, 119, 879, 956 and 946 respectively. Early-season archives are sparse and unrepresentative. The 2023–2025 comparison uses 2,781 games with totals and spreads. Portable history contains 11,926 completed team-game rows.

Historical odds have no execution price or original quote timestamp. Their label is a resolved historical line; this cannot prove availability at 06:30 or any earlier betting horizon. In particular, 2024 and 2025 total records are all half-points, so rounding and source resolution semantics merit continued auditing.

Further inspection found that the upstream `odds_source` labels **spread** provenance. A core-api row may retain a real spread and substitute 55.5 when the total is missing. The [upstream resolver](https://github.com/sportsdataverse/sportsdataverse-py/blob/main/sportsdataverse/cfb/cfb_pbp.py) does not publish a separate total-availability flag. Therefore even a retained identified-source row is not proof that its total was quoted. `reports/sensitivity_exclude_55_5.md` excludes all 166 such lines from the corrected 2023–2025 predictions without refitting or retuning; 2,615 games remain. True 55.5 quotes are also excluded. The conclusion is unchanged: fixed original-model ROI is negative, and every confidence-gated public policy abstains.

Weekly SportsDataverse ratings and summaries use previous-week outcomes but are reconstructed products. They do not establish actual historical publication availability. Their EPA transformations may have been trained retrospectively. The derived `fei_*` fields are not independent historical FEI forecasts. The [upstream ratings documentation](https://github.com/sportsdataverse/cfbfastR-cfb-data/blob/main/docs/models/cfb_ratings.md) describes their retrodictive nature.

## Findings for the replacement forward scanner

The old quote scanner allowed missing freshness timestamps and included stale quotes in its market center. Its no-vig price calculation did not affect that center. It selected a model's latest best quote as the close, compared prices at different lines, and could count repeated snapshots as independent wagers. The replacement runtime owns those paths; the old grading/scoring modules are retained only for compatibility and should not supply public performance claims.

The old paper ledger had 33 paper rows for only nine games. It is not imported as 33 independent prospective results. All prior historical test seasons have been inspected during development. They are retrospective evidence, not an untouched test set. The previously reported +2.89% HGB ROI is superseded by this audit.

## Regression checks

Tests verify that current-game outcomes cannot enter pregame form; live and historical form agree after seasonal blending; an in-progress archived game cannot enter an as-of state; future/missing FPI timestamps fail closed; an out-of-sequence FPI row is rejected even if its contemporaneous flag is true; and synthetic default odds never enter training rows.
