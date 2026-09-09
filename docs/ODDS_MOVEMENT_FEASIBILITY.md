# Odds-API.io movement-history feasibility

Documentation-only review, September 9, 2026 UTC. **DraftKings and FanDuel are explicitly supported history books, but the published contract does not yet establish a complete, timestamp-interpretable April-onward NCAA totals archive.** This could support a separate retrospective price-history study after validation; it cannot currently certify prices available when each historical GFS run became usable. No credentials were read, account/data API calls made, subscriptions changed, or pilot/live policies modified.

This review checked the existing [2026 quote provenance report](../model/reports/ARCHIVED_2026_QUOTES.md), [weather revision feasibility](../model/reports/WEATHER_REVISION_CAPTURE_FEASIBILITY.md), and [frozen collection protocol](../model/reports/WEATHER_REVISION_CAPTURE_PROTOCOL.md). Those cover locally retained price receipts and prospective collection, rather than a completed movements-endpoint audit.

The documented request is **`GET https://api.odds-api.io/v3/odds/movements`**:

| Query parameter | Contract / proposed NCAA use |
|---|---|
| `apiKey` | Required authentication; never include in a public request URL. |
| `eventId` | Required provider event ID. |
| `bookmaker` | Required single bookmaker: `DraftKings` or `FanDuel`. Both appear in the explicitly recorded subset. |
| `market` | Required market name; use `Totals` for the intended full-event total. |
| `marketLine` | String containing the particular total, such as `47.5`; documentation exempts moneyline markets from this parameter. |

One response describes one event/book/market/line with `opening`, `movements`, and `latest`. The reference supplies no date filter, pagination, historical-line enumeration, or retention/start-date guarantee. An unsupported bookmaker returns 404; a supported name does not guarantee every event has data. [Movement endpoint reference](https://docs.odds-api.io/api-reference/odds/get-odds-movements).

The current connected-account [existing quota receipt](../model/reports/weather_revision_quota_preflight.json) records selected DraftKings/FanDuel and a 100-request hourly limit. The public free-plan listing advertises two recreational books, 100 requests/hour and 500/day. **Movement access on this account remains untested**: the movement reference does not explicitly state a free-plan entitlement or a separate purchase requirement. No bookmaker change is indicated by documented coverage. [Provider plan listing](https://odds-api.io/#pricing).

| Required interpretation | Documentation finding and consequence |
|---|---|
| Full-game totals | The general odds guide distinguishes `Totals` from `Totals HT`, `Totals 2H`, and team totals. Availability varies by sport. A real NCAA response must still confirm the exact period and settlement rules, including overtime. [Odds guide](https://docs.odds-api.io/guides/fetching-odds). |
| Over/Under prices | The movements OpenAPI schema lists generic `home`, `away`, `draw`, `hdp`, `max`, and `timestamp`, rather than explicit Over/Under fields. Do not guess a home→Over mapping. |
| Timestamp units and meaning | Movement `timestamp` is only an integer in that schema. Seconds versus milliseconds, source-change versus provider-ingestion time, revisions, and ordering guarantees are unspecified. |
| Pregame / in-play / suspension | Movement rows expose no documented state, period, suspension/deletion marker, or pregame filter. The separate market catalogue has `period`, `prematch`, and `live` capabilities; these do not classify each historical tick. Comparing an unknown timestamp to scheduled kickoff alone cannot establish its role. |

The three schema findings above are from the [current OpenAPI document](https://docs.odds-api.io/api-reference/openapi.json). The [official SDK type](https://github.com/odds-api-io/odds-api-node/blob/main/src/types.ts) and [tracking example](https://github.com/odds-api-io/odds-api-node/blob/main/examples/odds-tracking.ts) present mutually different movement shapes and do not resolve those gaps. The REST response needs direct schema validation before analysis.

**Closing history is a different product.** The historical guide describes a final pre-start quote and says closing odds begin in December 2025. `historical/events` takes sport, league, `from`/`to` (at most 31 days); `historical/odds` takes an event ID and books, with optional markets. Both are documented for free and paid plans. Bulk `historical/closing-lines` is paid. These closing snapshots cannot recover the earlier price at each GFS release. The guide's approximately 90% March-2026-onward coverage statement concerns resolved participant IDs; it is not a movement-retention promise. [Historical guide](https://docs.odds-api.io/guides/historical).

For an April-2026-onward reconstruction, the unresolved requirements are old settled-event movement availability, all relevant historical lines, side mapping, temporal units, pregame identification, and interruption/withdrawal handling. A tick before a chosen decision time does not establish uninterrupted availability until that time. Searching only a game's final line would also condition the sample on later market behavior. Full-run weather availability begins in April in the separate source assessment, but initialization is not a receipt or publication time; the two archives do not automatically share an information cutoff.

A bounded future validation could check one already captured event and one older settled event for both selected books, retaining raw responses and comparing movements against the project's existing actual price receipts. First resolve the schema and retention questions without looking at profitability. Request cost scales with the number of book/event/line combinations, so this belongs outside the frozen pilot and its quota budget. If successful, label the output **provider-reported historical prices retrieved later**, retaining both historical source time and present receipt time. It would remain separate from prices actually received prospectively, and from executable sportsbook acceptance.
