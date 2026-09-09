# Direct probability development experiment

Specification written September 9, 2026, before fitting any of these four new configurations. The companion `direct_probability_research_plan.json` pins the existing inputs and this document. The new implementation and its tests must also be hashed before execution. This is a separate development experiment: no 2026 outcomes, live forecasts, policy changes, historical bet selection, or new profitability claim.

## Question and scope

Can directly predicting `P(final_total < reference_total)` extract useful information from the existing opponent-adjusted statistics beyond the same market context alone? Prior experiments trained point forecasts by squared error, then converted their means and scales into probabilities. Direct classification uses a different target and loss: the size of a realized scoring margin does not itself determine its contribution to the classification target. This is a testable difference, not a prediction that classification will win.

The repaired opponent model already includes opponent-adjusted scoring, points per drive, possession count, clock duration and pass/rush efficiency. The ordinary 58-feature ridge/tree comparison already added broader prior-game forms and history/context. The six-configuration calibration study changed existing centers and dispersions, and explicitly deferred direct logistic prediction. Do not add those 58 predictors, publisher EPA/FPI, undated summaries, alternate key-score families, new phase thresholds, or a hyperparameter search here.

## Pinned inputs and common cohort

Use `data/normalized/opponent_features_verified_cf764f803b38ecfd.parquet`, SHA256 `ec33e9add2cb5b84742a17e65c313ca225503f6edadba125fbf7eeb5a45bb73c`, containing 5,008 repaired 2020–2025 games. Use the matching existing `reports/opponent_adjusted_predictions.csv` only for the raw ridge benchmark. The companion JSON records hashes of these files, the earlier source/feature/calibration manifests, the original opponent/calibration/distribution implementations, and the underlying repaired market sources. Do not call any loader that falls back to the compromised scalar odds archive.

For both training and evaluation, retain exactly the rows with `adjusted_history_games >= 5` and a finite positive reference total whose fractional part is exactly `0.5`. Integer totals and other fractions are excluded by this fixed contract before viewing model outcomes. Require unique canonical game IDs, seasons restricted to 2020–2025, verified provider-role flags, the same repaired source identities, finite values of all eleven admitted predictors, and prior `ratings_cutoff < game_date`. Require final integer totals on the inherited valid score support 0–250 when labels are loaded. Integrity or unexpected missing-predictor failures stop the study; do not silently discard different rows for different configurations.

The outcome-free cohort inventory is:

| Season | Full repaired cache | Common half-point cohort with five priors |
| --- | ---: | ---: |
| 2020 | 538 | 323 |
| 2021 | 848 | 428 |
| 2022 | 849 | 403 |
| 2023 | 910 | 777 |
| 2024 | 920 | 798 |
| 2025 | 943 | 852 |

There are 3,581 eligible rows, including 323 warm-up rows and 3,258 test rows; the 2021–2024 selection period has 2,406 rows. Revalidate these counts before execution. Report exclusions, half-point coverage and market sources by year. Early years lose substantially more rows to integer-line exclusion; this sample change is not evidence about a scoring-rule or market-regime effect.

Before a fit, verify that feature and saved-forecast joins preserve one game per row and match game/team identities, season, week, kickoff, repaired line/spread context, source/provider flags and ratings cutoff. Match the saved benchmark's actual total when labels are loaded. Recompute the three adjusted-minus-market columns from the cached adjusted totals and repaired line and assert equality. Check the six adjusted columns and five context columns against the exact current opponent-model allowlist. Compare underlying repaired market rows using the existing audited source-priority contract; do not choose a fresh provider or select a more favorable line.

The saved benchmark has 3,913 games across 2021–2025 before the half-point filter. Its original point-model fits used the larger eligible prior-season cohort, including integer lines; the four new classifiers all use the same smaller half-point training cohort. Thus comparisons with `rawridge` assess a deployed-method benchmark, not solely a loss change under identical training observations. The opponent-versus-context comparisons within each classifier family do hold training observations fixed. The saved fold means and sigmas were independently reconstructed in the frozen calibration study. Verify those pinned provenance records and exact alignment; any additional baseline reconstruction must use strictly prior seasons and must be labeled baseline verification, not a new candidate fit. Never substitute the current all-years fitted artifact. Prior statistics retain the historical Monday cutoff and kickoff-plus-six-hour availability proxy; this archive cannot prove historical publication receipts or morning quote availability.

