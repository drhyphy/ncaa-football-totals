# PBP identity and chronology diagnostic

Research date: 2026-09-09. This diagnosis precedes matchup forecasts and model-performance evaluation. It reads only `season`, `game_id`, `id`, `sequenceNumber`, and `game_play_number` from the seven bodies pinned in [the source inventory](PBP_RESEARCH_SOURCE_INVENTORY.json). Only four identity-selected games additionally use period, clock and play type. No score or outcome columns were inspected.

**The 2025 rejection is caused by repeated `sequenceNumber`, not repeated raw play IDs.** All seven seasons have zero within-game duplicate `id`, zero duplicate `game_play_number`, zero duplicate composite `(game_id, sequenceNumber, game_play_number)`, and zero duplicate complete five-column identity tuples. All duplicate-field intersections are sequence-only.

| Season | Rows | Games | Sequence-tie games | Rows in tie games | Tied sequence groups | Rows in tied groups | Order-conflict games | Conflict games with unique sequence |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2019 | 156,908 | 890 | 4 | 748 | 4 | 8 | 17 | 16 |
| 2020 | 100,433 | 565 | 0 | 0 | 0 | 0 | 11 | 11 |
| 2021 | 147,319 | 842 | 5 | 887 | 8 | 23 | 31 | 28 |
| 2022 | 149,700 | 861 | 4 | 564 | 4 | 8 | 33 | 30 |
| 2023 | 153,690 | 903 | 7 | 1,206 | 8 | 18 | 24 | 22 |
| 2024 | 163,142 | 946 | 6 | 1,079 | 6 | 12 | 39 | 36 |
| 2025 | 166,053 | 956 | 414 | 72,693 | 4,290 | 8,813 | 439 | 31 |

In 2025, the 414 sequence-tie games contain exactly **72,693 rows**, explaining the earlier whole-game rejection. The 8,813 tied rows represent 4,523 keys beyond unique `(game_id, sequenceNumber)` pairs. Ordering by sequence produces 4,243 backward play-number steps across 439 games; 31 of those games have unique sequence values. “Order conflict” means any backward `game_play_number` step after sorting by sequence and then play number; it does not certify which field is correct. Complete per-season definitions, counts and examples are in [the JSON diagnostic](PBP_IDENTITY_DIAGNOSTIC.json).

## Missing and invalid identity values

Every selected column loads as nullable `Int64`; all seven seasons have **zero missing values and zero noninteger values** in the three play-identity fields. Raw `id` and play number have no invalid values under the parser's bounds (`id >= 1`, sequence and play number `>= 0`). The only bound violation is the negative sequence value shown below. Zero sequence values are reported separately and are allowed by those bounds.

| Season | Field | Missing | Negative | Zero | Invalid by parser bound |
|---|---|---:|---:|---:|---:|
| 2020 | `sequenceNumber` | 0 | 0 | 1 | 0 |
| 2021 | `sequenceNumber` | 0 | 1 | 2 | 1 |
| 2022 | `sequenceNumber` | 0 | 0 | 8 | 0 |
| 2023 | `sequenceNumber` | 0 | 0 | 1 | 0 |

All sequence values are below 2^53. Large raw IDs remain integer typed and unique. These facts do not establish the upstream cause of sequence collisions, and do not support attributing this rejection to float-rounded raw IDs.

## Fixed chronology sample

Selection used only identity properties: the two smallest 2025 game IDs with sequence ties, and the two smallest with unique sequence but an ordering conflict. No additional games were selected after examining clocks. Counts below are **period decreases / same-period clock increases** among adjacent records with valid regulation period and clock; ordinary quarter-boundary clock resets are not increases. Sequence ties use play number only to make this diagnostic comparison reproducible, not to authorize a chronology reconstruction.

| Game ID | Fixed selection group | Rows | Play-number order | Sequence order |
|---|---|---:|---:|---:|
| 401752746 | Repeated sequence | 164 | 0 / 13 | 0 / 1 |
| 401752747 | Repeated sequence | 170 | 0 / 6 | 0 / 0 |
| 401752673 | Unique sequence, order conflict | 213 | 1 / 0 | 0 / 1 |
| 401752800 | Unique sequence, order conflict | 152 | 0 / 1 | 0 / 0 |

For game 401752746, play-number order places a Q1 12:27 rush before a Q1 12:34 timeout; their sequence values are 9 then 8. Game 401752747 has a similar Q2 6:51 play before a 7:02 timeout in play-number order. In 401752673, play-number order places a Q1 record after a Q4 0:00 record. Sequence order fixes that period reversal but still contains one clock increase. In 401752800, sequence order fixes the one observed clock increase. These four examples favor keeping sequence as the primary order, but neither ordering is universally validated; clock corrections and administrative records remain possible.

## Primary-source interpretation

The [official release provenance](https://github.com/sportsdataverse/sportsdataverse-data/releases/tag/espn_cfb_pbp) identifies the capture, processing and publication repositories. In the inspected [processor ordering helper, lines 575–593](https://github.com/sportsdataverse/sportsdataverse-py/blob/main/sportsdataverse/cfb/cfb_pbp.py#L575-L593), regulation records are sorted by raw ID and adjusted remaining time; overtime uses sequence. The processor [casts supplied identities and applies that helper, lines 1104–1108](https://github.com/sportsdataverse/sportsdataverse-py/blob/main/sportsdataverse/cfb/cfb_pbp.py#L1104-L1108), then [assigns a one-based `game_play_number`, lines 1157–1158](https://github.com/sportsdataverse/sportsdataverse-py/blob/main/sportsdataverse/cfb/cfb_pbp.py#L1157-L1158). [Later filtering, lines 1778–1786](https://github.com/sportsdataverse/sportsdataverse-py/blob/main/sportsdataverse/cfb/cfb_pbp.py#L1778-L1786) can leave numbering gaps. Thus this processed row number is not an independent chronology observation. The [publication script](https://github.com/sportsdataverse/cfbfastR-cfb-data/blob/main/R/espn_cfb_01_pbp_creation.R) flattens records and conforms schema.

**Exact artifact-producing processor versions are uncertified.** These inspected primary-source locations explain the convention, but mutable source links do not prove the precise code/environment used for each downloaded asset.

## Bounded correction supported by this evidence

The proposed conservative correction is consistent with these observations: retain sequence as primary order; keep every archived row as a barrier; exclude all sequence-tied rows and the first state whose predecessor is tied; admit pre-score and clock edges only where unique adjacent sequence records also have consecutive play numbers. Duplicate raw IDs or duplicate play numbers should still reject the game. Do not guess tie order, bridge ambiguous records, or switch globally to play-number order. This preserves only locally agreeing segments and does not claim recovered chronology everywhere.

No source, frozen parser, acquisition status, inventory or prior build was modified by this diagnostic. Any subsequent parser revision and comparison must preserve the earlier build and its declared statistical design.
