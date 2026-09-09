# Integer-score shape results

The shape correction improved **exact-score likelihood**, but did **not establish an improvement at the actual betting line**. Selection on 2,327 games from 2022–2024 kept `ridge_normal`. In 852 reused 2025 games, the shape candidate's primary log-loss difference from the market reference was only **−0.000052**, with a descriptive 95% paired-week interval of **−0.000877 to +0.000746**. No historical EV/ROI or alternate-line betting test was run. No probability artifact or live policy was promoted.

## All three configurations

Primary NLL scores Under, Push and Over at each game's actual reference total. Exact-score NLL scores the probability of the precise final total. Brier is conditional on no push; CRPS scores the full cumulative distribution. Lower is better. The Market Normal is a statistical reference centered on the archived total, not an observed no-vig bookmaker distribution.

| Period | Configuration | Primary NLL | Brier | Exact-score NLL | CRPS |
| --- | --- | ---: | ---: | ---: | ---: |
| 2022–24 | Market Normal | 0.712282 | 0.249994 | 4.181317 | 8.896476 |
| 2022–24 | Existing ridge Normal | 0.710465 | 0.249078 | 4.177973 | 8.872540 |
| 2022–24 | Market + score shape | 0.712411 | 0.250302 | 4.148801 | 8.898620 |
| 2025 | Market Normal | 0.693158 | 0.250005 | 4.159109 | 8.772466 |
| 2025 | Existing ridge Normal | 0.697509 | 0.252163 | 4.161654 | 8.815413 |
| 2025 | Market + score shape | 0.693106 | 0.249979 | 4.095692 | 8.767971 |

Versus Market Normal, exact-score NLL improved by **−0.032517** in selection (95% interval −0.042291 to −0.022784) and **−0.063417** in 2025 (−0.075656 to −0.050710). This secondary improvement does not establish a betting advantage: total bets depend on the *sum* of probabilities on each side of the line, not on recognizing individual score frequencies. Redistributing score mass can improve exact-score likelihood while leaving the relevant side probabilities unchanged or worse. The earlier selection remains fixed despite the later point estimates.

## Primary paired uncertainty

Differences are shape candidate minus reference; negative favors shape. All four primary 95% and 99% intervals include zero.

| Period | Reference | Primary NLL difference | Descriptive 95% interval | Descriptive 99% interval |
| --- | --- | ---: | --- | --- |
| 2022–24 | Market Normal | +0.000129 | -0.000865 to +0.001146 | -0.001175 to +0.001497 |
| 2022–24 | Existing ridge Normal | +0.001946 | -0.001327 to +0.005315 | -0.002269 to +0.006369 |
| 2025 | Market Normal | -0.000052 | -0.000877 to +0.000746 | -0.001108 to +0.000982 |
| 2025 | Existing ridge Normal | -0.004404 | -0.010844 to +0.001449 | -0.013029 to +0.003494 |

Intervals use 10,000 PCG64 draws, seed 20260909, resampling whole Eastern weeks: 59 selection weeks and 22 check weeks. Loss sums are divided by contributing game counts; conditional metrics omit realized pushes. These intervals condition on the fitted forecasts and omit training-estimation uncertainty, the broader historical search, and some recurring-team dependence. All years remain reused development data.

## Coverage and pushes

Both references and the new candidate use identical games. Prior 2021 forecasts provide 734 warm-up games. Shape-training counts are 734, 1,468, 2,263 and 3,061 for test years 2022–2025, always from earlier out-of-fold seasons.

| Year | Games | Integer lines | Actual pushes | Market NLL | Ridge NLL | Shape NLL |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2022 | 734 | 331 | 11 | 0.749215 | 0.749837 | 0.748642 |
| 2023 | 795 | 18 | 1 | 0.697667 | 0.696290 | 0.697612 |
| 2024 | 798 | 0 | 0 | 0.692871 | 0.688372 | 0.693830 |
| 2025 | 852 | 0 | 0 | 0.693158 | 0.697509 | 0.693106 |

Selection has 349 integer lines and 12 pushes; expected pushes are **8.50 market, 8.47 ridge and 8.78 shape**. Conditional loss/Brier therefore use 2,315 earlier games. There are no integer lines in 2024 or 2025, so those years cannot validate actual integer-line push probabilities. They still provide valid half-line probability comparisons; no counterfactual rounded lines were substituted. Different push prevalence also limits interpreting raw primary NLL changes across periods.

| Verified market archive | 2022–24 games | 2025 games |
| --- | ---: | ---: |
| `cfbd_Bovada` | 13 | 0 |
| `cfbd_consensus` | 752 | 0 |
| `espn_nonlive_provider_100` | 0 | 47 |
| `espn_nonlive_provider_40` | 13 | 0 |
| `espn_nonlive_provider_52` | 1 | 0 |
| `espn_nonlive_provider_58` | 1548 | 805 |

Source and era are entangled; very small subgroups do not justify a betting rule. Full annual/source losses, conditional log loss, reliability bins, push counts and all paired comparisons remain in the [results JSON](score_shape_results.json).

## Shape isolation, audit and preserved failure

The one new candidate learns global integer-score ratios with the fixed 800-game prior and 0.6–1.6 bounds. A two-parameter exponential tilt preserves each market PMF's discrete mean and variance. All 3,179 corrections converged in three or four Newton steps. Maximum standardized moment residual was **7.794×10⁻¹²**, below the frozen 10⁻¹¹ tolerance; maximum absolute mean and variance changes were **4.834×10⁻¹¹ points** and **1.997×10⁻⁹ squared points**. Mean-error differences from the market at floating-point precision are numerical noise, not predictive gains.

The [independent numerical audit](SCORE_SHAPE_NUMERICAL_AUDIT.md) **passed**, reconstructing four shape fits and 3,179 games per configuration with a different solver. It checked 1,042,299 values; maximum discrepancy was 1.997×10⁻⁹ in a variance value. This confirms computation, not an executable edge; ridge centers came from pinned saved forecasts rather than a new independent ridge refit.

The first selection attempt failed a bookkeeping check because the runner demanded a Git blob for an intentionally ignored, checksum-pinned prediction archive. It stopped before data loading or new shape fits/scores. The original plan, failed receipts and [source correction](SCORE_SHAPE_SOURCE_CORRECTION.md) remain preserved. Execution-plan revision 2 removed only that incorrect Git requirement, kept the exact input hash, and was committed at `2ae9cac`; selection was committed at `a7fa4fd` before the 2025 stage. The scientific model and cohort were unchanged.

See the [fixed specification](SCORE_SHAPE_RESEARCH_PLAN.md), [revised machine plan](score_shape_research_plan_v2.json), [immutable selection](score_shape_selection.json), and [audit receipt](score_shape_numerical_audit.json). Historical quote receipts and paired offered prices remain unavailable. Under the frozen plan, an exact-score-only improvement does not justify another distribution-family search or a betting-policy change.
