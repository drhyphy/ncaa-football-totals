# ACC availability archive: bounded public-source preflight

**The public archive is accessible as structured JSON. Historical publication times and the current-report schema remain unverified.** On September 9, 2026, one archive response contained 83 player/game rows, six team/date/opponent groups and four reporting-phase columns. This establishes a concrete source for further evaluation, not a predictive or betting edge.

The preflight used **six successful public HTTP requests**, no redirects, and one prior sandbox DNS failure that sent no HTTP request. Only one availability-data payload was fetched; a separate 16-byte eligibility response confirmed public access. No credentials or cookies were sent, no roster or outcome data were joined, and no model, automation or live policy changed.

## Accessible delivery path

1. The [official ACC football archive](https://theacc.com/sports/2025/8/28/availability-reporting-football-archive.aspx) embeds [HD Intelligence's public ACC archive](https://app.hdintelligence.com/?source=ACC&sport=Football&conf=ACC&type=archive). The embedded source is `app.hdintelligence.com` in the inspected bytes.
2. Its [application script](https://app.hdintelligence.com/assets/index-CfQ2iXoZ.js) makes a public-view eligibility request. Repeating that documented request, with ACC/Football/archive and the actual referring archive URL, returned HTTP 200 and `{"public":true}`.
3. The linked [archive component](https://app.hdintelligence.com/assets/ArchiveScreen-Dlx_BrNt.js) explicitly selects `/api/get-archive-public` for its public view. That endpoint returned HTTP 200 without authentication when requested as shown below. The nonpublic and legacy endpoints also mentioned in the script were not called.

```text
POST https://app.hdintelligence.com/api/get-archive-public
Content-Type: application/json
{"sport":"Football","organization":"ACC","conference":"ACC"}
```

The official archive also links a [current report page](https://theacc.com/sports/2025/8/28/availability-reporting-football.aspx), and the application lists a separate lazy `PublishScreen` component. Neither current-report body nor its data endpoint was fetched within this budget. The archive's successful response must not be represented as proof of the current-report schema.

## Actual payload and identity fields

The response has `data`, `loaded`, `report_days` and conference presentation fields: font settings and a base64 logo. `loaded` is true. Every one of the 83 data rows has the same eleven string-valued fields:

| Fields | Observed meaning and limit |
|---|---|
| `Week` | A date-only RFC 1123 string at midnight GMT. The client labels it “Date” and formats it in UTC; it is not a report-issue timestamp or exact kickoff. |
| `Team`, `TeamDisplay` | Source and display labels; for example, Florida St. maps to Florida State. No stable team ID. |
| `Opponent`, `OpponentDisplay` | Opponent labels, including venue prefixes such as `vs.`. No stable game ID. |
| `Number`, `Player` | Jersey label such as `#0` and player name. No stable player ID, explicit position, roster-season key or starting-role guarantee. |
| `Initial`, `Update 1`, `Update 2`, `Game Day` | Presented status values for four phases; no individual observation/publication time or revision identifier. |

The three represented date values are August 29, September 4 and September 7, 2026. Team display labels are Florida State, Miami, NC State, SMU, Stanford and Virginia. No 2025 records appear in this one response; no claim about complete season/history coverage follows. Names and jersey numbers require an independently archived roster match before identifying quarterbacks or roles.

Non-game-day cells contain Available, Probable, Questionable, Doubtful and Out. Game-day cells contain Available, Game Time Decision and Out. These are source categories, not empirically calibrated participation probabilities. Counts, exact keys and raw-body hashes are retained in the [machine-readable source facts](ACC_AVAILABILITY_PREFLIGHT.json); individual player rows are not copied into this public report.

## Timing and policy limits

The actual **2026 payload has four phase columns and includes Doubtful**, whereas the previously reviewed [2025 ACC announcement](https://admin.theacc.com/news/2025/7/22/general-acc-announces-new-safety-and-well-being-policies-for-2025-26-year.aspx) described an initial report two days before kickoff, a day-before update and a final report, without that additional category. This is a material discrepancy to resolve against the applicable 2026 policy. This preflight does not infer new deadlines, category percentages or the meaning of the extra update column.

The payload contains no issue clock, publication timezone, first-published timestamp, last-modified timestamp per record, version sequence or correction history. It is an aggregate snapshot of presented phase cells. A server response date cannot establish when those cells first became public, and phase labels do not prove that every earlier revision is preserved. The source sets `max-age=60`, `s-maxage=60`, `stale-while-revalidate=60` and `stale-if-error=300`, so a fresh receipt can still contain cached content.

The archive response was received during the explicitly logged **07:21:19 UTC** request interval at one-second clock resolution; its exact body has SHA-256 `b2db7541ff5473f1fca4f647d3adc7ce476e2c0e0c62e495c70c6e7d793c1060`. Original bytes and headers for all six responses, UTC capture metadata and independent file hashes are saved under the ignored `model/data/raw/availability_preflight/acc/` directory. For the first landing-page request only, explicit HTTP start/end times were not logged; its filesystem last-write UTC is preserved as a receipt proxy and labeled accordingly. Later requests have explicit host UTC observations. Public metadata excludes cookies and player rows.

Prospective use would require retaining each changed body and its actual first receipt, separately preserving source dates, verifying the current-report delivery and 2026 policy, and resolving player identities from prior roster information. No collection schedule or fitting experiment was started here.
