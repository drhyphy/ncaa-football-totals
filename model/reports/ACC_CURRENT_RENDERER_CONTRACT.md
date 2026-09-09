# ACC current-report renderer: source contract

**A strict parser for exact `Initial` and `Game Day` reports can be frozen from the public renderer now.** The source defines `name`/`status` rows and an explicit team “none to report” condition. It does not expose a separate completed-report boolean, an exhaustive list of update-phase strings or a canonical player crosswalk. This is source evidence for a proposed parser, not parser/model activation.

On September 9, 2026, one additional public asset was retrieved: [routing-BOw04HdD.js](https://app.hdintelligence.com/assets/routing-BOw04HdD.js), directly imported by the already retained [PublishScreen wrapper](https://app.hdintelligence.com/assets/PublishScreen-Dg-G25B2.js). Its router explicitly maps ACC Football to component `Ze`; that component and its helpers are contained in the same file. No further assets, private routes, credentials or target outcomes were accessed, and the JavaScript was inspected as text without execution.

## Verified rules

All references below are to physical **line 1** of the original routing asset. Offsets are zero-based UTF-8 byte positions; the [JSON evidence inventory](ACC_CURRENT_RENDERER_CONTRACT.json) preserves exact fragments and source hashes.

| Question | Actual ACC Football behavior | Byte offset |
|---|---|---:|
| Correct renderer | `ACC.Football` maps to `Ze` | 139719 |
| Player fields | Reads `games[].rows[].name` and `.status`; prints names as supplied | 23259, 24793 |
| Pending / posted | Only literal `Report Pending` is labeled expected; every other string is labeled posted | 26471 |
| Recognized study phases | Exact branches for `Initial` and `Game Day` | 21058, 21233 |
| Team none declaration | Nonempty rows, with **every** status exactly `Available` | 23259, 23963, 24131 |
| Full versus partial Out | `Out` and `Out - (1st Half)` share the visual OUT heading | 20058 |

The remaining named categories are **Doubtful, Questionable, Probable and Game Time Decision**. Available rows are not printed as a named category. Unknown statuses can disappear from the fixed category display; a research parser must retain them as unknown rather than copy that omission. The category list itself is not restricted by report phase.

There is no player ID, jersey-based lookup or name normalization in this component. Its React row key is merely the row index. There is also no promise that an Available-only row contains a real player's name rather than a display placeholder. The explicit team none declaration can be interpreted without inventing that identity. **Empty arrays do not satisfy it.** A pending report cannot become usable merely because it contains Available rows.

The phase-heading helper uses weekday fallbacks for other strings, and its Wednesday check precedes the `Game Day` branch. Use the raw phase value, not rendered weekday text or an image. The renderer's 18-report display cap is a UI limit, not an endpoint completeness guarantee. Team block order does not establish home/away. Its time formatter appends `ET`; it does not certify first publication or convert verified game-location deadlines.

## What can be fixed before a populated response

An explicit **source-posted** study predicate can require an exact `Initial` or `Game Day` phase, reconciled game/team identity, valid row arrays and recognized statuses. Preserve Pending, unknown phases, malformed rows and empty team arrays as unavailable. This is a documented operational interpretation of the source's published presentation, not an invented backend `completed` flag or a blanket nonpending-complete rule.

For a nonempty all-Available team, the renderer supplies the team none declaration. For player-specific rows, the selected research rule is team/season-scoped exact full-name matching after NFKD → ASCII → lowercase → alphanumeric normalization. Preserve raw names and every name token/suffix; this rule removes punctuation but does not expand initials, drop suffixes or use prefix, fuzzy or jersey-only substitution. Canonical collisions and unresolved full-game Out names leave the burden unknown. This supersedes an earlier uncommitted NFKC proposal; normalization and uniqueness are research rules, not publisher guarantees. Keep first-half Out separate even though the visual heading combines it with full-game Out.

The [verified ACC policy](ACC_AVAILABILITY_POLICY_2026.md), pp. 1–3, supplies the unlisted-player Available presumption for an applicable report, requires explicit Available when a previously listed player recovers, and says both teams are published together after the deadline. It does not define API completion bits or phase strings. The ACC publisher/route is also **not proof that both teams are ACC members**: optional nonconference reports are permitted. Any conference-only study needs separate membership evidence.

No extra schema-review requirement is necessary solely because the next body becomes populated: its actual fields and roster matches can be checked against the already fixed parser. A failed check remains unavailable; a new field or ambiguous alias does not justify an outcome-informed repair. No canonical ACC-to-roster identity or source-wide completeness guarantee has been established by this inspection.

The asset is 140,231 bytes, SHA-256 `56a370b79721d76119891c55780731b941cda934019d484737f9d7b0a1f70be8`, received between **09:14:28.108149 and 09:14:28.444509 UTC**. Original bytes and receipt remain under ignored `model/data/raw/availability_renderer_preflight/`; the first source preflights and live collector are unchanged. Actual receipt, source issue labels, policy deadlines and cache metadata remain separate timestamps.

## Separate official conference-membership evidence

The original [ESPN inventory response](https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard?dates=20260909-20260916&groups=80&limit=1000), received **08:16:30.398440 UTC** on September 9, identifies game `401858216` as `competition.conferenceCompetition === true`. Its `groups` object identifies ACC (`id: "1"`, `name: "Atlantic Coast Conference"`, `shortName: "ACC"`, `isConference: true`), and both canonical competitor IDs `183` and `25` have `team.conferenceId === "1"`. These conjoined fields provide explicit eligibility evidence for this matchup; they do not certify every future response or every report on an ACC publisher route.

This is distinct ESPN source evidence, not a renderer field. The original decoded body SHA-256 is `3317efbaa69a3a711846aefab1c76771b9d13f2e960bc3eb1e15e7811c309fcc`; its immutable receipt and request interval are recorded in the JSON inventory. No source bytes, old receipt or target outcomes were changed or inspected.
