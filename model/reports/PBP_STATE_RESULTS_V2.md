# PBP state-rating experiment, version 2

The two new play-by-play (PBP) ratings did **not establish an improvement**. The fixed 2021–2024 selection kept the existing opponent-adjusted ridge. In the reused 2025 check, adding PBP ratings reduced MSE by only **0.0436** against that ridge, with a descriptive 95% paired-week interval of **−0.5416 to +0.4150**. Its MSE remained **1.0682 higher than the market reference**. Neither comparison establishes an advantage. No probabilities, historical EV or ROI were evaluated, and no live model or paper policy was promoted.

The [independent results audit](PBP_STATE_RESULTS_AUDIT_V2.md) reproduced all 10 annual residual ridge fits and 1,143 numeric checks, with zero mismatches (maximum absolute difference 2.84 × 10⁻¹³). This confirms the downstream calculations; it does not refit the upstream sparse state ratings or establish betting profitability.

## All fixed configurations

All three configurations use the same eligible games. Lower error is better: MSE is in squared total points; RMSE and MAE are in total points. Selection used pooled 2021–2024 MSE before the 2025 forecasts were computed.

| Period | Configuration | Games | MSE | RMSE | MAE |
|---|---|---:|---:|---:|---:|
| 2021–2024 selection | Market reference | 3,061 | 251.4935 | 15.8585 | 12.5162 |
| 2021–2024 selection | Existing opponent-adjusted ridge | 3,061 | 250.4281 | 15.8249 | 12.4990 |
| 2021–2024 selection | Ridge + PBP state ratings | 3,061 | 250.8194 | 15.8373 | 12.5051 |
| 2025 reused check | Market reference | 852 | 239.8744 | 15.4879 | 12.4754 |
| 2025 reused check | Existing opponent-adjusted ridge | 852 | 240.9862 | 15.5237 | 12.5615 |
| 2025 reused check | Ridge + PBP state ratings | 852 | 240.9426 | 15.5223 | 12.5600 |

The 2025 point estimate cannot replace the earlier selection after the fact. All historical years, including 2025, have been reused in this project; the ordered stages do not create an untouched holdout.

## Paired uncertainty

Each difference is **PBP ridge minus its reference**; negative values favor the PBP model. The fixed procedure resamples complete Eastern calendar weeks, using 10,000 paired ratio-bootstrap draws (PCG64 seed 20260909): 74 week blocks in selection and 22 in 2025. Both interval levels are descriptive, without a correction for the full earlier research search or all shared-team dependence.

| Period | Reference | MSE difference | 95% interval | 99% interval |
|---|---|---:|---|---|
| 2021–2024 selection | Market reference | -0.6741 | -2.4007 to +1.1353 | -2.9099 to +1.6980 |
| 2021–2024 selection | Existing opponent-adjusted ridge | +0.3912 | -0.1879 to +0.9934 | -0.3549 to +1.2223 |
| 2025 reused check | Market reference | +1.0682 | -2.8261 to +5.5299 | -4.2136 to +6.7791 |
| 2025 reused check | Existing opponent-adjusted ridge | -0.0436 | -0.5416 to +0.4150 | -0.7068 to +0.5597 |

Every pooled 95% and 99% MSE interval includes zero. Full paired MAE differences and intervals are retained in the [machine-readable results](pbp_state_results_v2.json).

## Every evaluation year

| Year | Games | Market MSE | Existing ridge MSE | PBP ridge MSE |
|---|---:|---:|---:|---:|
| 2021 | 734 | 252.9455 | 254.3631 | 255.8329 |
| 2022 | 734 | 237.2735 | 238.8872 | 238.9207 |
| 2023 | 795 | 249.6770 | 247.7104 | 247.6701 |
| 2024 | 798 | 265.0470 | 260.1316 | 260.2897 |
| 2025 | 852 | 239.8744 | 240.9862 | 240.9426 |

