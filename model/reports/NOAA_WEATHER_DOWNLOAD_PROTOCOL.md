# NOAA inventory and immutable range-download stages

The [frozen request plan](NOAA_WEATHER_REQUEST_PLAN.md) selects identities, fields, forecast timing and interpolation before weather classification. The separate [downloader](../ncaaf_model/noaa_weather_fetch.py) implements two resumable stages. It does not extract stadium weather values, classify the weather rule, join final scores, or change live policy.

From the repository root, with project dependencies available:

```bash
PYTHONPATH=model python -m ncaaf_model.noaa_weather_fetch --root model --inventory --workers 4
PYTHONPATH=model python -m ncaaf_model.noaa_weather_fetch --root model --verify-inventory
```

The inventory stage retrieves only the frozen object headers and small `.idx` files. It records original response headers, URLs, receipt time, ETag, Last-Modified and hashes. It requires each forecast object's Last-Modified to precede every affected game's decision. Exact field/height, initialization and lead must match; ambiguous selectors and malformed offsets fail validation. Terminal missing/late/invalid sources become explicit exclusions, with no replacement forecast cycle. Network errors remain incomplete and are retained in attempt logs.

When every object is accounted for, `model/data/raw/noaa_weather_research/inventory_plan.json` freezes exact byte ranges, total bytes, source exclusions, code snapshot and inventory-file hashes. Check `model/reports/noaa_weather_inventory_status.json` for progress and the final exact download budget before the next stage. An incomplete stage exits with status 2; rerunning resumes successful immutable records. `--max-objects N` bounds a single inventory run.

```bash
PYTHONPATH=model python -m ncaaf_model.noaa_weather_fetch --root model --fetch --workers 4
PYTHONPATH=model python -m ncaaf_model.noaa_weather_fetch --root model --verify-fetch
```

`--max-ranges N` optionally bounds a download run. At most four workers operate, without a fixed artificial delay. Each field request sends the exact `Range`, original strong `If-Match` ETag and `Accept-Encoding: identity`. Before consuming a body it requires HTTP 206, exact Content-Range/object size, expected Content-Length, unchanged ETag and matching admissible Last-Modified. HTTP 200 is closed unread, preventing accidental full-object downloads.

Each downloaded field must have GRIB2 magic, encoded message length, terminator, initialization, forecast hours and time units, valid date/time, variable, height, physical units, 0.25° global geometry and scan order matching the fixed request. The local validation runtime used ecCodes Python/API **2.47.3**. ecCodes is required for field downloads; inventory access needs only the project's existing Python dependencies. Cached field length and SHA-256 are rechecked on resumption; conflicting bytes are never overwritten.

`model/data/raw/noaa_weather_research/fields/{range_id}.grib2` stores each original message. The adjacent JSON records the range-plan hash, source response headers, field hash and decoded header metadata. A complete `fetch_manifest.json` pins every field and receipt file; the later extractor must call `read_fetch_manifest(root)` and verify these hashes before classifying weather. The raw files remain outside Git, with approximately 3.14 GB estimated in the original request plan and an exact amount determined by inventories.

**Timing is an availability proxy, not certified publication.** AWS states that Last-Modified for a multipart upload can refer to upload initiation rather than completion. The pipeline retains a multipart ETag-pattern indicator and all headers, keeps the fixed initialization buffer and Last-Modified gate, and adds no automatic multipart exclusion. These fields do not establish exact historical dissemination or bookmaker acceptance. [AWS object metadata semantics](https://docs.aws.amazon.com/AmazonS3/latest/userguide/UsingMetadata.html).

Offline verification:

```bash
cd model
python -m pytest tests/test_noaa_weather_fetch.py tests/test_noaa_weather_research.py -q
```

The 33 passing tests cover outcome-free venue selection, immutable plans, timing and interpolation metadata, exact inventory selectors, missing/late sources, HTTP preconditions, rejection before body consumption, truncated/corrupted downloads, cache integrity, and ecCodes checks for all four field types. No bulk network operation was performed as part of these tests.
