# Frozen public PBP acquisition and metadata audit

Plan date: **2026-09-09**. Identifier: `espn-pbp-raw-acquisition-v1`. This plan acquires raw data for a future neutral-state clock/field-position hypothesis. It does not fit or select a model, inspect outcomes, change a betting policy, or designate an untouched evaluation period.

## Fixed source and scope

Acquire exactly seven original Parquets, seasons **2019–2025**, once each from the official [SportsDataverse ESPN PBP release](https://github.com/sportsdataverse/sportsdataverse-data/releases/expanded_assets/espn_cfb_pbp):

```text
https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_cfb_pbp/play_by_play_2019.parquet
https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_cfb_pbp/play_by_play_2020.parquet
https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_cfb_pbp/play_by_play_2021.parquet
https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_cfb_pbp/play_by_play_2022.parquet
https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_cfb_pbp/play_by_play_2023.parquet
https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_cfb_pbp/play_by_play_2024.parquet
https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_cfb_pbp/play_by_play_2025.parquet
```

The [feasibility report](PUBLIC_PBP_FEASIBILITY.md) records approximately **333 MB** across these files by rounded release-display sizes. Its observed expected 2025 SHA-256 is `7889db9cc0c3d27651dfeb602a35e36f6b180c440bea253332b57649992f848e`; that file must match. No earlier-season expected digest is invented. Each acquired body receives its own streaming SHA-256, exact byte count and original receipt. Mutable source URLs and acquisition times do not prove historical availability. Public access is not blanket authorization to redistribute the underlying content.

## Execution boundary and limits

The plan, [script](../../scripts/pbp_raw_acquisition.py) and [offline tests](../../scripts/tests/test_pbp_raw_acquisition.py) must be tracked and byte-identical to the selected checkout's committed `HEAD` before any HTTP request. The executed script must belong to that checkout. The source-file hashes and commit are retained with the acquisition plan and each attempt; an explicit resume requires the same source-file hashes and definition, even if unrelated commits have advanced `HEAD`.

Default invocation previews without HTTP or archive writes. Execution is sequential, with **at most seven logical file fetches per invocation**, no authentication, no environment credentials, no purchases and no automatic retries. A logical fetch may follow at most three redirects. Every redirect is validated before requesting: HTTPS, no embedded credentials, the exact selected GitHub asset path, or the explicitly allowed GitHub asset hosts `release-assets.githubusercontent.com`, `objects.githubusercontent.com`, and `github-releases.githubusercontent.com`. Public signed redirect query strings are used only for retrieval and omitted from persisted URLs and logs.

Connect and read timeouts are each **30 seconds**. Fixed generous fail caps are **100 MiB (104,857,600 bytes) per file** and **600 MiB (629,145,600 bytes) of observed response payload over the acquisition's lifetime**, including failed attempts. These are task bounds, not claims about provider limits. Oversized declared lengths fail before body consumption. Streaming uses 64 KiB chunks; a crossing chunk may be observed before the cap is detected, but is not written or accepted. Its observed bytes remain charged against the lifetime budget. Unreceipted interrupted attempts conservatively charge retained partial bytes; bytes lost before receipt/persistence cannot be reconstructed. Redirect/error bodies are not consumed. Unexpected compression, inconsistent length, malformed Parquet and expected-hash mismatch fail explicitly. The first failure stops that invocation.

## Immutable archive and resume

Use only the ignored namespace `model/data/raw/pbp_research/espn/`:

- `acquisition_plan.json`: original immutable definition and source-code provenance.
- `play_by_play_{season}.parquet`: original successful bytes, published exclusively without overwriting.
- `attempts/{season}/{number}.request.json` and `.receipt.json`: append-only attempt records, with actual request/receipt UTC times, status, selected response headers, safe redirect chain, source/body hashes and byte counts.
- `partials/{season}/{number}.parquet`: retained incomplete/rejected bytes, never treated as completed input.
- `runs/{number}.json`: append-only invocation result, complete/missing seasons and failure reason.

Each season has an original namespace; new random acquisition directories or nonce-based retries are prohibited. A second incomplete run requires explicit `--resume`. It validates existing completed body hashes, sizes, source identities and footer metadata before HTTP, skips those files, and attempts only missing/failed seasons. A bare file without a successful original receipt is rejected, not silently adopted or overwritten. An interrupted publication without a receipt requires review. Failure does not authorize a fallback source or refresh of a completed file. Already-complete runs only verify cached files and make zero HTTP requests.

## Metadata-only audit

After each accepted download, read only the Parquet footer: schema field names/types/nullability, row/column counts, row-group counts, and each row group's row count and uncompressed byte size. Do not read table data, column values, min/max statistics, outcome summaries, feature correlations or model metrics. Normalized game-ID/date/period/clock coverage validation is a later separately agreed stage. Published EPA is excluded from any presumption of historical validity: its documented current model was trained through 2025.

From the repository root, after committing the three files:

```bash
python scripts/pbp_raw_acquisition.py --root .
python -m pytest -q scripts/tests/test_pbp_raw_acquisition.py
python scripts/pbp_raw_acquisition.py --root . --download
# Only after an interrupted/failed original acquisition is reviewed:
python scripts/pbp_raw_acquisition.py --root . --download --resume
```

The agent preparing this plan does not execute the network commands. Tests use fake HTTP responses and tiny synthetic Parquet bodies only.
