# Rich-feature reuse audit

Audited September 8, 2026. Scope: local code, raw and normalized cache schemas, source metadata, and read-only joins. No model was fitted, historical return calculation rerun, active artifact changed, or website edited.

**Recommendation:** build a separately versioned exploratory ridge/tree comparison using the repaired 5,008-game market universe and ordinary, prior-only game statistics. Reuse model factories and feature ideas, not the old public-ensemble training entry point, fitted weights, thresholds, probabilities, or cached market context. Rich publisher ratings can be a separately labeled reconstruction sensitivity after the availability problems below are addressed. This audit establishes a feasible implementation path; it does not establish predictive or betting advantage.

## Existing data that can support the experiment

Paths below are relative to `model/`.

| Cache | Observed contents | Appropriate use |
|---|---|---|
| `data/normalized/opponent_features_verified_cf764f803b38ecfd.parquet` | 5,008 games, 32 columns, 2020–25 | Base game universe and repaired market context; preserve provenance. |
| `data/models/opponent_history.parquet` | 11,926 team-game rows, 17 columns, 2019–25 | Prior-game ordinary statistics and drive history; recompute/verify query-time cutoffs. |
| `data/raw/sportsdataverse/adv_team_gamelog_{season}.parquet`, `drives_{season}.parquet`, `cfb_schedule_{season}.parquet` | Local seasons 2019–26; 2026 is partial | Rebuild new non-EPA forms, requiring completed-game status and information cutoff. |
| `data/raw/sportsdataverse/fpi_weekly_{season}.parquet` | Local seasons 2019–26; FPI values, computation dates, sequence flags | Conditional reconstruction sensitivity, with per-row availability rejection. |
| `data/raw/sportsdataverse/ratings_weekly_{season}.parquet`, `summaries_weekly_{season}.parquet` | Local seasons 2019–26; cumulative `through_week`; no publication timestamp | Reconstructed historical features, not verified original pregame publications. |
| `data/raw/sportsdataverse/ratings_final_{season}.parquet` | Local seasons 2019–25 | Previous-season sensitivity only; original publication/model vintage unverified. |
| `data/normalized/clean_public_features.parquet` | 3,152 rows, 176 columns, uneven 2019–25 coverage | Diagnostic reference only; the word “clean” does not mean repaired odds. |

The repaired universe has 538 / 848 / 849 / 910 / 920 / 943 games in 2020 / 2021 / 2022 / 2023 / 2024 / 2025. Sources are ESPN non-live provider 58 (2,680), CFBD consensus (2,235), ESPN provider 100 (47), CFBD Bovada (24), ESPN provider 40 (14), CFBD William Hill New Jersey (7), and ESPN provider 52 (1). `source_verified_pregame` denotes verified provider role; it does **not** establish a timestamped closing price or availability at 6:30 a.m. See [provider repair](espn_verified_provider_repair.md).

The repaired feature schema includes game/team IDs, date/week/season, scores and actual total, market total/spread, neutral venue/status, source flags, adjusted score/drive/clock totals and their market differences, pass/rush/share predictors, era indicators, history size, `ratings_cutoff`, and `ratings_training_rows`. No row in this cache has `ratings_cutoff >= game_date`. The history schema includes team/opponent IDs, start time, points, pass/rush efficiency and share, drives, seconds per drive, other points, drive scoring rate, and `available_at`.

The old public cache and repaired cache share 3,002 game IDs. Their market totals differ for 139 of these games, by as much as 9 points. Joining only those common games would also discard 2,006 repaired games. Therefore, do not make the old cache the new experiment’s sample or retain its implied-score/context columns. Old normalized walk-forward predictions and `opponent_features_v2.parquet` are not replacements for the verified cache.

## Feature classification

