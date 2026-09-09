# ACC current report and 2026 policy: bounded preflight

**The current public endpoint works, but the inspected report is pending and contains no player-status rows.** The current ACC policy summary also resolves the earlier archive discrepancy: football reporting now starts three nights before the game, and Doubtful is a recognized category. No predictive test, roster/outcome join or automation was performed.

Six additional public HTTP requests returned 200, without redirects or credentials. Exactly one actual current-report payload was fetched. All requests have explicit host UTC start/end observations at one-second resolution, original response bytes, headers and SHA-256 hashes under the ignored `model/data/raw/availability_preflight/acc_current/` directory. The [earlier archive preflight](ACC_AVAILABILITY_PREFLIGHT.md) remains preserved separately.

## Public route and actual response

The [official current-report page](https://theacc.com/sports/2025/8/28/availability-reporting-football.aspx) embeds [HD Intelligence with `type=report`](https://app.hdintelligence.com/?source=ACC&sport=Football&conf=ACC&type=report). The new embed bytes match the earlier capture and reference the same application-bundle URL, so that captured bundle was reused. Its documented public-view eligibility request returned `{"public":true}` for the current ACC football report.

The linked [PublishScreen component](https://app.hdintelligence.com/assets/PublishScreen-Dg-G25B2.js) explicitly chooses the public endpoint below. The nonpublic branch was not called.

```text
POST https://app.hdintelligence.com/api/get-publish-public
Content-Type: application/json
{"sport":"Football","organization":"ACC","conference":"ACC"}
```

At **07:44:20 UTC on September 9, 2026**, the endpoint returned a 1,117,075-byte JSON object with one report, keyed `1182`. Much of its size is embedded presentation imagery. The numeric-looking key is opaque; it is not verified as a stable game or report-version ID.

| Actual fields | Observed value or structure |
|---|---|
| `ReportType` | `Report Pending` |
| `publishDate`, `publishDayOfWeek`, `postedTime` | September 9, 2026; Wednesday; `20:00:00` |
| `conferenceTimeZone` | Literal label `ET`, without an IANA timezone or numeric UTC offset |
| `games` | Two team blocks: California and Syracuse, each with source/display team names and an empty `rows` array |
| `footer` | Date September 12, 2026; time `15:30:00`; location Syracuse, NY; additional image fields |
| `statusReportOrder` | Category presentation order, including Out, Out for the first half, Doubtful, Questionable, Probable and Available |

The payload therefore contains a **future matchup date**, established only from its own metadata; no schedule was fetched to reconcile it. A populated current player-row schema was not verified because both arrays are empty. Category-order metadata is not a list of actual player statuses.

The pending flag is essential: these empty rows do **not** mean all players are available. Likewise, the pending September 9/20:00/ET fields do not establish that a completed report has already been issued. They must remain separate from actual HTTP receipt time. First publication, revisions and detailed rendering-timezone semantics remain unverified. The endpoint permits 60-second caching and stale responses under its stated cache policy.

## Current policy resolves the archive mismatch

The [ACC reporting home page](https://theacc.com/sports/2025/8/28/availability-reporting.aspx) now describes the policy's second year. Its football summary requires daily reports beginning three nights before a conference game and a game-day report two hours before scheduled kickoff. It lists Available, Probable, Questionable, Doubtful and Out before game day; the game-day list is Available, Game Time Decision and Out. This explains the four phase columns and Doubtful values observed in the 2026 archive, which differed from the previously reviewed 2025 launch announcement.

The page links a [2026–27 policy PDF](https://theacc.com/documents/2026/8/24//ACC_Availability_Reporting_Policy_2026_27.pdf?id=2940). Its body was not fetched within this request budget. The exact evening deadline and exception rules, including first-half designations, are therefore not established here. The summary's percentage labels are policy definitions, not measured probabilities.

The current-payload SHA-256 is `7f16dceda02779e726ddb15f2392d2a65543e90ac2b02789127e09106cc4b6fc`. The [machine-readable facts and receipt manifest](ACC_CURRENT_AVAILABILITY_PREFLIGHT.json) bind all six response bodies and original headers, plus the reused application bundle. Public metadata excludes cookies and image bodies. No completed player-status report, historical release-time guarantee or betting edge is claimed.
