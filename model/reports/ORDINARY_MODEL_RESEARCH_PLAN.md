# Fixed richer-statistics ridge and tree development comparison

Written September 9, 2026 before fitting these new configurations. No 2026 outcome is used. The purpose is to test whether a modest set of ordinary prior-game forms and nonlinear interactions improves total-score predictions beyond the repaired market reference and existing opponent-adjusted ridge. This is new development on outcomes already used elsewhere; no season is an untouched holdout. The active models and four-policy forward evaluation remain unchanged.

## Inputs and exact predictor contract

Start from all 5,008 repaired 2020–2025 market games in `data/normalized/opponent_features_verified_cf764f803b38ecfd.parquet` and the matching `data/models/opponent_history.parquet`. Reconcile game/team identities, final status, kickoff and neutral context against the official schedule archives. Require exact reciprocal historical team rows; duplicates or identity failures stop the build rather than choosing a row. Do not use old scalar odds, old public-ensemble features, EPA, FPI, publisher ratings, injury guesses or undated recruiting inputs.

Use exactly 58 numeric predictors, in the order exported by `ordinary_features_research.FEATURES`:

- 32 ordinary forms: home/away team × own/opponent observation × the eight fields `points_for`, `ppd`, `drives`, `seconds_per_drive`, `other_points`, `yards_per_pass`, `yards_per_rush`, `passes_rate`. The opponent observation is the reciprocal team's row from each prior game, never the current game's statistics.
- 10 context fields: repaired total, absolute home spread, their product, home and away implied points `(total−home_spread)/2` and `(total+home_spread)/2`, week, neutral-site indicator, 2023 clock-rule and 2024 two-minute-rule indicators, and a missing-spread indicator. Missing spread handling must remain explicit. All market-dependent fields are rebuilt from the repaired row.
- 10 history fields: for each team, days since the latest prior kickoff capped at 60, prior-game count, current-season prior-game count, sum of effective game weights, and count of prior games with complete drive metrics.
- Six existing verified opponent-adjusted predictors: score, drive and clock projections minus the repaired market total, plus adjusted passing efficiency, rushing efficiency and passing share. Recompute the first three differences from the verified projection totals and current repaired total. Never copy market-dependent values from the quarantined feature archive.

The feature builder retains all 5,008 games and reports availability and source hashes. Outcome columns can be carried for scoring, but only the explicit predictor list enters either new estimator. No target, final score, game ID, season ID, status, source label or arbitrary numeric column enters that list.

## Identical historical and future feature timing

Use the existing shared `weekly_cutoff(kickoff, as_of)`: Monday midnight Eastern before the game, capped by actual query time. Admit completed historical observations only when `available_at = start_date + 6 hours` is strictly before the cutoff, and inside its trailing 3×366-day window. This is an availability proxy, not a certified original publication receipt; revised source files remain a limitation.

Within each team's eligible history, rank games newest first with rank zero for the latest. Fixed weight is `0.92**rank × 0.65**max(0, query_season−historical_season)`. For each finite metric, use the weighted mean shrunk by four effective observations toward the unweighted finite-value league mean from the identical prior-only trailing window. Missing metric observations do not contribute to that metric's numerator or denominator. With no league history, use the existing physical defaults, not future values. Rest and counts use only admitted games. The feature query must depend on its cutoff and game context, not other target games in the query batch.

Retain source/cutoff/feature hashes in a frozen plan before the first new-model fit. Targeted tests cover future-outcome mutation, shared-cutoff query equivalence, duplicate and invalid-status rejection, reciprocal-team identity, explicit predictor exclusion and repaired market recomputation.

## Four fixed configurations and annual chronology

Compare `market_only`, the existing `opponent_adjusted_ridge`, `ordinary_ridge`, and `ordinary_hgb` on the same game rows. The existing configurations use saved expanding-season forecasts from `reports/opponent_adjusted_predictions.csv`; independently reproduce their earlier-season fits before accepting those saved values.

Use 2020 as initial training and evaluate 2021–2025 separately. Each test season trains only on seasons strictly earlier than itself. Apply the same minimum of five prior games for both teams to training and test rows, as encoded by the verified `adjusted_history_games` field; require at least 300 eligible training rows. The common test counts must remain 734, 734, 795, 798 and 852. Source integrity errors stop the experiment; missing ordinary metrics are represented through the fixed imputation scheme rather than favorable sample exclusions.

Both new configurations regress `actual_total − market_total` on the same 58 features. Add the fitted residual to the reference total, clipping only the residual to ±10 points, as in the existing main model. Preprocessing is fitted within each training fold. Use median imputation with missing indicators and preserve entirely missing training columns as zero rather than deriving any value from the test set.

- `ordinary_ridge`: train-fitted standardization and `Ridge(alpha=140, solver="svd")`, with its ordinary fitted intercept.
- `ordinary_hgb`: `HistGradientBoostingRegressor(loss="squared_error", learning_rate=.025, max_iter=220, max_leaf_nodes=10, min_samples_leaf=45, l2_regularization=20, random_state=2026, early_stopping=False)`. No scaling is needed. Disable automatic early stopping to avoid an implicit random validation split.

These regularization settings are inherited from the earlier proposed bounded factory; they are not selected by the new outcomes. Record installed library versions, fit counts, missingness, entirely missing columns and clipping. Fix numerical worker count to one for reproducibility. A fit failure is a reported experiment failure, not permission to switch models after examining test performance. [Official estimator documentation](https://scikit-learn.org/1.6/modules/generated/sklearn.ensemble.HistGradientBoostingRegressor.html), [preprocessing and leakage guidance](https://scikit-learn.org/stable/common_pitfalls.html).

## Metrics, selection and uncertainty

Primary metric is mean squared total-score error, because the estimators predict conditional means. Report RMSE, MAE and mean signed error as secondary diagnostics. Select the lowest game-weighted MSE across 2021–2024 from all four configurations, with ties resolved in the listed configuration order. Determine that choice before calculating its 2025 summary. Retain and disclose all four 2025 results, even if another configuration does better there. No parameter, feature family, cap or bet threshold is selected from 2025.

Predeclare four comparisons: each new model versus the market reference, and each new model versus the existing ridge. Form per-game squared-error differences, preserving exact paired games and total lines. Resample whole Monday–Sunday Eastern kickoff-calendar weeks 10,000 times with seed 20260909, using the same sampled weeks for both models. Report descriptive 95% MSE intervals and a four-comparison sensitivity at 98.75%; also report descriptive 95% MAE differences. Smaller differences favor the new model. Show every annual and market-source group, without selecting favorable sources; groups with fewer than two weeks have no bootstrap interval.

The four-comparison sensitivity does not correct the project's unknown wider historical search. Shared teams across weeks, revisions, source changes and clock-rule eras remain outside a simple week bootstrap. These results can reject an unhelpful model or motivate a separately versioned future experiment. They cannot establish an executable price advantage. No probability distribution, historical ROI, EV threshold search, Kelly stake, active candidate replacement or live promotion is produced by this point-prediction experiment.

Run `python -m ncaaf_model.ordinary_model_research --root model --freeze` from the repository root with `PYTHONPATH=model`, then commit the frozen specification and hashes before running `--evaluate`. The evaluation refuses modified sources, numerical versions or existing results. Frozen baseline verification fits are permitted during `--freeze`; `new_model_fits_run=false` refers specifically to the two new configurations.
