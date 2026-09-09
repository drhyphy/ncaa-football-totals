> **Quarantined:** The historical scalar odds input includes live-game lines. These results are invalid as a closing-line profitability test. Retained for audit only.

# Conditional score distributions: bounded development study

**Quarantined historical market provenance.** A subsequent primary-source audit confirmed live-odds contamination in this archive. The original results below are retained for transparency; they are invalid as pregame/closing-line evidence. See `espn_market_timing_audit.md`.

Frozen on pre-2025 conditional log loss: **conditional_keymass**. No new profitable edge is established and no runtime policy changes.

## Design

Four prespecified families use expanding prior seasons to predict 2022, 2023 and 2024. Candidate selection uses those three folds only. The selected family is then refitted through 2024 and evaluated on 2025. Because these years were used elsewhere in model development, 2025 remains reused development validation, not a pristine holdout.

The conditional Normal uses heavily regularized residual mean and variance terms for market total, absolute spread, and 2023/2024 clock regimes. The key-mass family adds shrunk integer-score propensity ratios. The residual mixture adds a broad, heavily shrunk empirical residual kernel conditioned on total, spread and regime. All hyperparameters and the 3% hypothetical EV cutoff were fixed before this comparison; no threshold sweep was run.

## Comparison

| Period | Family | Games | O/U log loss | Brier | Score NLL | Hypothetical bets | ROI at assumed −110 |
|---|---|---:|---:|---:|---:|---:|---:|
| 2022–24 selection | constant_normal | 1953 | 0.69315 | 0.25000 | 4.18152 | 0 | — |
| 2022–24 selection | conditional_normal | 1953 | 0.69180 | 0.24933 | 4.17677 | 263 | 0.03076 |
| 2022–24 selection | conditional_keymass | 1953 | 0.69110 | 0.24898 | 4.14233 | 223 | 0.02731 |
| 2022–24 selection | conditional_residual | 1953 | 0.69151 | 0.24918 | 4.17815 | 157 | 0.10654 |
| 2025 reused | constant_normal | 946 | 0.69315 | 0.25000 | 4.15784 | 0 | — |
| 2025 reused | conditional_normal | 946 | 0.69595 | 0.25140 | 4.15779 | 474 | 0.00288 |
| 2025 reused | conditional_keymass | 946 | 0.69411 | 0.25047 | 4.09712 | 273 | -0.01399 |
| 2025 reused | conditional_residual | 946 | 0.69486 | 0.25085 | 4.15595 | 303 | -0.00450 |

## Uncertainty and push mass

Selection, frozen family: paired log-loss difference from constant Normal -0.00205; 95% whole-week bootstrap [-0.0046571400151342885, 0.0006665776063753568]; familywise three-comparison interval [-0.005273517370396295, 0.0012623012859814722]. Rounded-line expected pushes 49.4, observed 49 (counterfactual lines).

2025, frozen family: paired log-loss difference from constant Normal 0.00096; 95% whole-week bootstrap [-0.003575953147080131, 0.005517054493978559]; familywise three-comparison interval [-0.004606837736666244, 0.006548704349923814]. Rounded-line expected pushes 24.3, observed 28 (counterfactual lines).

## What changed in 2025

The selected family's average over probability was 52.68%, while the observed over rate was 49.26%. Its 273 hypothetical bets were all overs, illustrating the cost of carrying the earlier upward residual bias into 2025. The full-score distribution improved while over/under calibration failed to improve.

The pre-2025 low-total subgroup (45 or below) averaged roughly +2.9 points against the market; in 2025 it averaged roughly −0.7. Large-spread and high-total groups also moved materially. These retrospective patterns do not support a stable subgroup betting policy.

The JSON contains season-by-season results, calibration bins, fixed total/spread/regime/direction subgroup diagnostics, fitted coefficients, and prespecified sensitivity checks excluding 55.5 totals and restricting weeks 1–14. Subgroups are descriptive; no subgroup betting rule was selected.

## Limits

- All historical years have already informed broader model development; chronological refitting does not recreate a pristine holdout.
- Four fixed families; paired week-block intervals include a Bonferroni correction for three comparisons to baseline, but cannot correct unknown earlier searches.
- Only 371 real-source games precede 2023; folds can reflect source coverage changes rather than stable football effects.
- No integer posted totals in 2024/2025. Rounded-line push calibration is a counterfactual scoring diagnostic, never observed betting performance.
- Market totals have no morning publication timestamps, verified book-level consensus, or executable -110 prices. Diagnostic ROI is hypothetical.
- Score-mass and variance improvements may improve full-distribution likelihood without improving over/under decisions.
- Regime indicators learn residual associations after the market total, not a causal effect of clock rules.
- No live candidate is promoted and no forecast ledger or runtime policy is changed.
- The fixed valid-total range 15–100 excludes one real-source row: 2024 SMU–TCU game401635557 with archived total107.5 and final108. Its market timestamp/semantics require audit.

The [NCAA 2023 timing rule](https://www.ncaa.org/media-center-football-timing-rules-approved-for-divisions-i-ii/) changed first-down clock handling; the [2024 rule](https://www.ncaa.org/media-center-technology-rules-approved-in-football/) introduced the two-minute timeout. The model tests residual regime associations after controlling for the market total.

Reproduce from `model/`: `python -m ncaaf_model.conditional_distribution`.
