# Big Ten availability delivery preflight

Access date: **2026-09-09**. Six public GET requests, no redirects, no credentials, one empty report-endpoint response and **zero nonempty player-report bodies**. No roster fetches, labels, fits or automation. Original response bytes and headers are retained under ignored `model/data/raw/availability_preflight/bigten/`; [the source-facts JSON](BIGTEN_AVAILABILITY_PREFLIGHT.json) records every body/header/receipt SHA256 and verifies their sizes.

**Result:** the official 2026 page links to a publicly accessible report application and read endpoint. The endpoint returned **HTTP 200 with `{}` plus newline**, not a player report. This verifies the route but does not validate a populated 2026 schema, historical versions or publication timestamps.

## Verified delivery chain

The [Big Ten football index](https://bigten.org/fb/) links to [2026 Football Availability Reports, article 60323](https://bigten.org/fb/article/60323/). That article embeds both [current reports](https://confinjrepxyz.hdintelligence-app.com?source=B10reports) and [the archive](https://confinjrepxyz.hdintelligence-app.com?source=B10archive) from HD Intelligence. Only the current embed was fetched.

Its HTML loads [main.26907188.js](https://confinjrepxyz.hdintelligence-app.com/static/js/main.26907188.js). The public Big Ten report component is [267.9c3e3bbc.chunk.js](https://confinjrepxyz.hdintelligence-app.com/static/js/267.9c3e3bbc.chunk.js), which performs an unauthenticated [GET to its published-data endpoint](https://confinjrepxyz.hdintelligence-app.com/api/53PV8lI$h8!0). No login, draft/admin endpoint, identity substitution or fabricated referrer was used.

| Request | Archived body | HTTP | Bytes | Receipt UTC |
|---:|---|---:|---:|---|
| 1 | `01_index.html` | 200 | 2,069,671 | 2026-09-09T07:18:01Z |
| 2 | `02_current.html` | 200 | 1,645,576 | 2026-09-09T07:18:31Z |
| 3 | `03_app.html` | 200 | 1,099 | 2026-09-09T07:19:02Z |
| 4 | `04_bundle.js` | 200 | 258,471 | 2026-09-09T07:19:25Z |
| 5 | `05_publish_chunk.js` | 200 | 6,615 | 2026-09-09T07:20:17Z |
| 6 | `06_published.json` | 200 | 3 | 2026-09-09T07:20:39Z |

The final body's SHA256 is `ca3d163bab055381827226140568f3bef7eaac187cebd76878e0b63e9e442356`. Request/receipt times are whole-second UTC brackets around the actual fetch, not inferred from filenames or page dates.

## Format and identity: code evidence only

The fetched public component expects an object whose values are reports. Each report contains `games` and `footer`; team records use `teamName`, `teamColor`, `teamImage`, and `rows`; player rows use `name`, `status`, and `exempt_status`. Footer fields are `date`, `time`, `location`, and `networkImage`. The UI takes the first 18 top-level values. **No populated record was returned**, so these are client expectations rather than a certified response schema. Stable game/team/player IDs might exist in additional fields; none is referenced by this display code.

The component removes the first space-separated name token for display. A future parser should retain the raw name string and separately validate any jersey/position/identity interpretation; the current sample does not establish QB positions or starter roles. Matching to a canonical schedule needs both team identities and a verified kickoff. A missing team/report must remain unknown, not “all available.”

The retrieved component still hardcodes **December 30–31, 2025**, filters Out/Questionable plus `exempt_status == Out`, and does not visibly implement the complete newly announced 2026 categories. That is a concrete compatibility concern, not proof the service will fail when reporting begins. The [official 2026 policy](https://bigten.org/fb/article/60284/) identifies September 19 as its first covered conference games, with initial reports three days earlier. The empty September 9 response is therefore not evidence that those reports are missing or late.

## Time semantics and practical next step

The article's JSON-LD supplies page dates September 5/6, 2026; these are landing-page metadata. The app's HTTP Last-Modified and the report endpoint's HTTP Date likewise do not identify a player-status publication. The component references no report issue/version timestamp. Its footer time is displayed with a fixed one-hour addition and an ET label alongside date/location/network, indicating game context; it does not prove the source timezone or provide a safe UTC conversion. Do not reuse it as issue time.

A separately bounded check after the first required 2026 window—September 16 at 20:00 America/New_York—can validate one nonempty report, actual identity fields, complete status categories, and any issue-time/version fields. Preserve original bytes and receipt time even if the source provides its own timestamp. The archive embed and revision-list semantics remain untested. No historical timing panel or prospective collection readiness is claimed from this preflight.
