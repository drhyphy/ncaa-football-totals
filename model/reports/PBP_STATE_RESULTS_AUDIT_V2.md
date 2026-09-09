# Independent audit of PBP state matchup results

Audit status: **passed**. Reconstructed 10 annual residual ridge fits without importing the study's fitting or scoring functions.

The fixed 2021–2024 MSE selection reproduces `opponent_adjusted_ridge`. All 1,143 checked numeric values agree within the declared tolerances; 0 mismatches. Maximum absolute numeric difference: 2.84e-13.

| Period | Model | Games | MSE | MAE |
| --- | --- | ---: | ---: | ---: |
| selection | market_only | 3061 | 251.493466 | 12.516171 |
| selection | opponent_adjusted_ridge | 3061 | 250.428117 | 12.498979 |
| selection | pbp_state_ridge | 3061 | 250.819366 | 12.505055 |
| 2025 | market_only | 852 | 239.874413 | 12.475352 |
| 2025 | opponent_adjusted_ridge | 852 | 240.986232 | 12.561475 |
| 2025 | pbp_state_ridge | 852 | 240.942644 | 12.559962 |

The audit verifies immutable source/cache/prediction hashes, recorded Git blobs and stage ancestry, training-only mean/scale transforms, penalty 200 on every coefficient including intercept, residual clipping, strictly earlier seasons, disjoint target IDs, and saved baseline parity. It independently recomputes every pooled, annual and source MSE/MAE summary and the fixed 10,000 whole-week ratio bootstraps (PCG64 seed 20260909; 95%/99% MSE and 95% MAE intervals).

All history remains reused development. This audit does not refit the upstream sparse play-state ratings and does not certify historical publication time. Week intervals do not account for the full project search. The experiment evaluates point forecasts; no executable EV, priced profitability, new active policy or confirmed edge follows from this audit.
