# ACC QB role source: linked public-page preflight

**The retained 2026 rosters and public prior-game boxscores support an exact QB identity and passing-attempt join for both designated teams. The populated ACC status-row crosswalk remains unverified.** Access date: September 9, 2026. No player list, individual weights, model, target-game outcome analysis or policy activation is published here.

The [first preflight](ACC_QB_ROLE_PREFLIGHT.md), frozen at commit `39164343fc6d489dbcf7445ad34c470e13a6e5ad`, remains unchanged. This separate stage followed two literal Gamecast links already present in the archived official target summary, then each page's literal public boxscore link. All four new requests returned HTTP 200. Existing roster bodies were reused; the denied summary API was not retried.

## Identity and complete passing tables

The [Syracuse–New Hampshire Gamecast](https://www.espn.com/college-football/game/_/gameId/401858208/new-hampshire-syracuse) and [California–UCLA Gamecast](https://www.espn.com/college-football/game/_/gameId/401858210/ucla-california) confirm the two source-selected prior games. Their linked [Syracuse boxscore](https://www.espn.com/college-football/boxscore/_/gameId/401858208) and [California boxscore](https://www.espn.com/college-football/boxscore/_/gameId/401858210) repeat the exact event, competition, team IDs and dates. Embedded status fields agree on `Final`, status ID `3`, and `post`; dates are confirmed rather than TBD. Syracuse's prior game was September 5 at 16:00 UTC, California's September 6 at 02:30 UTC. No final score or game-result value was needed for these checks.

Both boxscore HTML bodies contain JSON at `window.__espnfitt__.page.content.gamepackage.bxscr[]`. Select the unique matching `tm.id`, then the unique `stats[]` object with `type == "passing"`. Its aligned `keys` and `lbls` explicitly identify `completions/passingAttempts` as `C/ATT`. Each `athlts[]` row contains its `stats[]` and `athlt.id`, `uid`, `dspNm`, `jersey` and player link. The category's `ttls[]` provides the corresponding team total.

| Team / ESPN ID | Roster QB rows | Prior passing rows | Exact roster-QB ID matches | Sum of attempts / published team attempts |
|---|---:|---:|---:|---:|
| Syracuse / `183` | 7 | 3 | 3 | 34 / 34 |
| California / `25` | 6 | 2 | 2 | 47 / 47 |

All five passing rows have positive integer attempts and valid `completions/attempts` strings. Athlete IDs are unique within each table; all five uniquely match retained 2026 roster rows with literal position `QB`. UID, published full name, jersey and player-link ID also agree. There are no unmatched or non-QB passing rows in these two tables. Every row and team-total array aligns with the explicit headers. Both completions and attempts sum exactly to the published team totals. “Complete table” refers to this returned, reconciled snapshot; it does not certify future publisher corrections or every other game's coverage.

The Gamecast pages provide passing leaders and explicitly labeled `C/ATT` play cards, but no full `bxscr` table. Those displays were useful crosschecks and link discovery, not the denominator for the passing table. The two linked boxscore responses establish the aggregate counts above.

## Timing and remaining uncertainty

The original summary's `lastFiveGames` dates selected these exact prior IDs before the web requests; no player or game was selected from the September 12 outcome. This preflight does not independently establish completeness of the source's entire season schedule. Prior participation also does not establish a future starter or a health condition.

The combined roster-plus-boxscore evidence was available locally at **09:03:51.740668 UTC for Syracuse** and **09:03:51.999291 UTC for California** on September 9. These times precede California–Syracuse game `401858216`, scheduled September 12 at 19:30 UTC. They follow the original ACC capture at 08:16 UTC and cannot be backdated into it.

The boxscores' `Last-Modified` headers are later than receipt—09:13:51 and 09:10:39 UTC. All original headers are retained, but those anomalous timestamps cannot prove publication time. Actual original receipt is the observation boundary; current roster and game pages remain mutable.

The ACC source inspected earlier was still `Report Pending`, with empty player arrays. Therefore this source check cannot establish the completed-report marker, name/number formatting, ACC stable athlete IDs, duplicate resolution or status semantics. Every roster QB's jersey is shared by another athlete, as the first preflight documents; jersey-only matching remains unsafe. An actual populated current-report audit and a separately fixed identity rule are still needed before any status burden/change or attempt-weighted feature is activated.

## Original-byte evidence

| New response | Bytes | SHA-256 |
|---|---:|---|
| Syracuse Gamecast | 1,309,736 | `4fd469b43b7bd1aec90df954f4eb88b088591887e081bd346559bd2eade3b71d` |
| California Gamecast | 1,322,785 | `4935f67d865ad70d1cc42c984bf7840ea18cbe1045d95f4012c5e3a42b5c3021` |
| Syracuse boxscore | 574,888 | `8090033efdee0f1ead380d166f292b7521e2ec2e1f7e428bcc113511eed14622` |
| California boxscore | 558,614 | `7127bb79259e26d8beefb831bd2cf717a4762c6fcf5b04269a35bffb79cdad14` |

The [JSON evidence inventory](ACC_QB_ROLE_WEB_PREFLIGHT.json) includes all four new receipts and the two reused roster receipts, hashes, timing, schema checks and aggregate joins. Raw bytes remain under the ignored `model/data/raw/availability_role_web_preflight/` and original roster namespace. This stage made only the two previously observed Gamecast requests and their two explicitly linked public boxscore requests, with no redirects or authentication. The denied API was not retried. Requests retained the fixed 8 MiB cap and bounded connect/read timeouts; between-chunk elapsed checks are not an absolute wall-clock guarantee.