Annual and source results are diagnostics, not a basis for selecting a favorable subgroup. The sources cover different eras, and some subgroups are very small. All source metrics and intervals remain in the results JSON.

| Verified market archive | 2021–2024 games | 2025 games |
|---|---:|---:|
| `cfbd_Bovada` | 15 | 0 |
| `cfbd_consensus` | 1,484 | 0 |
| `espn_nonlive_provider_100` | 0 | 47 |
| `espn_nonlive_provider_40` | 13 | 0 |
| `espn_nonlive_provider_52` | 1 | 0 |
| `espn_nonlive_provider_58` | 1,548 | 805 |

## What was tested

The new model adds two matchup predictors to the existing 11: a conditional game-clock rating in seconds and a conditional conversion rating in percentage points. Separate offense and opposing-defense effects adjust for fixed down, distance, field position, score margin, half-clock, period, pass/unknown-pass and rule-era terms. These are continuous conditional ratings; the conversion contribution is not a calibrated game-winning or Under probability.

At each Monday 00:00 Eastern cutoff, ratings use only prior rows whose recorded availability is strictly earlier, within a fixed three-year lookback. A cutoff can be capped by an earlier requested time. The response-specific team-game weights, recency decay and ridge penalty are frozen. Missing team history contributes zero rather than removing a target game. The final residual ridge uses the same training-only transforms, penalty and ±10-point residual cap as the existing model, with training restricted to earlier seasons.

The [feature audit](pbp_state_feature_audit_v2.json) records **762,297 retained states** and features for 5,008 repaired market games. Only the existing history requirement determines the common evaluation cohort; target-game PBP coverage is not an admission condition. The feature build matched all **956 archived 2025 final games**, compared with 511 under the original parser. These archive counts differ from the 852 eligible 2025 forecast comparisons.

## The v2 source-order correction

The [identity diagnostic](PBP_IDENTITY_DIAGNOSTIC.md) found tied sequence values in 414 of 956 archived 2025 games. Version 1 excluded entire affected games. Before any matchup forecasts or scores were computed, version 2 instead froze a conservative local-order rule: a tied row is a barrier, the following state cannot use its score, and clock endpoints cannot bridge a tie, administrative row or sequence/play-number gap. The original v1 cache and audit remain preserved. Broader coverage is a parsing improvement, not evidence of predictive improvement.

Historical archives can be revised. The recorded kickoff-plus-six-hours availability proxy does not establish when each original PBP record or market quote was publicly available. Current upstream processing code does not certify the exact code used to produce every historical archive. Recorded game-clock deltas are not verified snap-to-snap measurements, and conservative local ordering cannot prove perfect source chronology. No 2026 outcomes were read for this experiment.

## Reproducible records

- [Frozen v2 plan](PBP_STATE_RESEARCH_PLAN_V2.md) and [pinned machine plan](pbp_state_research_plan_v2.json).
- [Feature audit and coverage](pbp_state_feature_audit_v2.json); [selection recorded before 2025](pbp_state_selection_v2.json).
- [All scores, annual/source results and paired intervals](pbp_state_results_v2.json).
- [Independent results audit](PBP_STATE_RESULTS_AUDIT_V2.md) and [audit receipt with file hashes](PBP_STATE_RESULTS_AUDIT_V2.json).
- [Public archive acquisition audit](PBP_RAW_ACQUISITION_AUDIT.md) and [identity diagnostic](PBP_IDENTITY_DIAGNOSTIC.md).

The v2 implementation and plan were frozen at `afaaf193c18031c1c3a50115402acbd8ecf0f2cf`; the feature audit was committed at `7741e7548699adb00ea819f01835fa0563088bfc`, and the selection was committed at `96557bc2224cbdbe38dca0ac61f275de33931a7a` before the 2025 check. The machine plan SHA-256 is `f3e1ac7380fc787013a9f39229a08f4566f9f3e7ed31916889d0bfb996791d7b`.
