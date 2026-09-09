# Public play-by-play feasibility

Research snapshot: **2026-09-09**. Scope: primary documentation, official GitHub release metadata and local feature/model code. No seasonal PBP files were downloaded, new game outcomes inspected, correlations calculated or models fitted.

**Finding:** SportsDataverse's public ESPN play-by-play assets provide a feasible no-key path to finer prior-game features. They do not establish an edge. The next active experiment is the separately specified direct-classification comparison; the PBP work below remains a future, distinct hypothesis.

## Verified sources and access

| Source | Published coverage verified | Relevant content and limits |
|---|---|---|
| [ESPN PBP](https://cfbfastr.sportsdataverse.org/reference/load_espn_cfb_pbp.html) / [release assets](https://github.com/sportsdataverse/sportsdataverse-data/releases/expanded_assets/espn_cfb_pbp) | Seasonal files 2004–2026; 2026 is partial | Game/play/drive IDs, possession, period/game clock, down/distance, yards to end zone, play/scoring types, penalties, timeouts, score state and participants. Presence of a season file does not certify complete game or field coverage. |
| [ESPN drives](https://raw.githubusercontent.com/sportsdataverse/cfbfastR/main/R/load_espn_cfb.R) / [release assets](https://github.com/sportsdataverse/sportsdataverse-data/releases/expanded_assets/espn_cfb_drives) | 2004–2026 | Drive start/end field position and clock, result, offensive plays and yards. Recent seasons are already local and substantially overlap tested features. |
| [Classic cfbfastR PBP](https://cfbfastr.sportsdataverse.org/reference/load_cfb_pbp.html) / [release assets](https://github.com/sportsdataverse/sportsdataverse-data/releases/expanded_assets/cfbfastR_cfb_pbp) | FBS files 2014–2026 | Public prebuilt files require no key. This alternative processed family is not automatically independent evidence, and derived metrics require their own vintage audit. |
| [NCAA-native PBP release](https://github.com/sportsdataverse/sportsdataverse-data/releases/tag/ncaa_mfb_pbp) / [schema](https://cfbfastr.sportsdataverse.org/reference/load_ncaa_mfb_pbp.html) | Release description explicitly documents 2013–2025 | Parsed official stats.ncaa.org observations, including coverage ESPN misses. Drive/play numbers, clock, down/distance, field position, play flags and ESPN-ID crosswalk. Identity and schema agreement must be checked before combining sources. |
| Direct CFBD [plays](https://api.collegefootballdata.com/api/plays) and [drives](https://api.collegefootballdata.com/api/drives) | Historical endpoints; complete year-by-year coverage not independently measured here | Bearer key required. Plays include clock, down/distance, yards-to-goal, timeouts, types and PPA; drives include elapsed time, field position and scoring context. The [free tier](https://collegefootballdata.com/api-tiers) currently advertises 1,000 calls/month without a credit card. This is a free-key alternative, not a no-new-key path. |

Concrete public asset URLs, using 2025 as an example:

```text
https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_cfb_pbp/play_by_play_2025.parquet
https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_cfb_drives/drives_2025.parquet
https://github.com/sportsdataverse/sportsdataverse-data/releases/download/cfbfastR_cfb_pbp/play_by_play_2025.parquet
https://github.com/sportsdataverse/sportsdataverse-data/releases/download/ncaa_mfb_pbp/ncaa_mfb_pbp_2025.parquet
```

The observed ESPN PBP release metadata reports these Parquet sizes:

| Season | GitHub displayed size |
|---|---:|
| 2019 | 49.7 MB |
| 2020 | 31.8 MB |
| 2021 | 46.6 MB |
| 2022 | 47.4 MB |
| 2023 | 48.4 MB |
| 2024 | 52.6 MB |
| 2025 | 56.4 MB |
| **2019–2025 total** | **approximately 333 MB** |

These are rounded release-display sizes, not downloaded byte counts. For example, the listing reports the 2025 Parquet SHA-256 as `7889db9cc0c3d27651dfeb602a35e36f6b180c440bea253332b57649992f848e`, uploaded `2026-09-01T09:12:00Z`. This hash was read from GitHub metadata, not independently verified against downloaded bytes. The historical assets inspected were refreshed in 2026; stable URLs do not mean immutable contents or original pregame publication. A later acquisition must preserve original bytes, receipt, asset metadata and independently calculated hashes.

## EPA training-vintage finding

The official [EP model specification](https://cfbfastr.sportsdataverse.org/articles/college-football-expected-points-model-fundamentals-part-ii.html), dated September 3, 2026, explicitly says the current model was trained on **2004–2025, 2,219,971 plays**. Its eight predictors are game situation variables, including time, field position, down/distance and possession score margin. They do not include a market line.

Using that current trained model's EPA as an input to our 2021–2025 historical forecasts would introduce future model-fitting information. Simply lagging team EPA does not remove this problem. The exact generating artifact for every released historical EPA column was not independently established here, so published EPA cannot be treated as verified out-of-time input. A defensible EPA experiment would require a demonstrably earlier fixed artifact or EP fitting restricted to each evaluation fold's earlier seasons.

Market dependence is a separate issue: the official [WP specification](https://cfbfastr.sportsdataverse.org/articles/college-football-expected-points-model-fundamentals-part-v.html) distinguishes naive WP from `vegas_wp`, which incorporates the pregame spread. Do not describe all EPA/WP fields as market-derived. Neither their names nor current release availability establishes historical feature availability.

## Distinct raw-field opportunity

The existing [drive model](../ncaaf_model/drive_model.py) already aggregates regulation TD/FG/turnover rates, drives, seconds per drive/play, plays per drive and other points. The [ordinary58 builder](../ncaaf_model/ordinary_features_research.py) adds prior offense/defense, pass/rush efficiency and share, rest/counts and six adjusted predictors. Repeating aggregate possession or pace features would overlap this tested information. The existing conditional distribution family also already varies residual distributions with market total, spread and rule era.

The narrower untested opportunity is **play-state-normalized clock consumption and field-position/down-distance-adjusted efficiency**. Raw observations could distinguish clock consumption by score state, play type, down/distance and clock-stopping events, and compare offensive progress from comparable starting positions. This is a proposed feature family, not a selected model or evidence of predictive improvement.

The schema's clock is the **game clock**, not a measured 40-second play clock or certified snap timestamp. Adjacent-play decrements require validated ordering, unchanged possession/period and explicit handling of penalties, administrative rows, timeouts and terminal plays. They must not be mislabeled true between-snap timing. `start.yardsToEndzone` supplies a more explicit orientation basis than a generic yard-line number, but ranges, possession alignment and field coverage still need validation. The source's `wallclock` and `modified` fields were not verified as historical receipt or snap-time evidence.

Before any fit, freeze a modest raw-column allowlist, deterministic schema/coverage audit, exclusions and feature equations. Require exact canonical game/team IDs and completed-game status; construct team and opponent summaries only from games available strictly before the shared weekly cutoff. Retain the existing disclosed kickoff-plus-six-hours availability proxy for retrospective reconstruction. Same-cutoff equivalence, future-outcome invariance and duplicate identity tests remain necessary. Historical corrections and absence of original delivery receipts remain limitations even with correct temporal joins.

All 2019–2025 periods have already influenced this project. New PBP does not make them untouched validation, and older eras cannot establish current profitability. Public accessibility is evidence that the assets can be retrieved without a new subscription; it is **not** blanket permission to redistribute underlying content. No additional licensing rights or terms are inferred here.
