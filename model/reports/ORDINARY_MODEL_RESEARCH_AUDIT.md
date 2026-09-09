# Independent audit of the fixed ordinary-statistics experiment

**Passed. Neither new configuration merits replacement of the existing candidate.** The frozen 2021–2024 MSE selection chooses the existing opponent-adjusted ridge. Both the richer-statistics ridge and histogram gradient boosting model have higher MSE than both references during selection and in reused 2025 data. This study provides no new probability or betting edge.

The audited plan SHA-256 is `c74130cef9ec2ed994f3cb75f58fc354c9639323c06590eea43d6208bc82a150`; the prediction file SHA-256 is `c9d1504b1c21a2db48072f1a4ca2fb33fbf0ad11a3f0e08aebae90bf15d28ca6`. The [pre-fit audit](ORDINARY_MODEL_PREFIT_AUDIT.md) documents source/timing checks and the input-identity fix made before freezing. See the [fixed protocol](ORDINARY_MODEL_RESEARCH_PLAN.md), [independent executable replay](../../scripts/audit_ordinary_model_research.py), and [machine-readable audit](ordinary_model_research_audit.json).

## Independent reconstruction

The audit imports no project planning, feature, or model module. It checks all frozen input hashes and numerical versions, then replays all ten new annual fits using the frozen 58-predictor matrix. Training samples contain 505, 1,239, 1,973, 2,768 and 3,566 games for test years 2021–2025, respectively; every training season precedes its test season. The common test counts are 734, 734, 795, 798 and 852. No 2026 observation enters the experiment.

Median imputation and missing indicators are reconstructed directly from each training matrix. Ridge is independently solved through centered penalized normal equations, instead of the study's preprocessing pipeline and SVD estimator. Its largest forecast discrepancy is `3.42e-13`. The gradient boosting model is instantiated separately with the frozen public-estimator parameters and independently prepared matrices; its forecasts match exactly. Both retain the specified residual cap. Training missingness, empty columns, clipping and fit counts also match.

All **15,652 configuration forecasts across 3,913 games** are verified, including exact shared identities, targets, total lines and saved comparator forecasts. The existing comparator fits had separately passed the [calibration audit](CALIBRATION_RESEARCH_AUDIT.md); this replay checks their frozen values and shared sample without changing them.

The audit independently recomputes MSE, RMSE, MAE and mean signed error; 2021–2024 selection; and **20 summary groups with 80 paired comparisons**, including annual, source, and period-by-source diagnostics. It recreates the frozen 10,000 week draws as week multiplicities, rather than the study's sampled metric tensor. All reported 95% and 98.75% MSE intervals and 95% MAE intervals match within numerical tolerance.

## Results and uncertainty

MSE is measured in squared total-score points; lower is better.

| Configuration | 2021–2024 MSE, 3,061 games | 2025 MSE, 852 games |
| --- | ---: | ---: |
| Market reference | 251.4935 | 239.8744 |
| Existing opponent-adjusted ridge | **250.4281** | 240.9862 |
| New ordinary-statistics ridge | 255.8828 | 242.0733 |
| New ordinary-statistics gradient boosting | 259.8281 | 246.9417 |

The selection period contains 74 Eastern calendar-week blocks; 2025 contains 22. Positive differences below mean higher error for the new model.

| 2025 comparison | MSE difference | Descriptive 95% paired-week interval | Four-comparison 98.75% interval |
| --- | ---: | ---: | ---: |
| New ridge minus market | +2.1989 | [−2.6457, +7.5903] | [−3.7823, +9.2829] |
| New ridge minus existing ridge | +1.0871 | [−1.1715, +3.5020] | [−1.6748, +4.2060] |
| New boosting minus market | +7.0673 | [+0.8045, +12.8000] | [−1.2776, +14.2522] |
| New boosting minus existing ridge | +5.9555 | [+1.8091, +9.7055] | [+0.4775, +10.6778] |

In 2021–2024, all four new-model-minus-reference 98.75% MSE intervals are above zero. In 2025, the new ridge's higher-error point estimates remain uncertain; the boosting-minus-existing-ridge interval stays above zero under the four-comparison sensitivity. These statements concern the fixed models and the stated week-resampling assumptions, not every possible richer-feature or nonlinear model.

## Limits and decision

The 2025 outcomes were excluded from this experiment's selection rule but were already used elsewhere in development. The intervals are descriptive; the four-comparison sensitivity does not cover the project's larger research search, all 80 diagnostic comparisons, or cross-week team dependence. Historical stat availability and quote timing remain proxies, and source/era effects may be entangled.

The justified decision is to retain the existing configuration and publish the unsuccessful candidates. No live model, threshold, probability distribution, hypothetical ROI, stake, or prospective evaluation policy was changed. Lower point-prediction loss alone would not have established executable positive EV, and these candidates did not improve that loss.

Reproduce from the repository root:

```bash
python scripts/audit_ordinary_model_research.py
```