## Four new configurations and two references

The exact feature blocks are:

```
context = [market_total, abs_spread, week,
           clock_rule_2023, two_minute_rule_2024]

opponent = [adjusted_score_minus_market, adjusted_drive_minus_market,
            adjusted_clock_minus_market, adjusted_pass_yards,
            adjusted_rush_yards, adjusted_pass_share,
            market_total, abs_spread, week,
            clock_rule_2023, two_minute_rule_2024]
```

The full block has eleven columns in total, including the five context columns exactly once. Do not duplicate context columns, because doing so changes effective regularization. `abs_spread` retains the existing zero value for missing original spreads; no new imputation is introduced. The era indicators and week are already existing predictors, not newly selected favorable periods.

Fit `context_logit`, `opponent_logit`, `context_hgb` and `opponent_hgb`. References are `raw50`, which always predicts Under probability 0.5, and `rawridge`, whose probability comes from that game's saved prior-season ridge mean and sigma using the same zero-truncated, integer-discretized Normal convention as the calibration study. In that convention the support is 0–250, the last bin contains overflow and the raw sigma is bounded to 6–30. Sum the PMF strictly below the actual half-point reference; there is no push probability. `raw50` is a statistical null, not an observed no-vig bookmaker probability.

For a logistic training matrix, compute each column's mean and population standard deviation using only the training rows. Replace an exactly zero standard deviation by 1. Transform training and future rows with those saved values, then clip each standardized feature to [−5,+5]. Add an unscaled intercept. For binary label `y = 1[actual_total < market_total]`, design row D and coefficient vector beta, fit:

```
eta_i = D_i beta
objective(beta) = mean(logaddexp(0, eta_i) - y_i * eta_i)
                  + (0.1 / 2) * sum(beta_j**2)
gradient(beta) = D' (expit(D beta) - y) / n + 0.1 * beta
```

Every coefficient, including the intercept, is penalized. The likelihood is a mean; do not reuse the earlier point-ridge summed-loss penalty of 200. Use zero initialization, L-BFGS-B with an analytic gradient, no coefficient bounds, `maxiter=1000`, `ftol=1e-12`, and `gtol=1e-8`. A successful fit requires the optimizer's success flag and finite parameters, objective and gradient, with gradient infinity norm at most `1e-5`. Record convergence diagnostics and fail explicitly if they are not met. Return finite raw logits and raw probabilities `expit(eta)`; do not clip either output for scoring.

For both histogram gradient classifiers, use finite raw allowlisted columns, no scaling or imputation, and `HistGradientBoostingClassifier(loss='log_loss', learning_rate=0.025, max_iter=220, max_leaf_nodes=10, min_samples_leaf=45, l2_regularization=20, random_state=2026, early_stopping=False)`. Keep the common defaults `max_depth=None`, `max_bins=255`, no class weights and no categorical features. Pin the package versions in the final execution manifest. Preserve the exact positive-class mapping to Under; no sample or class reweighting.

The operational fitting minimum is 100 distinct eligible prior-season games with both binary labels present. It is a numerical/data-coverage guard, not a claim of sufficient evidence for profitability. A failed or insufficient fold is unavailable, never replaced silently by `raw50`, a different penalty or a preceding artifact. Do not select a candidate using a reduced set of favorable completed folds; resolve an implementation problem without changing the frozen specification, or report an incomplete experiment.

## Chronology and selection

Use 2020 only as initial training. For each test season y in 2021–2025, fit on all eligible seasons strictly less than y. Each transformer, optimizer and tree sees only that training slice. Require disjoint game IDs and the recorded maximum training season below y. Test-feature outliers and future labels must not change earlier fitted transforms or predictions.

