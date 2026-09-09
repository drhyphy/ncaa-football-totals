# Alternative historical NCAA football data

Research snapshot: 2026-09-08/09 UTC. The main addition is 3,096 completed, independently matched 2020–2023 games from a public CollegeFootballData archive. It adds 2,006 games to the prior sparse clean-history table, including 1,975 before 2023. This materially improves training coverage; it does not establish profitability or reproduce prices available at the scheduled 06:30 ET run.

Raw downloads and derived market tables stay under the ignored `model/data/raw/alternative/` directory. Public availability does not by itself grant redistribution rights. No subscription was purchased, and no third-party credentials were accessed.

## 1. CollegeFootballData per-provider archive: principal addition

Source: [jasperfriis-cuni/cfb-market-efficiency](https://github.com/jasperfriis-cuni/cfb-market-efficiency), pinned commit `59f7f0ef813b229894757619901c49855a08ac1a`, `raw_data/lines_2020.json` through `lines_2023.json`. Its README identifies CollegeFootballData `/lines` as the source. The repository has no declared license. Only raw data was used; its model code and reported results were not adopted.

The [official CFBD client documentation](https://github.com/CFBD/CFBSharp/blob/master/docs/BettingApi.md) describes this endpoint as closing betting lines. The [current endpoint schema](https://api.collegefootballdata.com/api/betting) provides distinct `overUnder` and `overUnderOpen` fields. These are documented closing/opening fields, but the downloaded records carry no bookmaker update timestamp, total-side Over/Under price, or certified final pregame observation time. Retrieval and Git publication times are not quote timestamps.

| Season | Raw game objects | Validated selected games | FBS versus FBS | New versus old clean table | Bovada same-book open/final pairs |
|---|---:|---:|---:|---:|---:|
| 2020 | 542 | 538 | 505 | 508 | 0 |
| 2021 | 849 | 848 | 731 | 735 | 840 |
| 2022 | 1,417 | 849 | 733 | 732 | 780 |
| 2023 | 1,350 | 861 | 748 | 31 | 786 |

The total newly covered count is **2,006**, of which **1,975** precede 2023. Raw coverage in 2022–2023 includes lower-division games that do not appear in the local FBS-oriented schedules; these were not silently treated as validated FBS games.

Validation requires a local ESPN schedule game ID, exact home and away team IDs, exact home and away final scores, `STATUS_FINAL`, and kickoff dates within 24 hours. Eight games have reversed home/away conventions and were excluded rather than automatically repaired. No selected game has an ID or final-score mismatch. Duplicate `(game_id, provider)` records are rejected. Plausibility filters use fixed broad ranges (total 10–130, spread −80–80), never closeness to the outcome.

Selection uses this fixed source priority: consensus, Bovada, DraftKings, William Hill (New Jersey), ESPN Bet, Caesars, SugarHouse, Caesars (Pennsylvania), Caesars (Colorado). A source must have both a plausible final total and spread. This priority is unrelated to game outcomes. Consensus dominates 2020–2022; Bovada dominates 2023. `numberfire` and `teamrankings` records remain in the per-provider archive but cannot become the selected main market. A consensus is an aggregator, not an independent bookmaker.

Outputs:

- `cfbd_market_games.parquet`: 3,096 unique games, with `game_id`, `season`, `week`, `date`, `start_date`, home/away IDs and names, scores, `actual_total`, `market_total`, `spread`, `market_source`, `opening_total`, and validation/provenance fields.
- `cfbd_provider_quotes.parquet`: 13,091 provider records, including all preserved open/final fields and validation flags. This larger file includes unmatched lower-division records; consumers must require `validated`.
- `cfbd_manifest.json`: pinned URLs, SHA-256 hashes, source selection, validation counts, and missing-timestamp disclosure.
- `cfbd_validation_exceptions.csv`: excluded or unmatched games.
- `cfbd_vs_sportsdataverse.parquet`: original scalar-archive comparison, for data auditing only.

`opening_total` is populated only from the same source as the selected `market_total`; it is never blended across books. Extra columns such as `bovada_market_total` and `bovada_opening_total` provide matched source pairs when the main source is consensus. Provider/bookmaker counts describe line coverage, not independently verified priced Over/Under pairs. All quote timestamps remain null.

### Cross-source disagreements and contamination

On 1,090 games shared with the former scalar SportsDataverse market table, absolute total differences were: exactly zero 365; up to 0.5 points 376; (0.5,1] 199; (1,2] 110; (2,3] 22; (3,5] 13; above 5 points 5. Mean absolute discrepancies by season were 0.48, 0.26, 0.58, and 0.72 points for 2020–2023. Provider choice and observation time can explain some disagreement, so disagreement alone is not an outcome-based exclusion rule.

| Game | Former scalar total | Selected CFBD total | Relevant provider detail |
|---|---:|---:|---|
| 2022 Virginia Tech–Wofford, 401411114 | 54.5 | 45.5 | Consensus/William Hill 45.5; Bovada 45 |
| 2023 UAB–North Carolina A&T, 401531377 | 44.5 | 50.5 | Bovada 50.5; DraftKings/William Hill 46.5 |
| 2023 Arizona State–Southern Utah, 401523987 | 61.5 | 53 | Bovada 53; DraftKings 59.5; William Hill 60.5 |
| 2023 Texas A&M–New Mexico, 401520173 | 43.5 | 49 | Bovada/DraftKings 49; William Hill 48.5 |
| 2023 Rice–Houston, 401525831 | 58.5 | 51 | Bovada/William Hill 51; DraftKings 53 |

A separate primary ESPN audit confirmed that the original scalar archive sometimes selected a provider explicitly named **ESPN Bet - Live Odds**. For SMU–TCU in 2024 (401635557), it retained 107.5, while that provider's opening total was 57.5. This is a market-role failure, not merely a large prediction error. Earlier benchmarks using those scalar values must remain quarantined. The replacement primary-provider audit and repair are documented in the dedicated ESPN timing reports. CFBD source-role documentation improves provenance but still cannot certify an exact 06:30 quote.

## 2. Independent CFBD derivative: 2024–2025 source sensitivity

Source: [andrewrpokorny-source/cfb-analytics](https://github.com/andrewrpokorny-source/cfb-analytics), pinned commit `1371e18135e778b41372b860f7045c76027719aa`, `cfb_training_data_24_25.csv`. The file first appeared in commit `f7d1ce1e9d5ffca61640b3b0cf8eecea02bbff72` on 2025-12-17 at 12:35:31 UTC. Repository license is undeclared.

The pinned [generator main.py](https://github.com/andrewrpokorny-source/cfb-analytics/blob/1371e18135e778b41372b860f7045c76027719aa/main.py) calls `/lines` and directly copies `spread` and `overUnder` from `lines[0]`, without a default-fill total. It discards provider identity, opening fields, and timestamps. It also drops games lacking full-season advanced statistics or spread. This is an imperfectly selected derivative, suitable for checking whether results depend on the main source, not a stronger replacement for directly verified pregame-provider records.

The CSV contains 1,560 games: 799 from 2024 and 761 from 2025. Removing three missing totals and two neutral-site orientation mismatches gives **1,555 score-validated games: 796 in 2024 and 759 in 2025**. SMU–TCU has total 58.5. Its attached annual advanced features visibly contain season-end information in week-one rows; **none of those features or its precomputed targets are used**.

Outputs: `cfbd_supplement_2024_2025.parquet`, `andrew_manifest.json`, and the raw CSV. [`historical_source_sensitivity.py`](../scripts/historical_source_sensitivity.py) fits the two existing opponent models on primary data from earlier seasons only, uses prior-season residual RMS for uncertainty, and changes only the tested market source. It also reports exactly shared cohorts to distinguish quote effects from sample-composition effects. Results appear in `model/reports/source_sensitivity.md` and `.json`; these are development diagnostics with assumed −110 prices, not realized returns.

## 3. SportsDatabase/SDQL long historical archive

Source: [jampdx/sdql2](https://github.com/jampdx/sdql2), commit `e8783b7f02da6a883904ed2e0bb03d75532e7214`, `Data/ncaafb_2019.csv`. The project identifies itself as the engine used by SportsDatabase.com. The repository uses GPL-3.0; the independent rights attached to underlying bookmaker data were not separately clarified. Its tree contains NCAA football season files from 1989 through 2020, making it a possible historical expansion source.

The downloaded 2019 file has **3,064 team rows / 1,532 game pairs**, with totals in 1,529 pairs. It is not conventional CSV: unquoted bracketed lists contain commas. The local parser splits only top-level commas and never evaluates source code. Adjacent team rows must share date/total and opposite spreads. Ambiguous normalized name/date pairs are excluded before joining. Exact normalized full names, local calendar dates, and final scores produce **678 matched ESPN games** in `sdql_market_games_2019.parquet`.

Bookmaker identity and quote time are absent. Both advertised opening-total columns are completely empty in this 2019 sample. `total` therefore remains a historical quote of unverified opening/closing role and is excluded from the primary benchmark. `sdql_manifest.json` records the hash, pairing counts, and limitations. Older seasons have not been bulk-downloaded or treated as a free untouched holdout.

## 4. Other public leads assessed

- [SportsbookReviewsOnline NCAA archive](https://www.sportsbookreviewsonline.com/scoresoddsarchives/ncaafootball/ncaafootballoddsarchives.htm): classic endpoint returned 404 in this investigation. A third-party repository describes newer HTML season tables, but this was not independently retrieved; no inferred spreadsheet was used. No two-sided historical total prices were recovered.
- [Prediction Tracker NCAA archive](https://www.thepredictiontracker.com/ncaaarchive.html): publicly lists CSV seasons 2000–2025. Primarily a point-spread prediction archive; the `total` field's betting-line role was not established, so it was not imported as an over/under market.
- Public SportsbookReview scraper mirrors inspected covered NFL/NBA/NHL/MLB rather than NCAA football. A Kaggle multi-sport closing-odds listing offered only a tiny free sample and advertised paid full data; it was not purchased or represented as complete.
- Public weekly CFBD caches could provide pregame publication evidence if a Git commit predates kickoff, but this bounded pass did not validate a full seasonal timestamped archive. A file name containing a date is insufficient by itself.

## 5. Public play-by-play and conditional score distributions

The [SportsDataverse loader documentation](https://cfbfastr.sportsdataverse.org/reference/load_espn_cfb_pbp.html) and its [release asset listing](https://github.com/sportsdataverse/sportsdataverse-data/releases/tag/espn_cfb_pbp) establish no-key ESPN-derived play-by-play from 2004 onward, with one seasonal Parquet. The current published schema contains possession, period/clock, down/distance, start/end score, play and scoring types, drive IDs, penalty flags, and player participants. 2019–2025 files are roughly 33–59 MB each. The classic cfbfastR family starts in 2014; NCAA-native lower-division coverage starts in 2013. The initial feasibility pass inspected metadata/schema only. A subsequent [audited acquisition](../model/reports/PBP_RAW_ACQUISITION_AUDIT.md) preserved seven full bodies totaling 1,037,245 records; a separate [source identity diagnostic](../model/reports/PBP_IDENTITY_DIAGNOSTIC.md) and [fixed parser audit](../model/reports/PBP_STATE_PARSER_AUDIT.md) document ordering and scoreboard limitations.

The first completed play-state experiment retained 762,297 states and constructed two strictly earlier-play conditional ratings for recorded clock consumption and conversion. Adding them to the existing 11-feature ridge did not establish an improvement: the existing ridge won 2021–2024 selection, and the new model's tiny 2025 gain over it had a paired uncertainty interval spanning zero while still trailing the market. See the [fixed v2 plan](../model/reports/PBP_STATE_RESEARCH_PLAN_V2.md), [all results](../model/reports/pbp_state_results_v2.json), and [independent numerical audit](../model/reports/PBP_STATE_RESULTS_AUDIT_V2.md). This point-forecast test did not evaluate a possession/scoring distribution, historical priced returns or a live policy.

These raw observations can support a better conditional distribution: separate regulation from overtime; estimate drive counts and points-per-drive variance; measure clock runoff only within unchanged possession and period; condition snap pace on score state and clock-stopping events; and preserve touchdown/field-goal scoring combinations and push mass. Fitting prior-game summaries avoids treating a current game's realized drives, pace, or participant list as pregame information. EPA/WPA columns are retrospectively modeled and may use market inputs; their presence alone does not certify out-of-time features. The existing strict prior-game drive summaries already capture part of this information, so any PBP candidate must demonstrate incremental value on the same clean market cohort.

A bounded next experiment would fix two distributions in advance: a market-centered discrete residual model whose variance depends on prior pace and absolute spread, and a regulation possession/scoring mixture with a separate overtime component. Fit only earlier seasons, score probability calibration and log loss alongside priced EV, and compare against the identical discrete market-centered baseline. Avoid using realized game weather, season-end roster summaries, or closing movement as morning-available predictors.

## Reproduction and evaluation boundaries

Reproducible scripts are committed under `scripts/`; raw datasets are not committed. Install the model dependencies, then run from the repository root:

```bash
python -m pip install -e ./model
python scripts/fetch_alternative_data.py --root .
```

The [download command](../scripts/fetch_alternative_data.py) obtains the six exact pinned market-source files, verifies their expected SHA-256 values, obtains missing public 2019–2025 schedules, and invokes both normalizers. Verified cached sources need no network request. A wrong hash fails before the destination is written. SportsDataverse release assets are mutable: their retrieved hashes and metadata are recorded, and a later re-download can produce different coverage. This command never executes source-repository code.

The normalizers can also be run independently:

```bash
python scripts/normalize_cfbd_public.py --root .
python scripts/normalize_alternative_supplements.py --root .
```

To reproduce the model-source sensitivity from a fresh checkout, first obtain the feature history and reconstruct the approved pregame-provider archive. The repair makes public requests per game and caches responses; the original scalar betting file supplies only the audit universe, never an accepted quote:

```bash
python scripts/fetch_alternative_data.py --root . --with-features
PYTHONPATH=model python -m ncaaf_model.espn_provider_repair --root model
python scripts/historical_source_sensitivity.py --root .
```

[`historical_source_sensitivity.py`](../scripts/historical_source_sensitivity.py) accepts optional `--market-file /path/to/normalized.parquet`. That file must use the market-only supplement schema described above, including game IDs, season/week, kickoff date, signed home spread, total, and outcome fields for grading. It never consumes the derivative CSV's annual features. The script fits only earlier primary seasons and writes its own report files; it does not overwrite primary fitted artifacts or history. Existing verified feature caches are reused only when their source-and-history fingerprint matches.

Small offline checks cover malformed SDQL rows, bracketed lists, immutable source pins, digest failure before writing, and cached-download behavior:

```bash
python -m pytest -q scripts/tests/test_alternative_data.py
```

The fixed weather hypothesis has a separate line-source check. It preserves the frozen flags in `model/data/raw/weather_research/game_results.parquet`, changes only `market_total`, compares identical primary/secondary cohorts, and refunds integer-line pushes. With the weather evaluation and CFBD supplement present:

```bash
python scripts/weather_source_sensitivity.py --root .
```

This produces `model/reports/weather_source_sensitivity.json` and `.md`; it performs no download, new threshold search, or probability fitting. The publisher includes this report only with its matching weather-plan hash.

After regenerating research result JSON, rebuild the site evidence bundle and readable annual report:

```bash
python scripts/publish_research.py --root .
python scripts/publish_research.py --root . --check
```

The [publisher](../scripts/publish_research.py) rejects mismatched primary/sensitivity/model fingerprints, includes all evaluated seasons, and separates quarantined earlier reports from current repaired-data evidence. Its output is deterministic for the current report inputs. Optional weather-result summaries remain labeled shadow research.

All 2019–2025 periods have already influenced this project. Newly acquired lines do not turn those outcomes into an untouched test. Earlier uninspected seasons could be frozen as a backward-era robustness check, but cannot establish present-day profitability given rule and market changes. The defensible untouched test begins with immutable future forecast/quote records after specification freeze. Historical opening/closing fields can study line movement, but cannot substantiate morning execution, actual payouts, limits, or achieved closing-line value without dated offered prices.
