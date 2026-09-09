# Season weather revision collection protocol

Protocol ID: `weather-revision-season-collection-v1`. This separate continuation profile is written before its first permitted capture. It does not extend or relabel the [seven-day pilot](WEATHER_REVISION_CAPTURE_PROTOCOL.md), whose original window and 28 scheduled slots remain fixed. Historical experiments and their frozen protocols remain unchanged.

## Window and schedule

Use explicit collector profile `season` for actual capture starts in **[2026-09-16 03:00:00 UTC, 2027-02-01 03:00:00 UTC)**. The start is inclusive and the end exclusive. Scheduled slots remain **01:17, 07:17, 13:17, and 19:17 UTC**: 552 slots over this 138-day window, beginning September 16 at 07:17 and ending February 1 at 01:17. Manual captures are separately identified by actual invocation and receipt times.

The default profile remains `pilot`, admitted only in its original September 9–16 window. Neither profile automatically switches to the other. A season invocation before or after its own window archives `outside_season` and makes zero HTTP requests; default pilot skips retain `outside_pilot`. Common in-window statuses remain `ok`, `partial`, `failed`, and `no_games` under the existing capture schema. A run admitted before its window ends may finish the already frozen bounded cohort afterward, retaining every actual receipt time.

Delays, missed slots, and manual attempts remain visible. Use actual start time for all eligibility and forecast initialization calculations. Do not backdate a delayed or replacement run, create a retrospective receipt, or claim a missed slot was observed. Scheduling is best effort and is managed separately from this collector profile.

## Unchanged collection contract

All source, cohort, quote, forecast, cache, and failure rules in the original pilot protocol apply unchanged:

- Freeze the official ESPN group-80 pregame cohort before observing weather or book coverage, with `capture_start < kickoff <= capture_start + 7 days`; sort by kickoff then numeric game ID and cap at 150. Keep unsupported contexts and failures in denominators.
- Require exact event/team/kickoff identity, actual venue, confirmed outdoor/non-neutral context, and the original frozen 100-venue catalog. A changed context retains a separate identity and history.
- Request the exact Open-Meteo GFS Single Runs initialization `floor_to_6h(capture_start − 6 hours)` with eight forecast days, the same three variables, units, coordinate options, strict parser, and four exact game hours. Preserve source-vintage limitations and the interpolation flag beyond 120-hour lead. Fetch Single Runs anew across captures; deduplicate only within one capture.
- Request the previous-day-two comparator only after the original maturity gate. Reuse only the earliest valid mature observation for the exact context, preserving its original request and receipt. Do not manufacture a new receipt or substitute another product/cycle after failure.
- Complete weather acquisition, then recheck official context, then request fresh DraftKings/FanDuel main full-game total pairs. Preserve exact decimal prices, source timestamps, and actual receipts. A quote association must follow both weather and final context recheck. Keep independent quote coverage and missingness visible.
- Keep the existing four-worker bound, per-capture source budgets, no automatic retry or redirect, maximum 17 Odds API IO requests, and verified quota/headroom gates with 20-request reserve. No new API keys, books, purchases, subscription changes, or assumed quota are introduced. The separate movement-schema probe's operational correction does not amend these collection gates.

This profile collects descriptive observations only. It performs no weather-rule classification, outcome join, signal test, model fitting, threshold selection, return/EV calculation, pick publication, or candidate promotion. Existing live weather and betting policies remain unchanged. Any subsequent analysis requires its own recorded plan.

## Manifest and archive compatibility

Every newly written capture manifest records `collection_profile`, `collection_protocol_id`, `collection_protocol_file`, `collection_window {start_inclusive, end_exclusive}`, and `scheduled_utc_slots`, alongside the existing schema, code version, actual times, run identity, counts, and failures. For admitted season runs, provenance hashes include both this protocol and the unchanged original protocol, plus the collector, parser, HTTP archive, team aliases, and frozen venue catalog. Source-provenance failure occurs before HTTP collection. Collector version `weather-revision-collector-v2` introduces profiles; it does not reinterpret earlier measurements.

Both profiles use the existing `data/runtime/weather_revisions` archive, immutable run/cohort files, content-addressed original response bodies, and receipts. Matching mature comparator receipts can be reused across the boundary; they keep the original capture provenance and timing. New explicit-run requests always receive distinct actual receipts even if identical bytes share a blob. Existing invocation identities cannot overwrite earlier files.

Old manifests without `collection_profile` remain original pilot captures and are not edited to add fields. Operational reporting must distinguish profiles, using that historical interpretation only for legacy pilot records, and retain their original source hashes and recorded Git commit. Report the pilot's 28 slots and continuation's 552 slots separately; a season capture is not a pilot completion or a replacement for a missed pilot slot.

From the repository root, an explicit season invocation is:

```sh
PYTHONPATH=model python -m ncaaf_model.weather_revision_collector --root model --profile season
```

Collection is permitted only inside the stated window. Operational reports cover actual attempts, coverage, missingness, timing gaps, request usage, and storage; they make no profitability claim.
