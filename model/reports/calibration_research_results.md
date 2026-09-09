# Repaired-data probability calibration experiment

This is chronological research on reused data, not prospective profitability evidence.

| Period | Configuration | Games | Market log loss | Conditional log loss | Brier | CRPS |
|---|---|---:|---:|---:|---:|---:|
| selection_2022_2024 | market_only:conditional_variance | 2327 | 0.710775 | 0.691864 | 0.249360 | 8.8710 |
| selection_2022_2024 | market_only:raw | 2327 | 0.712282 | 0.693135 | 0.249994 | 8.8965 |
| selection_2022_2024 | market_only:recalibrated | 2327 | 0.711055 | 0.691948 | 0.249401 | 8.8750 |
| selection_2022_2024 | opponent_adjusted_ridge:conditional_variance | 2327 | 0.711164 | 0.692250 | 0.249551 | 8.8820 |
| selection_2022_2024 | opponent_adjusted_ridge:raw | 2327 | 0.710465 | 0.691300 | 0.249078 | 8.8725 |
| selection_2022_2024 | opponent_adjusted_ridge:recalibrated | 2327 | 0.711505 | 0.692396 | 0.249622 | 8.8864 |
| reused_2025 | market_only:conditional_variance | 852 | 0.694359 | 0.694359 | 0.250600 | 8.7769 |
| reused_2025 | market_only:raw | 852 | 0.693158 | 0.693158 | 0.250005 | 8.7725 |
| reused_2025 | market_only:recalibrated | 852 | 0.694248 | 0.694248 | 0.250545 | 8.7801 |
| reused_2025 | opponent_adjusted_ridge:conditional_variance | 852 | 0.696664 | 0.696664 | 0.251737 | 8.8006 |
| reused_2025 | opponent_adjusted_ridge:raw | 852 | 0.697509 | 0.697509 | 0.252163 | 8.8154 |
| reused_2025 | opponent_adjusted_ridge:recalibrated | 852 | 0.696435 | 0.696435 | 0.251626 | 8.8036 |

The fixed2022–24selection rule chooses **opponent_adjusted_ridge:raw**. All2025configurations remain visible in the JSON report. Lower loss is better.

All paired comparisons, reliability bins and source-specific metrics are in the machine-readable report. No returns or EV-based selections were calculated.

## Limitations

- All2021–25outcomes were previously used in research; chronological fitting does not make this a pristine holdout.
- Verified pregame-provider role does not certify exact entry time or prices; no betting returns or EV-based selections are calculated.
- 2024and2025have no posted integer lines in this common evaluation sample; their posted-push calibration is untested.
- Raw comparators use saved fold means/scales in the same nonnegative discrete-normal family; this is not a byte-for-byte runtime replay.
- Eight-comparison intervals are descriptive on reused data and do not account for the entire earlier research search.
- The inherited absolute-spread feature maps a missing original spread to zero; no new missing-data treatment is introduced.
- No2026outcomes used; no active model artifact, registered forward policy or ledger changed.
