# ACC quarterback role-source preflight

**The two ESPN rosters expose usable team, athlete and QB fields. Prior-game passing participation and the populated ACC player crosswalk remain unverified.** Access date: September 9, 2026. This was source inspection only: no player was selected, no September 12 outcome was requested, and no model was fitted.

The original availability capture identified California (ESPN team `25`) at Syracuse (`183`), game `401858216`, September 12 at 19:30 UTC. Its official inventory supplied the literal roster links. Its already retained official summary supplied the two latest dated `lastFiveGames` entries: Syracuse–New Hampshire `401858208`, September 5 at 16:00 UTC; California–UCLA `401858210`, September 6 at 02:30 UTC. Selection used team IDs and dates earlier than the original receipt, without inspecting score or result values. The list's completeness and explicit completed-state semantics were not independently established by a fresh prior-game summary.

## Verified roster schema

The [Syracuse roster](https://www.espn.com/college-football/team/roster/_/id/183) and [California roster](https://www.espn.com/college-football/team/roster/_/id/25) each returned HTTP 200. Original HTML includes JSON at `window.__espnfitt__.page.content.roster`: matching `team.id`, explicit `metadata.season = "2026"`, and `groups[].athletes[]`. Athlete fields include string `id`, `uid`, published full `name`, `jersey`, scalar `position` and athlete-page `href`. Literal `position == "QB"` is observable. These are ESPN-published roster records, not independent team-department certification or evidence of the next starter.

| Snapshot | Athlete rows | Literal QB rows | Unique athlete IDs | Repeated jersey values / affected rows |
|---|---:|---:|---:|---:|
| Syracuse | 112 | 7 | 112 | 26 / 54 |
| California | 112 | 6 | 112 | 31 / 63 |

All 224 rows contain nonempty ID, UID, name, jersey and position. Every UID and athlete-link ID agrees with its row's ID. Within each team, both diagnostic normalized full names and name-plus-jersey pairs are unique. This is within-snapshot consistency; it does not prove stable cross-season membership or a cross-source match. The diagnostic name normalization is documented in the JSON and is not a frozen production rule.

**Every QB jersey is also used by another athlete on the same roster.** A jersey-only join could therefore misidentify all 13 QB rows. The current ACC body remains `Report Pending` with two empty arrays, so actual populated name/number formatting, stable-ID availability, duplicate handling and the ACC-to-roster crosswalk cannot yet be verified. No position or participation was inferred from an availability category.

## Prior participation and timing limits

The fixed [Syracuse prior-summary request](https://site.api.espn.com/apis/site/v2/sports/football/college-football/summary?event=401858208) returned HTTP 403 with an `Access Denied` body. Collection stopped; the designated [California prior-summary URL](https://site.api.espn.com/apis/site/v2/sports/football/college-football/summary?event=401858210) was not requested. Consequently, this preflight verifies neither a passing-attempt field nor an athlete-ID join from prior passing participation. A limited search found no readily available original summary for either exact prior ID. This is an observed access failure for one request, not proof that public prior participation is unavailable everywhere.

The rosters were received at **08:49:39.001529 UTC** and **08:49:39.280430 UTC**, respectively. Both precede the target game but follow the first ACC capture at 08:16 UTC. They cannot be backdated into that earlier observation. HTTP receipt times establish local observation, not first publication; current roster URLs are mutable. Both `Last-Modified` headers are about five minutes after receipt (08:54:38/39 UTC); these anomalous provider timestamps are retained and cannot establish publication time.

An existing schedule cache was excluded from receipt-verified prior evidence. Its current Parquet hash differs from the adjacent download sidecar: the [download writer](../ncaaf_model/sources.py) records original-response SHA, while [runtime input refresh](../ncaaf_model/runtime.py) can merge ESPN records and rewrite that Parquet without refreshing the download sidecar. Thus the two hashes can describe raw download versus derived bytes; the difference is not proof of corruption. No passing-attempt information was obtained from it.

## Original evidence and bounded outcome

There were three actual HTTP responses: two rosters and one denied summary. The initial Syracuse roster attempt failed inside the sandbox with no HTTP response and zero bytes; one expressly authorized operational retry preserved that failed receipt under a separate successful receipt. No response body was overwritten, and there was no retry after HTTP 403. Requests used no credentials, no redirects, an 8 MiB response cap, 5-second connect/10-second read timeouts and elapsed checks between chunks; these checks are not an absolute wall-clock guarantee.

| Original response | Bytes | SHA-256 |
|---|---:|---|
| Syracuse roster | 626,244 | `65342a98f7c5fefc68954c31ad4e02a46ff1053866c37f7f335b089db80e57ba` |
| California roster | 626,106 | `0f273af255340e09c479d64f58534c5573c5e9349fa076d1a7aef54fef6d7a4d` |
| Denied Syracuse summary | 461 | `4c685e9e0995f09b2481758981fdf4b4505aec2a20bae3207ed49fc6c3053b96` |

The [JSON evidence inventory](ACC_QB_ROLE_PREFLIGHT.json) records original request/receipt times, safe headers, hashes, source paths, aggregate schema checks and unavailable steps. Bodies remain in the ignored `model/data/raw/availability_role_preflight/` archive; no player list is published.

The bounded next requirement is a separately verified prior-participation source and a populated current ACC row audit before freezing any QB participation or attempt-weighted feature. This preflight does not justify substituting a guessed starter, equal QB weights or target-game participation.
