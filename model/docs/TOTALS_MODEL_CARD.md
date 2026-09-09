> **Superseded market evidence:** A subsequent primary-source audit found live-game lines in the historical scalar archive. Prior market-based metrics below are quarantined, including those previously called corrected. See [the timing audit](../reports/espn_market_timing_audit.md) and [v4 protocol](../../docs/PROSPECTIVE_PROTOCOL.md).

# NCAA football totals: audited model card

Version: `audited-v3`. Development freeze: 2026-09-08.

**No candidate has demonstrated a high-confidence profitable edge.** Models produce frozen forward research forecasts. Historical performance does not establish executable positive expected value.

The previous V2 report is superseded. It included future-dated ESPN FPI rows in early-season games and synthetic archive default odds. These defects were fixed before this refit.

## Corrected historical evidence

Expanding-season prediction tests cover 2,781 games in 2023–2025. The closing-market MAE is 12.4626; neutral market Brier is 0.2500. Historical prices are unavailable; betting calculations assume -110.

| Candidate | MAE | Brier | Confidence-qualified bets |
|---|---:|---:|---:|
| Public stack | 12.4978 | 0.2509 | 0 |
| Public full HGB | 12.7700 | 0.2578 | 0 |
| ESPN FPI family | 13.1819 | 0.2679 | 0 |
| Derived FEI/EPA family | 13.2782 | 0.2692 | 0 |
| Prior-season family | 13.4350 | 0.2725 | 0 |
| Weekly summary family | 13.4390 | 0.2747 | 0 |

The corrected stack comes closest to the market. Full HGB remains a separately recorded nonlinear challenger. The historical confidence gate abstains for every public family and the stack. Original structural and residual families have negative fixed-gate historical ROI.

## Inputs and temporal rules

- Scores, drives, EPA, pace and efficiency use completed prior games with shifted exponential form. Live states use the identical seasonal blending and refresh from current-season completed games. A six-hour completion lag is imposed for replay cutoffs.
- Historical lines require identified `core_odds_api` or `summary_pickcenter` provenance. Synthetic defaults are excluded.
- ESPN FPI requires a known publication timestamp earlier than both prediction as-of and kickoff, and no out-of-sequence flag. A season-level contemporaneous flag alone is insufficient.
- Weekly adjusted ratings and summary tables use through-week W−1. They are retrospective statistics built from prior outcomes, rather than verified archives of forecasts actually published that week. Underlying EPA transformations may have been fitted later.
- Annual returning production and team talent are excluded because preseason availability could not be verified. The prior/roster family name remains for artifact compatibility, but only prior-season ratings are used.
- The `fei_*` columns are SportsDataverse derived ratings, rather than independently sourced historical FEI publications.

## Validation and uncertainty

Each outer test season follows every training season. Stacking uses expanding inner-season predictions; its thresholds use a further temporal level. The selection gate requires at least 100 prior validation bets and eight weeks, with a one-sided 95% Bonferroni adjustment across 30 model/threshold choices and a week-cluster standard error. ROI intervals resample season-week blocks. These controls reduce selection noise but cannot make previously inspected data an untouched holdout.

All historical seasons have been inspected during development. Candidate comparisons, source fixes and redesigns are retrospective development. Forward predictions frozen before outcomes are the next valid test. No candidate is promoted by its best hindsight threshold.

## Forward evaluation contract

Keep every candidate projection and every reason for abstention. Count at most one precommitted decision per game and candidate. Use fresh executable quote timestamps, line-specific no-vig probabilities and a reference excluding the execution book. Report flat ROI and uncertainty, calibration, independent closing-line evidence, sample size and concentration.

An unavailable close is missing data. A morning quote cannot be relabeled a closing quote. Price CLV requires the same line or a closing fair probability evaluated at the entry line; decimal prices at different totals cannot be divided as a valid measure of value.

A positive estimate alone is insufficient for a high-confidence label: prospective evidence, independent week coverage, positive uncertainty-adjusted return and stable calibration must agree. No actual wager placement is part of this system.
