# ACC availability components: bounded validation

The retained hosted capture produces a usable role-request plan: **one future ACC matchup and four observed URLs**, comprising two rosters and two prior-game Gamecast pages. This confirms that the planner accepts the actual source schema. It does **not** validate an availability forecast, betting edge, completed injury report, or deployed probability runner.

This was an offline reconstruction performed after the capture. No new HTTP requests, target score reads, model fits, forecasts or paper positions were made. The supplied `as_of` was the hosted capture's completion time, **2026-09-09 08:35:08.271901 UTC**; running today's planner with that cutoff does not make its output a historically recorded decision.

## Verified original inputs and observed result

The original manifest is `data/runtime/availability/runs/34329726247-1.json`, SHA-256 `b9ec913a7646b051d7eb5c1a55fa1f8ed1a70f599ebfa97acc340a05ce4fc862`. Before parsing inventory or summary metadata, this check rehashed the manifest and cohort, all seven original response bodies and their canonical receipt identities, and all seven source files against the manifest's recorded Git commit. Source timing was checked against the supplied cutoff. Original bytes and all existing artifacts were left unchanged.

| Observed quantity | Result |
| --- | ---: |
| Inventory events | 86 |
| Verified future ACC conference matchups | 1 |
| Other events excluded by ACC matchup criteria | 85 |
| Targets omitted by the 20-game cap | 0 |
| Roster request plans | 2 |
| Prior team/game request plans | 2 |
| Selected-team summary or link failures | 0 |

The target is California (`25`) at Syracuse (`183`), game `401858216`, with source kickoff **2026-09-12 19:30 UTC**. The inventory contains one competition with `conferenceCompetition: true`, group ID `1`, name `Atlantic Coast Conference`, `isConference: true`, two distinct home/away team IDs, and conference ID `1` for both teams. The retained summary agrees on game ID, teams, kickoff and pregame state. The inventory supplies the literal roster URLs; the summary header itself does not need to supply roster links.

| Team | Observed roster route | Selected prior Gamecast ID | Prior date (UTC) |
| --- | --- | --- | --- |
| Syracuse (`183`) | `/college-football/team/roster/_/id/183` | `401858208` | 2026-09-05 16:00 |
| California (`25`) | `/college-football/team/roster/_/id/25` | `401858210` | 2026-09-06 02:30 |

The JSON companion retains exact URLs, source receipt/body hashes and JSON pointers. Each prior selection is the **latest dated entry in the observed prior-game list**. Selection uses no score or game-result field. These entries have no explicit season field, so their `season_basis` is `fixed_date_window_inference` under the prepared specification’s fixed interval `[2026-08-01 00:00 UTC, 2027-02-01 00:00 UTC)` (not a rule in force at the hosted capture), also requiring the date to precede `as_of`. Neither complete schedule coverage nor completed-game status is inferred from that list. Actual Gamecast identity/date and explicit Final/post status must still be verified before a passing-participation profile can be used; a boxscore URL must be literally observed there.

## What has and has not been validated

The new transport and planner passed **101 focused synthetic tests**: 49 transport tests and 52 planner tests. They cover original decoded-byte preservation, immutable failure receipts, URL restrictions, bounded transfers, source pointers, first designation before link validation, date boundaries, deduplication and outcome-field poison tests. This report does not claim to independently audit the planner's implementation, and it does not substitute these tests for end-to-end source or model validation. Other component test totals are deliberately omitted while their integration is in progress.

The [hosted capture audit](ACC_AVAILABILITY_HOSTED_CAPTURE_AUDIT.md) established operational collection of one **Report Pending** record and two book-price pairs. Its recorded completed-report and fitted-model counts are both zero. The separate [roster preflight](ACC_QB_ROLE_PREFLIGHT.md), [prior-page preflight](ACC_QB_ROLE_WEB_PREFLIGHT.md), and [current renderer contract](ACC_CURRENT_RENDERER_CONTRACT.md) document source capabilities and limitations. Those later page receipts cannot be backdated into this hosted capture. A QB roster label is not starting status; prior passing participation is not future participation; a pending or empty source record is not an all-Available report.

Following the HTML lexical-parser correction, this component check also rehashed all **six retained original HTML bodies and receipts** and replayed the current source parser offline. Both rosters validated with **112 athlete rows each**; both prior Gamecast contexts passed the exact identity/date and completed-state checks. The Syracuse passing table validated with **3 rows and 34 total attempts**; California's validated with **2 rows and 47 total attempts**. There were no unmatched passing IDs in these two roster comparisons. The JSON records the parser hash and later receipt times. These are aggregate schema/identity checks, not target-game outcome analysis or a verified populated ACC crosswalk.

The new opportunity-designation utility has **37 synthetic tests reported passing by the root integration run**, not rerun for this report. Its contract takes caller-verified context/raw payloads, excludes manual captures from prospective designation, fixes the first scheduled initial/game-day opportunity before examining player rows, retains duplicate-phase ambiguity, and preserves the first kickoff grouping and failed-source/inventory snapshots for preceding quotes. This is not yet an activated workflow step.

