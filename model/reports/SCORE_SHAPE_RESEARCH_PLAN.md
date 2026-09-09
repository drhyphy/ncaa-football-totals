# Fixed integer-score shape experiment

Specification date: September 9, 2026. This experiment asks whether learned integer-score mass improves actual-line probabilities after holding the baseline distribution's discrete mean and variance fixed. It contains one new fitted candidate and two references. All historical years are reused development data; no result is an untouched or confirmatory profitability test.

The earlier repaired calibration study explicitly deferred integer-score reweighting. Direct classifiers subsequently tested binary probabilities, and the play-state experiment tested two additional mean predictors. Neither isolates the present question. The old conditional-distribution runner used quarantined scalar odds and mixed shape, location and variance changes; do not run or reuse its input loader, fitted artifacts, selection or returns. Only its already reviewed pure Normal PMF function is reused.

## Inputs and chronology

Use exactly the repaired `reports/opponent_adjusted_predictions.csv` (SHA-256 `1b032e26993c7c15f98f74c26ed69d6112520da8dbae442e41b20ea602b0b524`) and the verified 5,008-game feature cache `data/normalized/opponent_features_verified_cf764f803b38ecfd.parquet` (SHA-256 `ec33e9add2cb5b84742a17e65c313ca225503f6edadba125fbf7eeb5a45bb73c`). Retain the existing five-prior-game evaluation requirement and verified pregame-provider flags. Require identical game/line/outcome/date/source rows for the market and ridge references. Reconstruct their saved earlier-season means and sigmas before the new comparison. No current all-years artifact is a substitute for a saved annual forecast.

Saved 2021 forecasts provide warm-up: 734 games. For each evaluated year, train the new shape ratios on strictly earlier saved out-of-fold market forecasts and observed integer scores, starting in 2021. Test 2022 (734 games), 2023 (795), 2024 (798), then 2025 (852). The first three test years total 2,327 games. Never fit ratios using a target year's outcomes. Retain every eligible game for each configuration; there is no favorable line, outcome or score-subgroup selection. No 2026 inputs or outcomes are used.

Commit the implementation, tests, specification and machine hash plan before any new shape fitting or scoring. Calculate 2022–2024, preserve its forecasts and choice, and commit that choice before calculating new 2025 shape forecasts or scores. Existing published 2025 baseline results are already known; these ordered stages do not erase that prior knowledge. Preserve failed stages and outputs; do not overwrite them to hide a numerical or data problem.

## Exactly three configurations

Tie-breaking order is `market_normal`, `ridge_normal`, `market_score_shape`.

Both references use the original saved center and prior-season market-residual sigma, with the existing 6–30 sigma bound and the same integer Normal PMF on scores 0–250. Half-point bin boundaries are retained, negative-score mass is excluded through normalization, the last bin retains upper overflow, and the inherited numerical mass floor is 1e−15. The ridge reference is a practical comparator, not a separately fitted shape model. No alternative mean, sigma estimator, score support, tail family or probability floor is selected using performance.

The new candidate begins with each market reference PMF `p0_i(y)`. Over n strictly earlier out-of-fold games, let `O_y` be the observed integer-score count and `E_y = sum_i p0_i(y)` the expected count. Fix:

```
prior_y = 800 * E_y / n
r_y = clip((O_y + prior_y) / (E_y + prior_y), 0.6, 1.6)
```

The 800-game shrinkage and ratio bounds retain the previously specified constants; no grid is searched. Ratios are global across earlier games, without interactions, a handpicked list of key numbers, recency weighting or an outcome-selected subgroup. All exact totals are eligible, including scores with zero observed count. Public archived final scores include overtime; this experiment does not claim a regulation-only decomposition.

For a target game's baseline PMF, compute its actual discrete mean `m0` and standard deviation `s0`, then `z_y = (y-m0)/s0`. Define:

```
p(y) = p0(y) * r_y * exp(a*z_y + b*z_y**2) / Z(a,b)
choose (a,b) so E_p[z] = 0 and E_p[z**2] = 1
```

This exponential tilt preserves the original discrete mean and variance while changing higher-order shape. Use a stable log-sum-exp dual solve with analytic gradient and Hessian, damped Newton iterations and a maximum absolute standardized moment residual of 1e−11. Record iteration counts, tilt parameters and residuals. A failed solution raises an error; it is not silently replaced by a reference or another fitted family. Synthetic tests must cover identity ratios, known moment constraints, tail cases, invalid inputs and strict training/prediction chronology before real evaluation.

## Selection and proper scores

Primary metric is game-weighted mean **three-outcome negative log likelihood at the actual reference line**, with Under, Push and Over obtained by summing the same coherent PMF. Actual pushes count in this primary score. Noninteger lines have zero push mass. No rounded counterfactual line substitutes for an actual line, and half-point lines are not removed merely because they have no push outcome.

Report all configurations in all periods, plus annual and market-source summaries. Secondary metrics are conditional binary log loss and Brier loss excluding realized pushes, exact integer-score negative log likelihood, discrete CRPS and absolute error of the PMF mean. Also report actual integer-line counts, observed versus expected pushes, and fixed conditional-Over reliability bin edges `[0,.4,.45,.5,.55,.6,1]`, excluding realized pushes. Bins include their lower edge and exclude their upper edge, except the final bin includes 1. Better exact-score likelihood alone does not establish better betting decisions.

Select the smallest pooled 2022–2024 primary loss, using the fixed configuration order for ties. Commit this choice before the 2025 stage and report all three 2025 configurations. The later check cannot replace the earlier choice.

## Paired comparisons and limits

Primary contrast is `market_score_shape` minus `market_normal`; the practical contrast is `market_score_shape` minus `ridge_normal`. Negative loss differences favor the shape candidate. Match exact game identities, actual lines and outcomes. Use 10,000 PCG64 draws, seed 20260909, resampling complete Monday–Sunday Eastern calendar weeks. Calculate ratios of resampled loss sums to eligible game counts, including separate nonpush counts for conditional metrics. Weeks with no eligible rows for a metric are absent from that metric's resampling universe; reset the fixed random seed for each metric. Report descriptive 95% intervals for all losses and 99% sensitivity intervals for primary loss. Return no interval with fewer than two eligible weeks. These intervals condition on fitted annual forecasts and do not refit the model or quantify training-estimation uncertainty. Do not infer a global confidence level from them: they do not correct the full historical search or all recurring-team dependence. Preserving moments isolates predictive shape; it does not identify a causal football scoring mechanism.

The archive contains 331 integer lines in 2022, 18 in 2023, and none in 2024 or 2025. This limits later actual-push validation but does not prevent proper half-line evaluation. A market-centered Normal is a statistical reference; paired historical offered prices and exact historical publication times remain unavailable. No assumed-price ROI, historical EV selection, staking, active artifact replacement or forward-ledger change is part of this study.

If only full-score likelihood improves, preserve that finding without presenting it as a betting edge or expanding into another distribution-family search. Any improvement in actual-line scores would still need a separately frozen prospective assessment against observed prices and subsequent outcomes. Existing paper policies and their recorded histories remain unchanged.
