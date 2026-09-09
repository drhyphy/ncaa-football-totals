# Frozen probability-calibration development experiment

Specification fixed September 9, 2026 before running this experiment. **No 2026 outcomes enter training, selection, evaluation or diagnostics.** This document contains no experiment results. The active v4 model, its ledgers and its prospective protocol remain unchanged.

## Question and inputs

Does a small recalibration fitted to genuinely prior out-of-fold predictions improve probabilities beyond both the raw ridge model and an equally recalibrated market reference? Does conditional variance add anything after that recalibration?

Use only the repaired prediction archive `reports/opponent_adjusted_predictions.csv`, restricted to `market_only` and `opponent_adjusted_ridge`, and its matching verified feature/input fingerprint `cf764f803b38ecfd`. Before execution, record SHA256 hashes of the prediction archive, this specification and the new experiment implementation. Confirm one row per candidate/game, identical game cohorts, finite required inputs, at least five prior team games, strictly earlier ratings cutoffs, and every prediction's original season-based fit. Reject a changed or mismatched input; never fall back to quarantined odds.

The predictor construction is in `ncaaf_model/opponent_model.py`: `adjusted_features` uses prior observations at weekly cutoffs; `fit_artifact` and `projections` create strongly regularized residual projections; `run_research` saves expanding-season forecasts and their prior-season market-residual sigma. Its current distribution artifact estimates one market-residual sigma and reuses it for ridge (`data/models/score_distribution_v2.json`). Model-specific calibration on honest forecast errors is the precise gap tested here.

Do not call `conditional_distribution.load_history`: it reads the quarantined scalar ESPN odds. Its four old families also change several components at once. No key-mass or empirical-kernel family is included in this experiment.

## Chronology and six configurations

Use the already saved 2021 forecasts as calibration warm-up. Test 2022, 2023, 2024 and 2025 separately. For test year y, fit every calibration parameter only to out-of-fold rows from 2021 through y−1. Test-year means and raw sigmas come from that year's saved out-of-fold predictions; never substitute today's all-years artifact or refitted in-sample predictions.

The common per-candidate test counts in the existing archive are 734, 795, 798 and 852 for 2022–2025; 2021 warm-up has 734. Revalidate counts rather than silently changing coverage. Imputation, clipping, fitting and model selection cannot inspect a test year's outcomes. The inherited `abs_spread` feature uses zero for missing original spread; report that limitation rather than adding a new missing-data policy.

For each base b in {market, ridge}, compare exactly:

1. **Raw:** saved base center and saved prior-season `residual_sigma`.
2. **Recalibrated:** the shared mean correction below and a constant, model-specific sigma estimated from prior out-of-fold errors.
3. **Conditional variance:** exactly the same recalibrated centers and base variance, adding only the specified variance regression.

No additional families, penalty grids, probability bins, score thresholds, ROI filters or nonlinear predictors may be selected after inspecting results. Direct logistic prediction from the eleven opponent features is a separate possible future experiment, not an unregistered seventh configuration.

## Exact fit equations and constants

For a calibration row i, let Y be the final integer total, L the repaired reference total, mu the saved base projection, A the inherited absolute spread, and e=Y−mu. Use fixed physical scales, not an additional data-dependent standardization:

```
X_i = [1, (mu_i − L_i)/4, (L_i − 55)/10, (A_i − 14)/14]
beta = inverse(X'X + 200 I_4) X'e
mu_cal,i = mu_i + clip(X_i beta, −4, +4)
r_i = Y_i − mu_cal,i
v0 = clip(mean(r_i²), 100, 576)
sigma_constant = sqrt(v0)
```

Fit beta and v0 separately for the market and ridge bases. For the market base, mu=L and the second design column is identically zero; its coefficient remains zero under the penalty. All four coefficients, including the intercept, are penalized. The slope correction is explicitly measured in points per four-point model deviation; it is not an unscaled probability slope. The constants retain the existing 200 penalty, four-point adjustment cap and 10–24-point sigma range. v0 uses prior out-of-fold base errors after fitting the small calibration model; it is not computed from test residuals.

For conditional variance, preserve mu_cal and v0 exactly. Define:

```
Z_i = [1, (L_i − 55)/10, (A_i − 14)/14]
v_i(gamma) = clip(v0 * exp(Z_i gamma), 100, 576)
objective(gamma) = 0.5 * sum(log(v_i) + r_i²/v_i)
                   + 100 * sum(gamma_j²)
```

Start gamma at zero and constrain each of its three coordinates to [−1,+1]. The penalty is lambda=200 in the conventional `(lambda/2)||gamma||²` notation; **do not divide the summed likelihood by sample count** without correspondingly changing the penalty. Use the same settings for both bases. Record convergence and boundary hits. A failed fit falls back to gamma=0 and is reported, never replaced by a different family chosen using test performance. Fit the variance using prior score errors because a near-50/50 categorical forecast alone weakly identifies dispersion.

## Coherent probabilities and scores

All configurations use the same zero-truncated, integer-discretized normal distribution. Preserve the existing half-point continuity boundaries and all tail mass; if using scores 0–250, the last bin retains overflow. The raw sigma uses the existing 6–30 safety bound, while calibrated sigma is already bounded 10–24. At a common reference line L, sum the PMF into Under (Y<L), Push (Y=L) and Over (Y>L). Push probability is exactly zero at a noninteger line. Do not separately patch push mass or sigmoid probabilities in a way that breaks a coherent score distribution.

**Primary metric:** mean three-outcome logarithmic loss at the common reference line, including pushes. For half-point/noninteger lines this becomes binary log loss. Secondary metrics, all reported: conditional binary log loss and Brier excluding realized pushes; full integer-score negative log likelihood; discrete CRPS `sum_k (F(k)−1[Y<=k])²`; expected versus observed push counts at actual integer reference lines. Report fixed reliability bins `[0,.4,.45,.5,.55,.6,1]` descriptively, with counts, rather than choosing bins that look favorable. A lower proper score measures overall probabilistic quality, not calibration alone. [Proper scoring-rule theory](https://sites.stat.washington.edu/raftery/Research/PDF/Gneiting2007jasa.pdf), [calibration documentation and the need for independent calibration forecasts](https://scikit-learn.org/stable/modules/calibration.html).

Candidate selection is the smallest game-weighted primary loss across the 2022–2024 test folds, with a deterministic tie-break ordered market raw, ridge raw, market recalibrated, ridge recalibrated, market conditional variance, ridge conditional variance. Freeze that choice before calculating the selection result's 2025 summary. Report **all six** 2025 configurations and the preselected one; do not substitute the best 2025 performer. The entire historical corpus has already been reused in this project, so 2025 remains a reused-development check, not a new untouched holdout.

## Paired comparisons and interpretation

Compare candidates on exactly the same game/line and resample complete Monday–Sunday Eastern calendar weeks, including every candidate's loss for each sampled game. Report paired loss differences and descriptive 95% intervals, with random seed 20260909 and 10,000 draws. Predeclare these eight comparisons: recalibrated versus raw within each base (two); conditional variance versus recalibrated within each base (two); ridge versus market at each of the three levels (three); and ridge conditional variance versus raw ridge (one). A Bonferroni sensitivity may use all eight comparisons (99.375% individual intervals); it does not repair historical search or justify a confirmatory profitability claim. Report annual and source-specific differences, including source-transition limitations, without selecting a favorable subgroup.

Fairness requires the calibrated market comparisons: a correction derived only from total level/spread is not evidence that opponent statistics add information. The historical reference lacks paired offered prices and certified receipt times; it is a market-centered statistical comparator, not known true no-vig probabilities or executable bookmaker prices. No ROI, EV threshold or historical bet selection enters this experiment.

Conditional variance can alter confidence when a projection differs from a line, but at a symmetric market-centered half-point line it supplies almost no directional information. More sharply concentrated probabilities are useful only if future scoring verifies them. Global integer-score reweighting can improve exact-score likelihood while barely improving offered-line decisions and can alter both location and dispersion after normalization. It is deferred here: the repaired archive contains 331 integer lines in 2022, only 18 in 2023, and **zero in 2024 or 2025**. Counterfactual rounded lines cannot validate push accuracy at actual offered lines in those later seasons.

Successful historical scoring would justify a separately versioned future paper test, not replacement of frozen v4, a new live EV claim or achievement of the profitability goal. Cross-week shared teams, archive revisions, source/regime shifts and historical reuse remain outside simple weekly sampling intervals.
