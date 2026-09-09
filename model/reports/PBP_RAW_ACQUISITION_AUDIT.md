# Public PBP acquisition audit

Audited **2026-09-09**. Original-byte hashing and Parquet footer/schema inspection only: no table values, column statistics, game outcomes, feature correlations or model metrics were read. The auditor made no network requests and changed no original archive files.

**All seven retained bodies match the fresh public GitHub release's exact source URL, asset digest and size.** The original acquisition correctly remains **partial**: 2019–2024 passed its checks, while 2025 failed its frozen expected hash. A separate [research-source inventory](PBP_RESEARCH_SOURCE_INVENTORY.json) selects the verified original bytes for future research after that inventory is committed; it preserves the failed 2025 receipt and original partial-file path.

## Original acquisition integrity

The plan, downloader and tests match their recorded SHA-256 values at commit `cdcba88fc6c5cad141e9ccf987ff2482a96a1f1f`. The acquisition plan hash is `281427a27bb062729ab9b21348b4038b4730231e3aea40ed49951527fc5d44d1`; the original run manifest hash is `dc7ff06be33bf1401eb2ae4bd8670511d6f760132f5e448209f6b778ccbde5e7`.

One invocation made **7 logical fetches / 14 HTTP requests**, with no retry or resume. Every file followed one recorded HTTPS redirect from the exact GitHub asset URL to `release-assets.githubusercontent.com`, then HTTP 200. Request/header/receipt times are ordered, signed query strings are omitted, all body hashes and sizes match their receipts and final Content-Length, and both byte caps were respected. The run occurred from `2026-09-09T05:45:20.317629+00:00` through `2026-09-09T05:45:33.715791+00:00`.

| Season | Original acquisition state | Bytes | Footer rows | Columns | Row groups |
|---|---|---:|---:|---:|---:|
| 2019 | Accepted | 52,072,547 | 156,908 | 497 | 2 |
| 2020 | Accepted | 33,396,809 | 100,433 | 496 | 1 |
| 2021 | Accepted | 49,143,293 | 147,319 | 497 | 2 |
| 2022 | Accepted | 49,831,930 | 149,700 | 497 | 2 |
| 2023 | Accepted | 50,819,931 | 153,690 | 497 | 2 |
| 2024 | Accepted | 55,269,115 | 163,142 | 497 | 2 |
| 2025 | Quarantined: expected-hash mismatch | 59,281,459 | 166,053 | 496 | 2 |
| **Total retained** | **Original status remains partial** | **349,815,084** | **1,037,245** | — | **13** |

The six originally accepted files account for 290,533,625 bytes and 871,192 footer rows. Every footer parses and row-group counts reconcile. For those six, independently reconstructed footer metadata exactly matches the original receipt. The quarantined 2025 body also has a valid footer; that fact alone did not override its failed hash gate. These are PBP row counts, not validated game counts or unique-play counts.

## Separate 2025 source reconciliation

The frozen expected hash, recorded from an earlier cached release listing, was:

```text
7889db9cc0c3d27651dfeb602a35e36f6b180c440bea253332b57649992f848e
```

The retained body hash is:

```text
aa037920e980acf17317747f2010a031b3b7e1013a44a7b15db18b1dd9df0e63
```

Root separately archived one fresh [public GitHub release API response](https://api.github.com/repos/sportsdataverse/sportsdataverse-data/releases/tags/espn_cfb_pbp), HTTP 200, received `2026-09-09T05:47:14.197310+00:00`. Its original 126,222-byte body hash independently verifies as `768a7c93172a395bec7713b5e1196f82560769e0eb5f64e6c8246981e4c912cd`. It identifies 2025 asset **549505481**, updated **2026-09-07T23:08:18Z**, with the retained body's exact digest and size. All six earlier seasons also uniquely match the API's exact asset names, URLs, hashes and sizes.

The current metadata identifies a September 7 version; the earlier cached listing described a September 1 version. This is a documented source-version reconciliation. The original plan, expected pin, failed receipt, run status and stored body remain unchanged. The separate inventory points directly to `model/data/raw/pbp_research/espn/partials/2025/0001.parquet`; no body was republished, overwritten or downloaded again.

## Schema feasibility and limits

All seven schemas contain the 31 anticipated identity, ordering, possession, down/distance, field-position, clock, type, drive, status and score-state column names checked. Relevant types include integer game/team/order IDs and start yards-to-end-zone, string clock/type/drive IDs, and boolean completed status. The explicit schema audit is recorded in the [JSON report](PBP_RAW_ACQUISITION_AUDIT.json). The actual 496–497 columns exceed the earlier documentation snapshot; future parsers must use an explicit validated allowlist.

Field-name presence does not establish non-null coverage, correct semantics, stable ordering, possession identity or field orientation. The checked kickoff names `game_date`, `gameDate`, `start_date` and `startDate` are absent; future cutoff construction needs an independently verified schedule join. Game-clock data is not automatically measured snap timing. Published EPA and other modeled columns remain subject to the [training-vintage limitation](PUBLIC_PBP_FEASIBILITY.md).

Local retrieval times and September 2026 source refresh dates do not certify original historical pregame availability. Source verification neither demonstrates an edge nor changes a model, betting rule, evaluation period or underlying redistribution rights. A later normalized coverage/feature audit still requires its own agreed scope.