The probability and evaluation utilities remain an **unvalidated experiment**. No populated current availability record has been used here to establish a full report-to-role-to-price opportunity. No ACC probability fit, prospective scoring result or model-driven page posting is asserted by this report. The four existing policies and both weather-study families are unchanged.

## Proposed role-preparation controller contract

These are integration recommendations, not an implemented or activated controller:

1. Accept already verified inventory and summary **envelopes**, original receipt paths/hashes, invocation ID/attempt and a clock. Run `plan_roles` on their original payloads; retain an immutable plan and source-reference manifest. The planner cannot itself certify receipt timing because its API receives payloads only. Reject unverified or future receipts rather than manufacture a current context.
2. Keep original HTML bodies and receipts in `data/runtime/availability_roles`. Store immutable attempts and parsed profiles separately, each linked to original receipt/body hashes and the parser/code version. A convenience index may be rebuilt atomically, but it is not evidence: reverify the referenced immutable records before reuse.
3. Use a roster cache keyed by `(season, team_id)`. A successful verified roster can be reused for up to 24 hours from its original receipt, never from an index update. A failed attempt cannot renew that freshness. Preserve an otherwise valid earlier snapshot only until its original 24-hour limit; an unavailable roster may be retried on the next invocation through the fair queue and fixed request budget, with no additional 24-hour failure embargo.
4. Cache a verified prior-game participation profile by `(team_id, selected_prior_game_id)`, retaining exact game/team/date identities and the Gamecast/boxscore receipt chain. A newly designated prior game cannot inherit an earlier game's profile. A failed chosen page remains unknown; it does not authorize earlier-game substitution.
5. Allow **12 total outbound GET attempts per role-preparation invocation**, including failures and any Gamecast-to-observed-boxscore follow-up. Inputs are existing envelopes, so this contract needs no additional discovery HTTP. Any later added discovery request must also consume that role-preparation budget. The raw ACC collector's existing independent request/quota limits remain intact.
6. Choose due requests by never-attempted first, then oldest recorded attempt time, with deterministic target kickoff/game-ID/team-ID/kind tie breaks. Count and immutably record each attempt before choosing the next item. Do not retry a failed key within the invocation; the new attempt timestamp moves it behind older work. Report budget-deferred work explicitly. Schedule selection must not depend on availability statuses, QB burden, prices or modeled EV.
7. Prepare roles before the current report and quote capture. Any role-preparation exception, unavailable source or budget exhaustion must still allow raw ACC collection to run, with model inputs marked unknown. A later successful source cannot repair an earlier locked first-phase decision. Perform current inference and receipt revalidation immediately after current quotes; do not insert full-history hashing, role fetching or fitting into the short quote-freshness window.

The existing availability workflow uses the fixed seven-day window, shared `daily-totals-pages` concurrency, continued publication after collector failure, and a namespace-limited archive commit. It currently invokes only the collector and status publisher: none of the proposed role/model steps is activated. An eventual role step must preserve the raw collector's reachable failure path; merely adding a prior failing step would make the normal subsequent `if` condition skip collection. New role/study namespaces would also need explicit archival paths before Pages upload.

The separate prospective runner should retain immutable initial/game-day designations before feature validity, a distinct forecast key per game/phase, and at most one earliest positive paper entry per game. Weekly artifacts should require 20 distinct completed games and two completed weeks, using only labels available at their specified cutoff; this is an operational fit minimum, **not a confidence threshold**. The fixed positive L2 penalty permits one-class training samples, so both target classes are not an admission requirement. Fit availability must precede a new decision's quote receipt. Labels-only maintenance can settle existing entries and prepare future artifacts, but cannot backfill past forecasts or make role requests implicitly. The 06:30 board can later read a separate aggregate study status; it must not reinterpret archived prices as currently available offers or merge this experiment into the existing policy ledgers.

## Subsequent integration check

After the independent reviews, the complete local suite passed: **1,731 tests and 176 subtests**, with nine existing dependency/environment warnings, in 38.99 seconds. The new components account for 495 tests: 52 numerical model, 137 source parser, 49 HTML archive, 52 role planner, 48 evaluation, 37 opportunity and 120 decision tests. The JSON companion records the tested file hashes separately from the earlier source replay.

The review corrected static JSON extraction inside regex/inert script text, and a rescheduling case that could skip a failed capture after the old kickoff passed. Regression cases now cover both. The six retained original HTML pages still parse after the lexical correction. Decision tests include one-book positive-EV qualification, exact-line and side-price arithmetic, paired model serialization, current-week artifact cutoffs, original-offer fingerprints and the 120-second lock boundary. These are software/source checks, with **zero real-data fits and zero prospective availability forecasts**.
