# ACC football availability policy: 2026 verification

Access date: **2026-09-09**. The [official ACC document viewer](https://theacc.com/documents/2026/8/24//ACC_Availability_Reporting_Policy_2026_27.pdf?id=2940) links the [original four-page policy PDF](https://s3.amazonaws.com/sidearm.sites/acc.sidearmsports.com/documents/2026/8/24/ACC_Availability_Reporting_Policy_2026_27.pdf). One viewer response and one original PDF were retrieved, both HTTP 200 with no redirects. All four PDF pages were extracted and visually inspected. Existing source preflights remain unchanged; no outcome joins, fits or model/collector changes were performed.

**The evening deadline is 8 p.m. in the game venue's local time, not universally Eastern.** Reports are published after the applicable submission deadline, with both teams published together; the policy specifies no maximum publication lag (pp. 2–3).

| Football phase | Submission deadline | Source |
|---|---|---|
| Initial | Three nights before the game, by 20:00 where the game is played | p. 2 |
| First update | Two nights before, by 20:00 in that same local-time basis | p. 2 |
| Second update | Night before, by 20:00 in that local-time basis | p. 2 |
| Game day | No later than two hours before scheduled kickoff | p. 2 |

Coverage includes ACC regular-season and conference championship games. Reporting against a non-ACC opponent is optional (pp. 1, 3). The noon-local-or-two-hours-before rule for consecutive-day games appears under **Basketball and Baseball**; it is not the football schedule (p. 3).

Before game day, the categories are Available, Probable, Questionable, Doubtful and Out. Game day uses Available, Game Time Decision and Out. Football first-half suspensions under targeting or other NCAA/ACC rules are explicitly designated **Out (1st half)** (pp. 1–2). That is a partial-game restriction, not a full-game absence.

The report supplies a player's name and availability status, without the underlying reason. An unlisted player is presumed available, and a previously listed player who becomes available for the same game must be affirmatively marked Available (pp. 1–2). For collection, that presumption requires a completed applicable report; it cannot convert a pending/missing response into an all-available report. Availability does not establish who starts or how many snaps someone plays.

**Source ambiguity:** p. 1 includes Doubtful with a 25% policy label, while p. 2 says a player with any chance to play must be at least Questionable before game day. Preserve both statements and actual reported categories; do not silently harmonize them or treat policy percentages as calibrated participation forecasts. The Conference may issue further guidance (p. 4).

For a prospective collector, derive deadlines from verified game-venue timezone and local calendar date. Keep the scheduled deadline, any provider issue-time fields and actual receipt separate. The PDF does not define API phase strings, stable IDs or revision-history completeness. Its rule that playing after a game-day Out designation is an automatic violation is not a guarantee that such events cannot occur (p. 4).

## Provenance and dates

The PDF prints **Last updated: August 20, 2026** on p. 4. Embedded creation/modification metadata is August 24 at 13:08:41 with a −04:00 offset; the storage object's Last-Modified is August 24 at 17:31:57 UTC. These are distinct from our PDF fetch bracket, **September 9, 07:54:52.210465–07:54:52.742906 UTC**. They do not certify the original public release instant, or the timing of any player report.

The PDF is 216,495 bytes with SHA256 `f12f1e5b006683d313dd7fb009d361e77d53fac8df9271be6650691043d9d5a0`. Original bodies, raw headers and receipt hashes are retained under ignored `model/data/raw/availability_preflight/acc_policy/`; the first downloaded path has a `.pdf` suffix but contains the site's HTML viewer, which is explicitly identified as HTML in [the evidence JSON](ACC_AVAILABILITY_POLICY_2026.json). `policy_original.pdf` contains the verified PDF bytes. Raw source bodies are not republished.