| Feature family | Reuse assessment | Required restriction |
|---|---|---|
| Existing adjusted score, drive, pace, pass/rush efficiency, history depth and era predictors | Best starting point, conditional on the existing reconstruction assumptions | Use the repaired cache and preserve its cutoff/source fingerprint. These are not independently archived historical forecasts. |
| Prior points for/against, drives, scrimmage plays, yards/play and allowed, pass share, ordinary drive scoring rates | Suitable for a new low-risk reconstruction | Derive only from completed prior games known before the query cutoff. Use a strict allowlist. |
| Rest days, season games, prior-year ordinary forms | Feasible with existing schedule/history | Compute from prior dates; never use a future schedule result or current-game box score. |
| FPI and its offense/defense/special-teams efficiency fields | Better timestamp metadata, still conditional | Require the contemporaneous flag, reject out-of-sequence rows, and require computation time strictly before the chosen historical/live cutoff. Preserve missingness. |
| Weekly adjusted EPA/“FEI” ratings and cumulative EPA summaries | Reconstruction sensitivity; not a verified historical information set | `through_week` alone does not prove original publication time or the historical vintage of the upstream EPA model. Do not silently include these in the conservative primary experiment. |
| Non-EPA pace/yardage/pass-rate fields from weekly publisher summaries | Recomputable more safely from raw completed games | Prefer direct reconstruction to trusting an undated aggregate’s week label. |
| Previous-season final ratings | Lower direct season leakage risk, timing/model vintage still unknown | Correct prior season only, separately labeled; direct ordinary-stat carryover is preferable. |
| Returning production, recruiting/talent and blue-chip fields | Excluded already in `public_features.py` | Keep excluded until contemporaneous preseason sources can be demonstrated. |
| Current-game score, winner, margin, attendance and unshifted box-score fields | Postgame data | Never predictors, even if numeric and present in a cached frame. |
| Injury status, starting quarterback changes, current roster availability, archived morning weather, historical price movement | Not supplied by these feature caches | Do not impute their existence or treat static team ratings as substitutes. Separate sources and availability audits are needed. |