First execute the 2021–2024 folds and choose the configuration with the smallest game-weighted pooled binary log loss. Break exact ties in this fixed order: `raw50`, `rawridge`, `context_logit`, `opponent_logit`, `context_hgb`, `opponent_hgb`. Write and hash an immutable choice file identifying its input/specification/implementation fingerprints **before** calculating the 2025 diagnostic results. Then run/report all six 2025 configurations and identify the preselected one. Never substitute the best 2025 result. No 2026 outcomes are loaded at any stage.

All 2020–2025 outcomes have already been reused elsewhere in development. The staged choice file protects this experiment's decision rule but cannot make 2025 an untouched holdout or undo prior research selection.

## Scores, comparisons and uncertainty

Primary score is mean binary log loss at the actual common half-point line, calculated stably from each finite raw logit z as `logaddexp(0,z) - y*z`, with no probability or logit clipping. The two utility families expose public finite logits, `raw50` has z=0, and `rawridge` converts the PMF-derived probability p using `log(p) - log1p(-p)` after verifying `0 < p < 1`. Preserve raw logits and raw probabilities `expit(z)`; this avoids limiting the penalty for confidently incorrect forecasts with an arbitrary probability floor. Secondary score is mean Brier loss; Brier and descriptive calibration use raw probabilities. Do not use accuracy, ROI, a probability threshold or a favorable subgroup to select the winner.

The four primary paired comparisons are:

1. `opponent_logit - context_logit`.
2. `opponent_hgb - context_hgb`.
3. `opponent_logit - rawridge`.
4. `opponent_hgb - rawridge`.

Negative log-loss differences favor the first model. Report the paired point estimates and descriptive 95% intervals, plus 98.75% individual intervals as a local Bonferroni sensitivity for these four comparisons. Compute them separately for the pooled 2021–2024 selection period and the 2025 reused check. The local four-comparison correction does not cover historical model searches, selecting among six configurations, both periods, or every diagnostic; it is not a project-wide confidence guarantee.

Resample whole Monday–Sunday Eastern calendar weeks with replacement. For each paired difference, let P_w be the sum of game-level loss differences in week w and N_w its common game count. Each draw samples G complete weeks from the G observed weeks and returns `sum(P_w*) / sum(N_w*)`, preserving all paired games and candidates. Use `numpy.random.Generator(numpy.random.PCG64(20260909))`, 10,000 draws, sorted week identities, NumPy linear quantiles, endpoints `[0.025,0.975]` and `[0.00625,0.99375]`, and at least two distinct weeks. Reuse the same draw indices for every comparison within a period; restart the specified generator for each period. Unequal week sizes must not turn the estimand into an unweighted mean of weekly means.

Report all six configurations' metrics and coverage by year, by source, and by period-and-source. Report fixed reliability bins with edges `[0,0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9,1]`, intervals left-closed/right-open except the last includes 1, with counts, mean raw prediction and observed Under frequency; retain empty bins as empty. Include mean predicted probability, observed Under fraction and coefficient/convergence or tree-iteration diagnostics. Descriptive breakdowns cannot introduce additional selected rules. Cross-week shared teams, source revisions and source/regime changes limit a simple weekly bootstrap's interpretation.

## What a result would mean

The relevant incremental tests compare opponent features with equally trained context-only models. Beating `raw50` alone can reflect a broad historical Under imbalance or market-total context, rather than information in opponent statistics. Classification at this reference line does not define a coherent score distribution or establish calibrated push probabilities. Do not obtain an alternate-line CDF by merely changing the market total in its input.

Historical paired offered prices and their receipt timestamps are unknown. This experiment therefore computes no historical EV, stake allocation or hypothetical selected-bet ROI. Better proper scores could justify a separately versioned future paper policy using actual observed offers and immutable decisions; they do not establish profit, accepted execution, high-confidence staking or achievement of the user's goal. Preserve every negative result, the existing live policies and their separate forward evaluation. A prospective profit test, not a working pipeline or a favorable proper score alone, must answer the profitability goal.
