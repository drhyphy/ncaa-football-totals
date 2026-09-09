# Weather revision capture feasibility

Read-only assessment, September 9, 2026 UTC. **A small prospective archive is feasible.** The strongest practical next step is a separate, collection-only pilot using explicit GFS initialization times, joined to newly received two-book quote observations. It would not change the fixed live weather rule, generate signals, or establish an edge. The initial assessment inspected code and primary documentation without forecast requests. A subsequent bounded endpoint preflight, documented below, corrected the request example. No game outcomes were analyzed.

The current implementation already supplies useful components:

| Component | Existing behavior | Gap for revision research |
|---|---|---|
| `weather_shadow.py` | Explicit `gfs_global`; three weather variables; request parameters, UTC receipt, venue metadata and parsed payload; immutable content-addressed gzip archive | `live` uses the latest stitched product without an exact initialization identifier. Original response bytes and HTTP headers are not retained. |
| `weather_candidate.py` | Fresh actual ESPN event/venue/roof receipts; frozen 100-venue coordinate catalog; pregame, outdoor and non-neutral validation; archives under `data/runtime/weather/` | Target validation deliberately requires game day after 06:30 Eastern and within 24 hours. It cannot simply be reused for a seven-day collection cohort. |
| Daily runtime | Captures `previous_day2` weather before quotes, then publishes the existing fixed paper strategy | It does not collect forecast revisions during the preceding week. |
| `closing_collector.py`, `capture.yml` | Paired full-game prices, exact decimal payouts, separate market-update/response-receipt times, immutable normalized quote snapshots; 32 scheduled attempts/day during configured football months | Targets come from positions/board forecasts within 24 hours. It collects no weather. The selected feed's original JSON body/HTTP headers are discarded before archival. |

The quote collector's overnight scheduling gap also means attaching a new-run check to its current schedule would not guarantee observing every GFS cycle. GitHub execution time can differ from scheduled time; record actual receipt and workflow run identifiers. Existing file hashes authenticate content, while a Git commit alone is not an independently certified historical timestamp.

**Use explicit runs for revisions, retaining the current fixed-lead product separately.** Open-Meteo's Single Runs endpoint accepts `run=YYYY-MM-DDTHH:MM` in UTC and documents GFS-era coverage from April 2, 2026. A run identifies initialization, not publication; global forecasts generally take another 4–6 hours to become accessible. The service returns errors for unavailable runs. Its documentation advertises broad compatibility with forecast parameters, but the subsequent preflight found that `start_hour`/`end_hour` cannot be used here. Request the full run and select the required valid hours locally. [Single Runs documentation](https://open-meteo.com/en/docs/single-runs-api).

```text
https://single-runs-api.open-meteo.com/v1/forecast
  ?latitude=<frozen_latitude>&longitude=<frozen_longitude>
  &models=gfs_global&run=<explicit_UTC_initialization>
  &hourly=temperature_2m,relative_humidity_2m,wind_speed_10m
  &forecast_days=8
  &temperature_unit=fahrenheit&wind_speed_unit=mph&timezone=GMT
```