The publisher documents weekly ratings as refits using games through the labeled week, and describes `fei_off`/`fei_def` as ridge-derived drive EPA ratings. These fields should not be presented as a separately archived external FEI product. This supports a reconstruction interpretation, not original publication at a specific hour. [Official ratings documentation](https://cfbfastr.sportsdataverse.org/reference/load_cfb_ratings_weekly.html)

FPI’s `last_updated` and sequence flags matter: a week label may point to a value computed much later. The existing code already rejects out-of-sequence rows and masks rows computed at or after the game/as-of cutoff. A within-season flag alone is insufficient. Computation time is still different from a verified local pregame receipt. [Official FPI documentation](https://cfbfastr.sportsdataverse.org/reference/load_cfb_fpi_weekly.html)

The 2024 ratings and summaries sidecars record retrieval in August 2026 and modification in August 2026, not receipts during the 2024 season. Their reconstructed statistics may be correct, but those receipts cannot certify historical dissemination. Upstream EPA-model training vintage was not established in this bounded audit.

## Join and timing findings

`attach_public_features()` can attach the current raw caches directly to the repaired universe: the read-only result was 5,008 rows × 135 columns with no duplicate games. Mean nonmissing feature coverage was:

| Season | FPI | Weekly ratings | Weekly summaries | Existing roster/prior coverage field |
|---|---:|---:|---:|---:|
| 2020 | 91.7% | 82.3% | 83.9% | 45.2% |
| 2021 | 84.8% | 81.0% | 81.7% | 42.5% |
| 2022 | 77.1% | 82.0% | 82.2% | 43.2% |
| 2023 | 73.6% | 78.2% | 80.2% | 42.9% |
| 2024 | 71.5% | 74.6% | 77.0% | 41.4% |
| 2025 | 72.5% | 76.9% | 78.6% | 42.1% |

These are field-coverage measures, not predictive results or source-validity percentages. The last column retains eight disabled roster fields and seven prior-rating fields per side in its denominator: prior-only full coverage is 7/15, so it must not be described as observed roster coverage. FPI cutoff-invalid metadata occurred in approximately 19.5% of home and 24.7% of away attachments, in addition to other missingness.

The current join is exact `(team_id, week - 1)` within each season, not a last-available as-of join. Enforce unique `(season, team_id, snapshot_week)` rows, normalized ESPN IDs, and many-to-one joins before reuse. FPI raw rows include duplicated team/week keys across season-type records; the loader currently sorts `run_date_time_key` and keeps the first. A new implementation should explicitly resolve season type and calendar cutoff, especially when postseason week numbering restarts. Do not silently pick a duplicate by row order.

Current 2026 ratings/summaries contain `through_week` values 1–15 although the season is only beginning. The maximum observed rating `games` is 2, and sampled Ohio State rows for weeks 2–15 repeat identical values. This is evidence of carry-forward generation, **not proof that future outcomes entered the file**. It does establish that `max(through_week)` cannot identify the latest completed week. Derive an explicit cutoff from the official schedule/as-of time and preserve it in the feature record.

`totals_features.add_pregame_forms()` correctly shifts each game before its EWMA, but its historical transform only sorts/shifts games. The live helper additionally filters `start_date + 6h <= as_of`; historical and live cutoffs must be aligned explicitly. `load_team_games()` filters completed games only when a schedule file exists, so a new experiment should require that file and fail on missing status. Its all-drive points-per-drive definition also differs from the opponent model’s drive scoring definition; give them distinct feature names.

`opponent_model` uses `available_at = start_date + 6h` as an availability proxy, then limits rating history to before Monday midnight Eastern (or the earlier supplied as-of). It does not establish actual publisher delivery time or exclude subsequent source corrections. Retain this disclosed assumption and test that later games cannot alter earlier features. Its current cache fingerprint includes module code, market rows, and history rows; a richer feature cache must additionally fingerprint its own feature code, source files and contract.

## Pipeline reuse and live readiness

The ridge factory uses train-fitted median imputation with missing indicators, scaling, and `Ridge(alpha=140)`. The tree factory uses imputation and a restrained histogram gradient booster: learning rate .025, 220 iterations, 10 leaves, minimum leaf 45, L2 20, seed 2026. These fixed factory settings are reasonable bounded starting specifications; their existence supplies no evidence that they beat the repaired baseline. Explicitly disable automatic early stopping for a chronological experiment so larger future training sets cannot introduce a random validation split.

**Do not invoke `run_public_backtest()`.** It calls `historical_game_features()`, which still reads the original `betting_{season}.parquet` scalar totals and accepts broad `core_odds_api`/`summary_pickcenter` tags. Those tags do not isolate non-live providers. The function then writes the old model, probability scale, threshold selection, predictions and reports. Even a fresh refit through this entry point would retain the market-provenance defect. The old serialized model is not safe to load for the new experiment.

Do not reuse its five-threshold × six-candidate selection or treat its nominal Bonferroni calculation as correcting the wider development search. Its scale is estimated from training market residuals, not the new candidate’s chronological out-of-fold prediction errors. Its assumed −110 return scoring and historical gates are not equivalent to current live quote, price, schedule, distribution and robustness checks. Fresh probabilities require their own prior-only calibration and push treatment; initially, prediction-error comparison can proceed without claiming individual EV.

The active daily `refresh_inputs()` refreshes schedule, advanced team-game logs and drives, plus ESPN scoreboards. It does **not** refresh FPI, weekly ratings, summaries or previous-season final ratings. `DataClient.archive_public_models_season()` can download those sources, but it is not in the current v4 refresh path. Local 2026 files are not evidence that GitHub’s daily job has fresh copies. Any later public-rating candidate needs explicit collection, receipt archiving, schema/freshness checks and a documented no-signal policy when required inputs fail.

## Concrete implementation path

1. Create a separate exploratory module, cache, model identifier and report namespace. Preserve active v4 and the four-policy prospective protocol. Adding a candidate now is new development; it cannot inherit their registration or historical evidence.
2. Start from `opponent_model.load_market_games()` and the verified adjusted-feature cache. Assert one canonical game row, correct scores/IDs/status, and accepted source role. Preserve all 5,008 games before applying an explicit, reported history-eligibility rule.
3. Reconstruct a modest allowlist of ordinary non-EPA team forms from raw 2019–25 games. Use the same cutoff function and availability proxy for historical and live reconstruction. Join home/away states by ESPN IDs and query cutoff; never join current-game statistics directly as predictors.
4. Recompute every market-dependent field from the repaired total and spread: market differences, absolute spread, spread/total interaction, implied points and residual target. Exclude all old cached market context, fitted weights and selected thresholds.
5. Compare two prespecified factories—ridge and the restrained tree—using identical approved inputs and annual expanding training. Require adequate prior training rows; use 2019 for history warm-up. Report results by year and era, against market and repaired ridge on the same rows. The 2020–25 outcomes have already been repeatedly examined; even a separately reported 2025 slice is development validation. Existing 2026 replay is also retrospective, not a new holdout.
6. Fit imputation/scaling only within each training fold. Keep missing-input indicators and coverage/sample counts. Do not add stacking, a threshold grid, or dozens of feature subsets before inspecting the bounded comparison. If probabilities are later needed, derive their calibration only from earlier chronological out-of-fold residuals and evaluate proper scores as well as hypothetical returns.
7. Add targeted tests: future-outcome mutation leaves earlier features unchanged; historical/live queries at the same cutoff agree; duplicate provider/team rows cannot multiply games; missing status fails closed; poisoned postgame columns do not enter the allowlist; repaired market replacement updates all dependent fields; artifact/source fingerprint mismatch fails. Record source hashes and the full feature contract with each result.

Only after these steps should a separate public-rating sensitivity add timestamp-filtered FPI or explicitly reconstructed EPA/weekly products. Their outcome must not be blended into a claim that the conservative information set was available at historical 6:30 a.m. prices.
