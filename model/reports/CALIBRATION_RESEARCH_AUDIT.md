# Independent audit of the frozen calibration experiment

Audit date: September 8, 2026 Eastern time. **Passed computational and chronology checks; no replacement model is justified by this experiment.** The frozen rule selects raw opponent-adjusted ridge using 2022–2024 scores. All three ridge configurations have worse primary log-loss point estimates than raw market in reused 2025 data. The paired uncertainty does not establish that raw ridge is worse than raw market, and it provides no reliable evidence of improvement.

The checked plan SHA-256 is `bbe38aeaef6fa287da0f4b7d15548f604fa85980d90c8ef1e9e4154851884d32`. See the [predeclared equations and comparisons](CALIBRATION_RESEARCH_PLAN.md), [machine-readable audit](calibration_research_audit.json), and [independent executable audit](../../scripts/audit_calibration_research.py).

## Independent verification

The audit imports no project model or research module. It checks the frozen plan and input hashes, reconstructs the original annual ridge fits directly from the verified feature cache, and reconstructs every probability distribution from the saved forecasts. It independently checks:

- Base means and raw residual scales for 2021–2025, using strictly earlier seasons. Every recorded ratings cutoff precedes kickoff. This confirms the saved feature chronology contract; it does not independently establish historical publication times for every upstream observation.
- All eight calibration fits: their prior out-of-fold calibration samples contain 734, 1,468, 2,263, and 3,061 rows for test years 2022, 2023, 2024, and 2025, respectively. No 2026 input is present. Mean and constant-variance equations reproduce the saved parameters. Separate optimization confirms each recorded conditional-variance objective within `5.8e-10` of the independent solution; all eight optimizers reported success.
- Identical recalibrated means for the constant- and conditional-variance comparisons, leaving only variance different in those paired tests.
- All 19,074 prediction rows: three-outcome log loss, conditional binary log loss, Brier score, exact-score log loss, and absolute error. CRPS is calculated through the independent energy identity rather than the study's squared-CDF implementation. Maximum discrepancies are zero for the four log-loss/Brier metrics, `5.7e-14` for CRPS, and `2.9e-14` for absolute error.
- Six-configuration selection using only 2,327 games from 2022–2024; all six 2025 configurations are evaluated on the same 852 games. All 16 reported paired comparisons (eight per period) and both bootstrap interval levels reproduce exactly.

## Results and paired uncertainty

Log loss is measured in natural-log units per game; lower is better. Differences below are candidate minus comparator. Intervals resample complete Eastern Monday–Sunday calendar weeks, preserving each game's paired predictions: 10,000 draws, seed 20260909. There are 59 observed weeks in 2022–2024 and 22 in 2025.

| Period and comparison | Mean difference | Paired 95% interval |
| --- | ---: | ---: |
| 2022–2024 raw ridge minus raw market | −0.001817 | [−0.004795, +0.001236] |
| 2025 raw ridge minus raw market | +0.004351 | [−0.000975, +0.010161] |
| 2025 recalibrated ridge minus raw ridge | −0.001075 | [−0.002338, +0.000083] |
| 2025 conditional-variance ridge minus recalibrated ridge | +0.000229 | [−0.000062, +0.000556] |
| 2025 conditional-variance ridge minus raw ridge | −0.000846 | [−0.002208, +0.000461] |

Raw market's 2025 primary log loss is 0.693158; raw, recalibrated, and conditional-variance ridge score 0.697509, 0.696435, and 0.696664. Some other unadjusted paired intervals exclude zero, including conditional versus constant variance during selection and ridge versus equally recalibrated market in 2025. **Every predeclared contrast's 99.375% interval includes zero.** Those wider intervals apply an eight-comparison Bonferroni adjustment within each period; they do not correct for the project's broader historical research or testing both periods.

## Interpretation and limits

The calculations support retaining the existing configuration and declining to add a live calibration candidate. They do not demonstrate market-beating probabilities or profitability. The market comparator is a probability model centered at the common historical total; it is not an observed, contemporaneous no-vig distribution with certified executable prices.

All 2020–2025 outcomes were already available during broader development. Strict annual fitting avoids within-experiment future-label fitting, but 2025 remains reused development data rather than a fresh project-level holdout. Bootstrap intervals assume resampled weeks adequately represent dependence; they cannot remove cross-week team effects, source shifts, prior research selection, or historical timestamp uncertainty. No 2025 quoted totals are integers, so this period does not validate push probabilities. There is no ROI or betting-threshold selection in this experiment, and no active model, prospective policy, or locked forecast was changed by this audit.

Reproduce from the repository root:

```bash
python scripts/audit_calibration_research.py
```
