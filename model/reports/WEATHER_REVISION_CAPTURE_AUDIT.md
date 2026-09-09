# Weather revision collection: independent pre-capture audit

Scope: the fixed seven-day [collection protocol](WEATHER_REVISION_CAPTURE_PROTOCOL.md), collector, original-response archiver, forecast parser, status publisher and GitHub workflow. This audit concerns data integrity and operational coverage. It does not inspect game outcomes, classify a weather strategy, evaluate a forecast or betting return, or authorize a policy change. No bulk pilot request was made by the auditor.

## Finding

The reviewed implementation follows the collection design after the corrections below. No remaining material cohort, forecast-timing, quote-pairing or quota defect was found in the reviewed code. Synthetic checks and code review cannot establish actual endpoint coverage or successful scheduled operation. The first real capture still requires a separate receipt-level check; future missed or incomplete runs remain missing.

The pipeline writes separate research archives and collection status. It does not import a strategy evaluator, join outcomes, calculate EV, fit a model, or change any of the four registered policies, forecasts or paper positions. Collection success is not evidence of a profitable edge.

## Verified design and implementation

**Cohort and context.** `weather_revision_collector.parse_cohort` uses an official group-80 inventory, the actual start clock, strictly future pregame events through seven days, and kickoff/game-ID ordering. The 150-game cap and exclusions are archived before weather retrieval. Indoor, neutral, unknown or uncatalogued contexts remain in the capped denominator. Current official summary and venue receipts supply actual event/venue/roof evidence; the code does not infer a home stadium. Context hashes include kickoff, teams, venue, roof and neutral state. A successful empty official inventory and a failed or malformed inventory have different statuses.

**Forecast timing.** `weather_revision_weather` selects the UTC cycle at `floor_6h(capture_start - 6h)`. Single Runs requests specify GFS, eight forecast days and the frozen coordinates. Parsing requires the exact initialization-aligned 192-hour time axis, GMT/UTC metadata, correct units, aligned arrays, and the four required game hours. Only kickoff temperature/RH and all four winds are required numerical measurements. The four scalar winds are averaged arithmetically. Broad physical ingestion bounds and a 50-km returned-grid proximity bound are fixed integrity checks, not a weather strategy. The parser identifies hours beyond 120-hour run lead as potentially interpolated native three-hour output.

Every explicit-run receipt must be requested during its own capture. Identical venue/run requests may be deduplicated within that capture; the collector refetches across captures. The previous-day comparator alone can reuse an older receipt, after validating its original request and response against the fixed maturity time and exact context. An early receipt cannot acquire maturity retrospectively. The cached comparator is selected by the earliest validated successful receipt, retaining its original timestamps and body.

**Price sequence.** Weather acquisition finishes before the official summary recheck and new price requests. Weather associations require a successful unchanged recheck and a quote request following both weather-stage completion and that recheck receipt. The quote response must remain pregame. Exact normalized team identity, home/away orientation, kickoff and unambiguous provider event identity bind the two sources. Each quote request is parsed only against its own event batch. DraftKings and FanDuel remain the only permitted books. Same-book Over/Under prices share one finite integer or half-point line and a verified main full-game market. There is no favorable-price selection or earlier-quote substitution.

Weather coverage counts validated Single Runs responses; mature comparator coverage is separate. The two-book count does not imply weather coverage. The paired count requires weather and at least one subsequent valid same-book pair; the optional paired-two-book count is the stricter intersection. Final counters are reconstructed from retained rows even after a later-stage failure.

**Archive and quota.** `revision_archive` retains original unparsed response representation bytes after the HTTP client's content decoding, separately recording HTTP content headers and stored-body size. Content-addressed gzip bodies and receipts use temporary writes, flush/fsync and atomic link creation; existing files cannot be overwritten. Cached receipts are checked against their own digest/path and the decompressed body digest. Public requests omit credentials, permitted response headers exclude authentication/cookie headers, and detected credential echoes are hashed but withheld. Transport exception text is not published. Requests have timeouts and no automatic retries or redirects.

The actual account preflight returned a 100-request/hour header, rather than the generic documentation's 5,000. The collector's capped cohort implies at most two discovery calls plus 15 ten-event quote batches: 17 Odds API IO requests. A reported reserve at or below 20 stops collection; missing discovery allowance prevents quote batching. It verifies sufficient headroom for all required batches and stops on missing subsequent allowance or HTTP 401/403/429. These local checks cannot reserve capacity against unrelated concurrent clients.

**Failure publication.** `scripts/weather_revision_status.py` publishes a metadata allowlist and explicitly labels repeated game observations. Expected success requires a manifest from the current workflow run and attempt, starting after the workflow decision timestamp. A prior successful manifest cannot substitute for a skipped, cancelled or failed attempt. `.github/workflows/weather-revisions.yml` can archive and publish failure status after a collector or prerequisite failure; the deployment subsequently reports failure. It shares the existing publication concurrency group. The UTC window stops collection after the pilot; a final status transition is published without further forecast/quote retrieval.

## Corrections identified during this audit

All changes were coordinated with the owning authors before the first bulk capture:

1. Replace non-atomic archive creation with temporary/fsynced atomic creation, and validate the cached receipt's own digest rather than only its body.
2. Reject reuse of a previous capture's Single Runs receipt; retain mature cached comparator receipts without redating them.
3. Require only the specified kickoff temperature/RH values, avoiding coverage loss from unused later null values; retain strict validation of all four winds.
4. Normalize numeric total-line representations before duplicate detection, so conflicting `54`, `54.0` and `"54.0"` pairs are all rejected.
5. Restrict each quote response to that request's event batch and explicitly enforce recheck-receipt-before-quote-request ordering.
6. Stop after a selected-books response reports quota at or below the reserve, retain counters after late-stage failures, handle malformed shapes explicitly, and hash the team-normalization dependency.
7. Correct any-book versus two-book status arithmetic, require the current workflow attempt's manifest, and permit publication of failure status after prerequisite failure.

## Verification and limits

The auditor independently ran **58 forecast-parser tests** and **15 status tests**, all passing. Explicit `PYTHONPATH=model` is required in the local shared Python environment to avoid importing an unrelated installed project. The source authors' collector, transport and end-to-end synthetic checks are reported separately; this document does not treat them as actual network observations.

The provider's requested-run contract, first valid hour and archived request identify the intended vintage, but the JSON does not independently certify initialization or first public dissemination. Actual response receipts provide availability evidence at receipt time only. Default provider grid/elevation processing, native long-lead interpolation, current roof evidence, API coverage, quote latency, scheduler delays and missingness can constrain any later study. The original body and version hashes allow those constraints to be examined; they do not remove them. A later analysis needs a separate frozen plan and cannot treat these operational checks as model validation.
