# Independent direct-probability research audit

Audited September 9, 2026. **The frozen experiment reproduces without a material discrepancy. It does not establish profitable betting predictions, and no new candidate should replace an active policy on this evidence.** The pre-2025 rule selected the existing `rawridge` benchmark; all four new classifiers have worse 2025 log-loss and Brier point estimates than the price-free 50/50 reference.

This audit independently reconstructed the source join, refitted all twenty new classifiers and five original ridge folds, and recalculated scores and uncertainty. It did not import the direct-probability utilities or study module, call their selection/check functions, change their outputs, or inspect any 2026 outcomes. Detailed checks and numerical differences are retained in [the audit JSON](direct_probability_research_audit.json). The fixed specification is [DIRECT_PROBABILITY_RESEARCH_PLAN.md](DIRECT_PROBABILITY_RESEARCH_PLAN.md).

## Source and stage verification

All 23 source-file hashes, seven implementation hashes and three validation/dependency hashes match the final plan. Both prediction CSV hashes match their report records. The plan SHA256 is `ef36cdea636dd87661141560af6f3934bf883730aab6d354370c218f26b5778b`; the written specification SHA256 is `4de223ef4fe3b22692075e78fd61f9bfb62fa85e40cb24eb6fc64bed0c28e5d0`.

Git commit `586423cb0739e6eeea1208c940649331dbdd5518` contains the frozen specification, implementations, tests and dependency specification. It precedes selection commit `33e1c5db738800c5a2c443f8014395a83565f940`, which contains the exact immutable earlier-year choice. The selection file is absent at the initial freeze; the 2025 result is absent at the selection commit. Recorded execution timestamps follow that order, and both phases identify the matching committed source bytes. The 2025 check references the exact selection-file digest. These are verified repository and execution records, not certification by an external timestamp authority.

An independent source-priority join reproduced all 5,008 repaired games: verified ESPN provider rows take priority, then validated CFBD rows fill unmatched identities. Team/game IDs, dates, final scores, neutral status, reference totals, spreads and source labels agree with the cache. All cached feature cutoffs equal the independently calculated Monday midnight Eastern before the matchup and precede kickoff. Each adjusted score/drive/clock difference equals its cached projection minus the repaired line. This confirms consistency with the previously audited feature reconstruction; it does not create original historical publication receipts or remove later source-revision uncertainty.

All 3,258 evaluated game identities and binary Under labels match the frozen feature rows. There are no duplicate evaluated games, no 2026 rows, and no 2025 rows in selection. Saved ridge identities, venue context, spread and cutoff/kickoff timestamps also agree.

## Independent fit and score reconstruction

The four new models used the identical eligible half-point training rows within each fold:

| Test season | Prior training games | Test games |
| --- | ---: | ---: |
| 2021 | 323 | 428 |
| 2022 | 751 | 403 |
| 2023 | 1,154 | 777 |
| 2024 | 1,931 | 798 |
| 2025 | 2,729 | 852 |

For each fold, every training season precedes the test season, train/test identities are disjoint, both labels are present, and the recorded transforms and fitting counts agree. The audit implemented the penalized logistic objective and gradient directly in NumPy/SciPy, recomputed training-only means and population standard deviations, and independently fitted the fixed histogram classifiers with one computational thread. All twenty fitted predictions reproduce with maximum absolute differences of **1.11 × 10⁻¹⁶** in logits and probabilities. Recorded logistic coefficients, transforms, objective values and gradient norms also reproduce; all ten histogram fits complete exactly 220 iterations.

The five original ridge fits were independently solved using their original prior-season all-line training rule. Mean differences from the saved benchmark are at most **7.11 × 10⁻¹⁵ points**. Independently discretizing its saved Normal distribution and summing strictly below each actual half-point line reproduces its Under logits within **8.89 × 10⁻¹⁶**. The direction is Under, not Over, and half-point outcomes cannot push. The existing PMF's numerical bin floor and overflow convention were retained exactly; no score-loss probability floor was introduced.

Recalculating pooled, annual and source-specific log loss, Brier loss, raw-probability means and all fixed reliability bins produced 3,250 matching scalar checks; the largest difference is **1.11 × 10⁻¹⁶**. The selection-period and 2025 source summaries remain separate. Every stored logit was scored as `logaddexp(0,z) - y*z`, without probability clipping.

All eight reported paired comparisons were reconstructed using the frozen PCG64 seed, 10,000 resamples, Eastern calendar weeks and the ratio of resampled loss sums to resampled game counts. Selection uses 73 observed weeks; 2025 uses 22. The maximum interval-endpoint discrepancy is **5.21 × 10⁻¹⁸**. The same sampled week identities are used across comparisons within each period; unequal week sizes retain the game-weighted estimand.

## Reproduced result

Lower loss is better. The predeclared tie order and pooled 2021–2024 log loss select `rawridge`; no 2025 comparison changes that choice.

| Configuration | 2021–2024 log loss, 2,406 games | 2025 log loss, 852 games | 2025 Brier |
| --- | ---: | ---: | ---: |
| raw50 | 0.693147 | 0.693147 | 0.250000 |
| rawridge | **0.691899** | 0.697509 | 0.252163 |
| context_logit | 0.692463 | 0.695176 | 0.251007 |
| opponent_logit | 0.694645 | 0.694379 | 0.250610 |
| context_hgb | 0.703267 | 0.698217 | 0.252477 |
| opponent_hgb | 0.713164 | 0.696273 | 0.251467 |

Adding opponent features worsens both classifier families' selection-period point estimates. In 2025 it improves their point estimates relative to their context-only counterparts, but both predeclared local 98.75% intervals cross zero. The new opponent logistic model improves on the weaker 2025 ridge benchmark by −0.003131 log loss: its descriptive 95% interval is [−0.005643, −0.000655], while the local four-comparison 98.75% interval is **[−0.006305, +0.000034]**. This does not establish incremental information beyond market context or a profitable edge. All four new models remain worse than `raw50` in the 2025 log-loss and Brier point estimates; this statement is not a claim that each difference is statistically distinguishable from zero.

## Scope and interpretation

The shared half-point cohort contains 3,581 games including warm-up. Of the full 5,008 games, 590 fail the inherited five-prior-game rule and a further 837 have non-half-point lines. Those latter exclusions are concentrated in early sources: 822 are CFBD consensus, 10 CFBD Bovada, and five other ESPN providers. The early 2021/2022 test samples fall from 734 history-qualified games each to 428/403; 2024 and 2025 lose none to this half-point filter. Source and era changes therefore remain entangled with coverage and should not be repackaged as a discovered phase-of-season or clock-rule effect.

The existing ridge benchmark trained on more prior-year observations, including integer lines: 505 versus 323 before the 2021 test, and 3,566 versus 2,729 before the 2025 test. Its sigma used the original broader prior-season residual sample as specified. Comparisons against ridge consequently change both method/target and training cohort; the within-family opponent-versus-context comparisons hold training observations fixed.

All historical years were reused in earlier research, including 2025. A committed selection does not turn them into a new holdout, and the local four-comparison intervals do not account for the project's full search history or eliminate shared-team/cross-week dependence. The 50/50 reference is a statistical null, not a verified historic no-vig price. Actual paired historical offered odds and original executable morning quote receipts are unavailable, so no historical EV or selected-bet ROI was calculated. A single-line classifier also supplies neither an alternate-line score distribution nor calibrated integer push probabilities.

Preserve these unsuccessful configurations and the existing prospective records. This study supplies no basis for a new live policy, confident staking, or claiming the profitability goal is achieved.
