# Existing 2026 quote archives: provenance and normalization

These are existing user captures of publicly offered NCAA football prices, collected before the recorded kickoffs according to local receipt metadata. They are not a freely downloadable historical API dataset. The raw files are excluded from the public repository; someone reproducing this normalization must already possess the captures. No new historical odds were purchased or downloaded for this exercise.

The normalized input was frozen at **2026-09-09 01:19:04 UTC**, after the relevant games. Its SHA-256 is `bf0c86d92458d7f253424b3fa8c9b97182207dae7519c559d93f89ceb91b970b`. Freezing earlier captured prices now does not make the subsequently reconstructed forecasts prospective, independently registered, or untouched validation.

## Coverage before joining outcomes

| Source | Paired quote records | Source evidence |
|---|---:|---|
| The Odds API | 7,646 | Local receipt metadata, matching raw SHA-256, bookmaker or market update time when supplied |
| ESPN scoreboards | 427 | Local receipt metadata, matching raw SHA-256, explicit pregame state, DraftKings provider 100; no quote update time |
| Combined | 8,073 | 171 uniquely matched games; repeated observations and books remain separate |

There are 166 games with a valid receipt within 14 days before kickoff, including 120 whose recorded kickoffs had passed at normalization. There are 98 games with a same-Eastern-calendar-day receipt at or after 06:30 and before kickoff: 74 appear in The Odds API, 97 in ESPN, with overlap. These counts describe source coverage, not qualifying bets, completed games, or weather outcomes. The first such observations occur on August 29 Eastern time (one late-evening capture), September 4, and September 5. Actual observation times are retained; most later captures were around 09:00 Eastern. They do not represent a 06:30 execution record.

The separate frozen-model replay uses only The Odds API and the previously selected DraftKings/FanDuel cohort. ESPN records remain available for provenance research and cannot meet an update-time freshness requirement merely because their file was recently received.

## What supports the receipt time

Each accepted raw file has a sidecar with an explicit `retrieved_at` and matching SHA-256. Original filesystem birth and modification dates provide additional local consistency evidence. Source market timestamps are retained and must not exceed the stated receipt. Neither filenames nor a new download time supply missing timestamps.

This evidence is **local and mutable**, not independent timestamp attestation. A matching hash detects whether bytes agree with the sidecar; it does not prove when either was created. Filesystem dates and sidecar receipt values can be changed. Source timestamps describe the provider's market update, not authenticated receipt by this project or sportsbook acceptance. No trusted timestamp service, independently verified GitHub server timestamp, or corresponding workflow run has been verified for every raw price.

The sibling `ncaa-football-daily-board` repository contains commit `d890afd0a4d6f78e87707376365bbad81c7a58fd`, whose author and committer dates read September 1, 2026. It includes `model/data/snapshots/predictions_20260831T144241Z.json`. The snapshot's stated time is August 31, 2026 at 14:42:41 UTC; 73 common games' consensus totals exactly match the corresponding raw capture's median bookmaker totals. This corroborates the aggregate content across the two local artifacts. **Git author and committer dates can be backdated and are not a trusted capture timestamp.** The commit does not bind individual total prices or raw-file hashes. Its `american_odds` field is a moneyline price and is not used as a total price. The raw odds files themselves have no verified pregame Git commitment.

## Rules fixed without reading outcomes

The [normalizer](../../scripts/normalize_2026_archives.py) requests only schedule identity, season/week, kickoff, team names/IDs and neutral-site columns. It never requests schedule scores or final status and does not read weather, predictions, winners, or grading results.

- The Odds API events require a unique canonical home/away match and a recorded kickoff less than one hour from the schedule kickoff. ESPN requires the same game ID, exact home/away IDs, the same kickoff tolerance, explicit `pre` state, and DraftKings provider 100.
- Each retained bookmaker contributes exactly paired Over and Under outcomes at the same full-game line. American prices and their exact mathematical decimal equivalents are retained. Duplicate outcomes, mismatched lines, missing prices, and conflicting bookmaker snapshots fail validation.
- Receipt must strictly precede kickoff. Missing market update times remain missing. ESPN's nested `close` fields mean the current paired quote in that captured pregame response; they are not claimed as the eventual closing prices.
- The first manually initialized August 19 source file is quarantined in full: 1,487 market/book update references fall after its claimed receipt. No corrected receipt is guessed. Other exclusions comprise 195 missing/ambiguous event matches, 15 after-kickoff observations, 23 ESPN kickoff mismatches, and one unpaired ESPN total. Counts use their respective file, event, or quote units, not a common game denominator.
- Outcome-free schema validation checks identities, paired prices, hashes, source chronology, and horizon flags before writing. Target outcomes enter only the separate replay's later grading step.

Schedule identity and kickoff matching use a retrospectively available public schedule. Corrections to that schedule and to prior-game statistics can differ from what was published at the historical receipt. A later replay therefore needs to disclose this availability assumption, even with correctly ordered feature cutoffs.

## Reproduction and input integrity

From the repository root, using an environment with the project's Python dependencies:

```bash
python scripts/normalize_2026_archives.py --root . --source-root ../ncaa_football_model
python -m unittest discover -s scripts/tests -p test_normalize_2026_archives.py
```

The source-root argument names an existing local archive workspace containing `data/raw/the_odds_api` and `data/raw/espn_scoreboard`, each with raw JSON and matching `.meta.json` files. The schedule input is `model/data/raw/sportsdataverse/cfb_schedule_2026.parquet`. Outputs are `model/data/raw/alternative/archived_2026_paired_quotes.parquet` and `archived_2026_quote_manifest.json`. The ignored manifest retains per-file hashes, receipt evidence, and local source locations; this public report contains no personal filesystem paths.

Do not regenerate a frozen replay's input in place: normalization writes new artifact/manifest files and a new creation time, so a locked replay must reject changed hashes. The eight offline tests use temporary synthetic captures and cover receipt-versus-filename separation, exclusion of score columns, exact paired prices, hash corruption, future updates, duplicate/mismatched outcomes, missing timestamps, ESPN pregame/provider validation, and corrupted normalized schema. The existing frozen input passed the new schema validation without being rewritten.

The result is an auditable reconstruction input with explicit evidence limits. It does not establish executable historical bets, account access, stake limits, a prospective publication record, or a high-confidence profitable strategy.
