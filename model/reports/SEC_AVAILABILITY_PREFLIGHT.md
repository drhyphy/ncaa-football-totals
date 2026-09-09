# SEC availability delivery preflight

Access date: **2026-09-09**. This report independently checks six existing root-executed HTTP captures; it makes **no new network requests**. The executor used an 8 MiB body limit and disabled redirects; none were followed. All six original body sizes and SHA256 hashes match their receipt records. Raw source bodies remain ignored under `model/data/raw/availability_preflight/sec/`; [the JSON inventory](SEC_AVAILABILITY_PREFLIGHT.json) publishes selected metadata and hashes only.

**Observed result:** the officially linked public current-report endpoint returned HTTP 200 with an empty JSON object plus newline, **three bytes**. No nonempty report was obtained. Current report semantics, player identity fields and issue timestamps therefore remain unverified. No labels, outcomes, fits, roster collection or production changes were involved.

## Delivery chain and receipts

The original [current-page HTML](https://www.secsports.com/fbreports) embeds `source=SECreports`; the [archive-page HTML](https://www.secsports.com/fbreports-archive) embeds `source=SECarchive`, both on `confinjrepxyz.hdintelligence-app.com`. These routes are present inside the pages' embedded CMS content even though a text browser had previously shown only a shell.

The fetched archive embed loads [main.26907188.js](https://confinjrepxyz.hdintelligence-app.com/static/js/main.26907188.js). Its exact existing Big Ten capture was reused offline: 258,471 bytes, SHA256 `601c89d6ed02259d2b738d99b324e0c776a19ab9490c03af64480a37fc9e70f3`. That reuse did not add a seventh SEC request. The [published component](https://confinjrepxyz.hdintelligence-app.com/static/js/368.27a1ca65.chunk.js) references the public [GET endpoint](https://confinjrepxyz.hdintelligence-app.com/api/PV8l!c$3C) which produced the empty response.

| Request | Original body | HTTP | Bytes | Request → receipt UTC |
|---:|---|---:|---:|---|
| 1 | `0001-archive.html` | 200 | 127,805 | 2026-09-09T07:18:45.010749+00:00 → 2026-09-09T07:18:45.544448+00:00 |
| 2 | `0002-current.html` | 200 | 127,635 | 2026-09-09T07:20:17.052532+00:00 → 2026-09-09T07:20:17.506423+00:00 |
| 3 | `0003-embed.html` | 200 | 1,099 | 2026-09-09T07:20:20.305442+00:00 → 2026-09-09T07:20:20.416196+00:00 |
| 4 | `0004-published.js` | 200 | 9,932 | 2026-09-09T07:23:03.654731+00:00 → 2026-09-09T07:23:03.754040+00:00 |
| 5 | `0005-archive.js` | 200 | 7,299 | 2026-09-09T07:23:05.514870+00:00 → 2026-09-09T07:23:05.586003+00:00 |
| 6 | `0006-current.json` | 200 | 3 | 2026-09-09T07:23:54.879900+00:00 → 2026-09-09T07:23:55.174328+00:00 |

The final body SHA256 is `ca3d163bab055381827226140568f3bef7eaac187cebd76878e0b63e9e442356`. Original receipt times record the actual fetch brackets; they were not inferred from filenames or page dates.

## What the captures establish—and leave open

The published component contains the heading **“No Games Until Fall 2026.”** This is an observed code string, not a certified delivery deadline, proof that reports are unavailable, or evidence of current-season readiness. An empty current object cannot establish the populated schema or why the object is empty.

The archive component was also captured. It references an additional client header; its name/value are omitted and were not reused. **No archive data endpoint was requested.** This preflight consequently makes no claim that historical data are inaccessible or that an archive revision history is complete.

Both landing pages contain `published_at = 2024-08-29T19:00:00.000000Z`. That is their CMS page-publication metadata, not a report release or player-status timestamp. HTTP Date and script Last-Modified headers also do not certify the timing of individual reports. No actual record validated a source issue time, timezone, release sequence, stable game/player identifier or status transition.

Before any collector or model uses this source, a separately bounded check should obtain one nonempty report through an officially linked route and validate its game/team/player identities, phase/status fields and timestamp meanings. Preserve original bytes and actual receipts. This evidence does not yet support a historical timing panel, a production parser or an edge claim.
