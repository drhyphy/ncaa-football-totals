# Ambiguous 55.5-total sensitivity

No models were refitted and no thresholds were selected from this comparison. The same frozen out-of-fold predictions are rescored after excluding every 55.5 line. Some excluded lines are genuine market quotes. This intentionally conservative check addresses the archive’s inability to distinguish a missing-total fallback from a real 55.5 quote when a spread exists.

| Candidate | All games | Retained | MAE, all | MAE, exclude 55.5 | ROI, all | ROI, exclude 55.5 |
|---|---:|---:|---:|---:|---:|---:|
| market_only | 2781 | 2615 | 12.4626 | 12.4759 | — | — |
| pace_efficiency | 2781 | 2615 | 12.5056 | 12.5137 | -2.61% | -3.33% |
| market_residual_ridge | 2781 | 2615 | 13.7296 | 13.7142 | -4.80% | -4.48% |
| market_residual_hgb | 2781 | 2615 | 13.0940 | 13.1471 | -1.81% | -2.83% |
| market_residual_ensemble | 2781 | 2615 | 13.3163 | 13.3231 | -4.55% | -4.61% |
| public_fei_epa | 2781 | 2615 | 13.2782 | 13.2741 | — | — |
| public_fpi | 2781 | 2615 | 13.1819 | 13.1912 | — | — |
| public_full_hgb | 2781 | 2615 | 12.7700 | 12.8075 | — | — |
| public_roster_prior | 2781 | 2615 | 13.4350 | 13.4211 | — | — |
| public_summary | 2781 | 2615 | 13.4390 | 13.4555 | — | — |
| public_superensemble_v2 | 2781 | 2615 | 12.4978 | 12.5175 | — | — |

All confidence-gated public policies still abstain. This exercise does not rescue the earlier leakage-compromised results or establish executable profitability. Intervals and detailed sample counts appear in the JSON report.
