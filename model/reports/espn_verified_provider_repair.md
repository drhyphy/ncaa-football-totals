# Historical ESPN provider repair

**2,742 of 2,781 games retained.** Label: pregame-provider archived line, close-time unverified.

A fixed provider-ID/name priority excludes live markets without substituting opening prices. No outcome or model metric influences selection.

| Season | Original | Retained | Excluded | Changed totals |
|---|---:|---:|---:|---:|
| 2023 | 879 | 879 | 0 | 10 |
| 2024 | 956 | 920 | 36 | 0 |
| 2025 | 946 | 943 | 3 | 0 |

Retained provider counts: {"ESPN BET": 2680, "DraftKings": 61, "Caesars Sportsbook (Colorado)": 1}.

The exported parquet contains game metadata, signed home spread, provider identity, response hash, source URL, and audit observation time. `role_verified=true`; `quote_timestamp=null`; `closing_time_verified=false`. Cached raw gzip responses are ignored by Git.

## Limits

- Role verified by fixed known non-live bookmaker IDs and exact names; provider59 and any live-named provider excluded.
- No historical quote timestamps or evidence of an exact closing or06:30AM price. Observation time is the audit retrieval time.
- Opening totals are never substituted. Selection uses provider role and availability, never outcomes or model performance.
- Retained and excluded populations can differ. Compare clean-source evaluation to its own population; old contaminated results are not repaired retroactively.
