# Frozen-model 2026 replay with recorded prices

These predictions were reconstructed after the games. They are not a pre-existing prospective forecast record. Actual archived prices, rather than assumed −110, determine hypothetical returns.

| Books | Candidate | Settled signals | W–L–P | ROI | Pending |
|---|---|---:|---|---:|---:|
| connected_two_books | market_price_reference | 12 | 6–6–0 | -3.40% | 3 |
| connected_two_books | opponent_adjusted_ridge | 2 | 1–1–0 | -3.70% | 2 |
| connected_two_books | opponent_adjusted_structural | 11 | 5–6–0 | -12.13% | 5 |

## Limits

- Models fitted only through 2025, but this replay was created after 2026 game outcomes were available.
- Raw quote receipts have matching local metadata hashes; no independent pre-kickoff timestamp attestation of every raw price.
- Exact recorded prices replace the -110 assumption; sportsbook acceptance, stake limits and account availability remain unverified.
- Prior game inputs use kickoff+6h availability proxies and retrospective corrections. Target and future outcomes are excluded from feature cutoffs.
- Archive capture times and sparse capture dates differ from the current 06:30 publication schedule.
- Unchanged candidate models and qualification thresholds; no post-result selection of games, books or forecasts.

- The original pre-evaluation plan pinned quote, provenance and fitted-model files, but did not pin code, configuration or history hashes; full byte-for-byte pre-evaluation reproducibility is not attested.

Reproduce after normalizing the existing source archives: `python -m ncaaf_model.archived_replay --plan`, then `python -m ncaaf_model.archived_replay --evaluate`. No live ledger is written.