Keep `models=gfs_global` fixed and retain requested/returned coordinates, grid elevation and spatial options. The provider describes GFS global output at approximately 13/25-km resolution; its default location/elevation processing differs from the earlier NOAA 0.25° bilinear extraction. Hourly API values beyond 120 forecast hours interpolate native three-hour GFS data. Consequently, days 6–7 have a different temporal measurement resolution even when four hourly values are returned. [GFS fields, resolution and request semantics](https://open-meteo.com/en/docs/gfs-api).

For a first pilot, choose `floor_to_6h(capture_start_utc − 6h)` deterministically, request that exact run, and record success or failure without substituting another cycle. Call this the **latest cycle eligible under the six-hour collection buffer**, not the latest possible forecast. A later enhancement could archive provider metadata to choose a newly available run. The metadata distinguishes initialization, conversion completion and API availability; the provider recommends allowing ten minutes for replication across servers. Metadata calls do not count toward forecast quotas. They are corroborating receipts, not substitutes for the received forecast body. [Model-update metadata semantics](https://open-meteo.com/en/docs/model-updates).

Continue archiving the existing `previous_day2` product as its own comparator when mature. It is a fixed lead-time series, with a potentially different source initialization for each valid hour; do not stamp a single inferred run onto it. The provider explicitly distinguishes these offsets from independently stored runs. [Previous Runs documentation](https://open-meteo.com/en/docs/previous-runs-api). Preserve the current maturity guard: receipt must be at or after kickoff-hour +3h −48h +6h. Early provisional responses cannot later become mature merely because time passed. The daily strategy's 06:30 Eastern rule and fixed thresholds remain unchanged. A same-initialization GFS run at least 48 hours before kickoff would be another clearly named archive product, not an interchangeable replacement.

**Minimal collection contract.** Freeze this before any analysis:

1. At each collection start, archive the schedule/provider event inventory and enumerate all matched, confirmed pregame games in the next seven days, capped at 150 by kickoff then game ID. Include every cohort row and missingness reason before seeing weather values. Obtain actual venue IDs and current roof/neutral evidence independently of the existing strategy's game-day gating; retain unsupported venues as missing. Do not infer a stadium from the home team. Kickoff or venue changes create a new context version.
2. Fetch each eligible `(venue, explicit run)` once as a full-run response. Require the returned valid-time range to cover every requested game hour; then select all four hourly wind values plus kickoff temperature/RH per game locally. Store units, native/interpolated temporal-resolution flag, source product, requested run, all valid times, lead hours and actual receipt. Compute the existing scalar-wind four-hour mean only as a descriptive archived field; do not classify, rank or test thresholds in this collector.
3. Fetch the selected DraftKings/FanDuel pairs after weather, using the seven-day frozen cohort. Preserve exact decimal prices, same-book/same-line pairing, provider/event IDs, market timestamps and distinct quote receipt. Join by verified home/away identity and kickoff, not text proximity alone. Link the weather and quote receipts and store their actual time gap; they are not simultaneous. Leave unmatched quotes and unavailable weather explicit.
4. Save original response bytes, SHA-256, sanitized request parameters, request start/end UTC, HTTP status and available `Date`/`ETag`/`Last-Modified`/content-type headers. Store parsed tables separately with parser version, code/coordinate-catalog hashes, parent body hash and immutable run manifest. Never place a credential-bearing query URL in public receipts. Preserve failed requests, changed same-run responses, and the earliest successful receipt instead of overwriting them.

Suggested storage is `data/runtime/weather_revisions/{body_hash}.json.gz` plus a small per-run manifest referencing quote/context/weather hashes. Keep its ledger separate from model forecasts and positions. A future comparison must use only snapshots received before its declared decision time; weather fetched after an earlier quote cannot be retroactively paired as information available at that quote.

**Bounded next step and cost.** Implement offline parser/timing tests, then a seven-day collection-only pilot at 01:17/07:17/13:17/19:17 UTC. These four slots cover explicit 18/00/06/12 UTC cycles with over seven hours of initialization lag; missed jobs remain missing. Leave both existing workflows' strategy behavior intact. At most 100 catalog venues × four runs gives roughly 400 single-location forecast calls/day; mature fixed-lead comparator captures add at most 150/day under the game cap if cached once per game. An eight-day, three-variable request is within the documented one-call dimensions. Four seven-day quote batches cost at most 68 HTTP requests/day under the existing ten-event batch implementation; the current subscription's remaining allowance must be checked separately. No book selection or subscription change is required.

Open-Meteo lists these APIs on free noncommercial access, with 600 calls/minute, 5,000/hour and 10,000/day limits and no uptime guarantee. Multi-location batching reduces HTTP overhead but should not be assumed to reduce billable location counts. Commercial use requires the corresponding service entitlement; no subscription was purchased or changed. Retain Open-Meteo/source attribution when publishing captured data. [Current pricing, call accounting and licensing](https://open-meteo.com/en/pricing). Actual storage and response latency should be measured during the bounded pilot, rather than estimated from unrelated historical GRIB downloads.

The pilot would establish whether we can observe forecast revisions and contemporaneous price movement with usable coverage. Venue coverage, provider processing, forecast receipt gaps and two-book market coverage remain limitations. It would supply prospective inputs for a separately frozen future experiment, not evidence of profitability by itself.

**Post-assessment endpoint preflight.** The primary agent made bounded schema requests for frozen venue ID `3793`, coordinates `41.65838893680983, -91.55147552490234`, model `gfs_global`, requested run `2026-09-08T18:00`. The original bodies and receipts are preserved in the ignored local directory `model/data/raw/weather_revisions_preflight/`. These are availability/schema checks, not weather-rule classifications.

| Request | Receipt UTC | Result |
|---|---|---|
| Original example with `start_hour`/`end_hour` | 2026-09-09 02:54:28.223353 | HTTP 400, 64 bytes; provider reason: `Parameter 'start_hour' must not be set` |
| Same request without either range parameter | 2026-09-09 02:55:13.114389 | HTTP 200, 5,609 bytes; 168 hourly times from September 8 18:00 through September 15 17:00 UTC; all three variable arrays and °F/%/mp/h units present |
| Same full-run request with `forecast_days=8` | 2026-09-09 02:56:12.226092 | HTTP 200, 6,355 bytes; 192 hourly times from September 8 18:00 through September 16 17:00 UTC; same variable schema and units |

Body SHA-256 values, respectively:

```text
803f5434208860e7155eaae49fd24d3921bcf731033ae1dfc7b24bd87b593ebc
a488b4042afb3e15f16664b9a2d743a2924f500d6d871b1dd54e34019426dedc
408f1cce715f6ea415efc893803ed548d8172f7a0fd8db198fd031a476ba0de7
```

Both successful responses cover the sampled game's four required hours. The default 168-hour horizon starts at initialization and is insufficient for a blanket seven-days-from-receipt cohort. The verified `forecast_days=8` response reaches initialization +191 hours. Under the proposed cycle-selection rule, initialization is 6 to less than 12 hours before capture; a seven-day cohort plus three further wind hours requires less than 183 hours. Thus the verified eight-day range has enough nominal horizon. Each response still needs explicit per-game time/variable completeness checks; one venue/run does not establish universal service coverage. A schema-only [public preflight receipt](weather_revision_endpoint_preflight.json) preserves the three request/response checks without forecast values.

The JSON body does **not** echo an independently verifiable initialization field. The retained requested `run`, endpoint contract and first valid time agree, but do not independently certify original model vintage. The actual receipt proves what this endpoint returned at that time; it does not certify that a forecast was available before that receipt. Original GRIB initialization metadata would be stronger corroboration if a later, separately scoped archive required it.
