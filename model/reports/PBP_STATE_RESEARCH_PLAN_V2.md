# Fixed play-state feature experiment: local-order correction

Specification date: September 9, 2026. This experiment tests whether two conditional ratings extracted from archived play records improve the existing repaired 11-feature totals model. It is a new information family, not a rebranding of the already tested aggregate pace/efficiency features. All historical years remain reused development data. No profitable edge is presumed.


## Pre-performance source correction

Version 2 supersedes the initial feature build for matchup evaluation. Version 1 was frozen at `2277702`; its 5,008-game cache, 693,329 retained play states and [original feature audit](pbp_state_feature_audit.json) remain preserved. **No matchup predictions or performance scores were calculated from that initial build before this correction.** Model family, predictor equations, weights, penalties, evaluation cohort, annual folds and selection rules remain fixed.

The [identity diagnostic](PBP_IDENTITY_DIAGNOSTIC.md) found duplicate `sequenceNumber` values in 414 of 956 archived 2025 games (72,693 raw records in those games), while raw play IDs and `game_play_number` remain unique. Primary publisher code assigns regulation play numbers after sorting raw IDs; that index is not independent chronological truth. The fixed diagnostic sample shows that switching wholesale to play-number order would introduce additional clock/quarter reversals.

The correction retains `sequenceNumber` as the primary order and uses only locally unambiguous segments. A tied-sequence record is an explicit barrier: it contributes no state or response, the first following state cannot use its score, and clock pairs never bridge it. Missing identities or duplicate raw IDs/play numbers still reject an ambiguous game. Global disagreement between sequence order and play-number order no longer rejects the whole game. Instead, a pre-score requires unique predecessor/current sequence numbers and consecutive play numbers; a clock response additionally requires a unique next sequence number and consecutive endpoint play numbers. Other state, possession, period, clock and exclusion rules remain fixed. The first row and first state after a numbered gap remain unavailable.

No clock-based tie breaker, filled score, reordered ambiguity block, new eligibility threshold or favorable outcome subset is introduced. This local agreement check does not certify that the publisher's sequence or scoreboard is correct. The [fixed 14-game parser audit](PBP_STATE_PARSER_AUDIT.md) separately documents archived scoreboard noise and one missing game. Version 1 source/code remains reproducible from its frozen commit; version 2 receives separate machine-plan and normalized-output paths and a new freeze before rebuilding.

## Sources and staged execution

Use exactly the seven 2019–2025 bodies in [the audited source inventory](PBP_RESEARCH_SOURCE_INVENTORY.json). The inventory reconciles the 2025 checksum against fresh GitHub release metadata while preserving its original failed acquisition receipt and original stored path. Version 1 pinned its inventory, source audit, implementation, tests, specification, verified market cache and schedules before the initial play-value read. Version 2 transparently uses the resulting source-quality findings and pins the corrected implementation and diagnostic before rebuilding; no matchup model performance has been inspected. Do not refresh any source or add 2026 outcomes.

Only the parser's explicit raw-column allowlist may be read. The files are enriched ESPN-derived archives: even raw-named fields may have been repaired by the publisher. Current EPA, WP, published success measures and other fitted publisher metrics are excluded. Canonical game/team/season identities and completed status come from the separate archived schedules. Historical availability remains the disclosed kickoff-plus-six-hours proxy; September 2026 retrieval and correct temporal joins do not prove original publication timing or remove later source corrections.

Stages are separate: (1) commit specification, code, tests and machine freeze; (2) parse/describe coverage and construct strictly earlier-play ratings, without scoring matchup predictions; (3) commit the feature audit and its immutable cache hashes; (4) evaluate 2021–2024 and commit its choice; (5) compute the separate reused 2025 check. Report all configurations and failures. A source-semantic problem may require a documented correction before scoring; it must not be concealed or selected using prediction performance.

## Two historical play responses

Preserve integer play/game/team/order IDs without a floating-point round trip. Reconstruct pre-play score from the preceding consecutive archived row's post-play score; the first row has unavailable pre-score even when its processed index is one, and the first state immediately after a game-play-number gap is withheld with a specific exclusion count before resuming on consecutive records. Ambiguous identity/order segments and invalid state are excluded with counts under the version-2 local-order rules above. Regulation state requires period 1–4, game clock 0–900 seconds, down 1–4, distance 1–100, and yards to end zone 1–100.

Use the fixed ordinary pass/rush, offensive touchdown, sack, interception and fumble type sets documented in the official [type vectors](https://github.com/sportsdataverse/sportsdataverse-py/blob/main/sportsdataverse/cfb/model_vars.py) and encoded in the parser. Explicit special-team provenance, penalties/nullification, kneels, spikes and administrative records are excluded. An appended PAT description does not itself invalidate an offensive touchdown. Unknown nullable exclusion flags are counted, not described as verified false. Fumble types can be ambiguous; original types, valid scrimmage state and kick/punt flags constrain inclusion. No type-only claim of perfect classification is made.

**Conversion:** whether a legal scrimmage play gains at least the lesser of the line-to-gain distance and yards to end zone while retaining possession, or unambiguously scores an offensive touchdown. Lost turnovers and defensive return scores are failures. Retained offensive fumbles are judged by gain/TD. Missing yardage/end possession produces an unavailable response unless an explicit turnover or offensive touchdown establishes it. This is a conditional descriptive target, not published EPA or an arbitrary percentage-of-distance success rule.

**Recorded clock consumption:** current game clock minus the next archived record's clock, restricted to literally adjacent records in the archived sequence ordering, both eligible scrimmage rows with the same game, drive, possession and period, retained possession after the current play, and a decrement from 0 through 60 seconds. Never bridge a discarded record or a missing processed index. This measures game-clock consumption between archived records, not verified snap-to-snap tempo. Earlier publisher removal/repair of rows remains a limitation.

Output raw-derived state, independent nullable responses, canonical IDs and schedule availability. No target matchup's own PBP coverage may determine whether that matchup is evaluated.

## Conditional ratings and final predictors

At each existing Monday-midnight Eastern cutoff (or an earlier actual `as_of`), use only games with availability strictly before the cutoff and within the preceding 3×366 days. Recompute each historical row's features at its own cutoff. Never fit through 2025 and backfill earlier ratings.

Fit separate weighted continuous ridge regressions for clock seconds and binary conversion. These are rating estimators, not released probability distributions. Use `Ridge(alpha=4, fit_intercept=True, solver='lsqr', tol=1e-7)`, offense and opponent-defense indicators, nonneutral home status, and this fixed state basis:

- Down 2/3/4 indicators; `log1p(distance)/log(101)` and `min(distance,20)/20`.
- Yards to end zone divided by 100 and its square.
- Possession score margin clipped to ±28 divided by 28, and its absolute value.
- Seconds remaining in the half divided by 1,800 and its square; period 2/3/4 indicators.
- Pass indicator, zero-filled only when unknown, and a separate pass-unknown indicator.
- Indicators for the 2023 clock rule and 2024 two-minute rule eras.

Do not learn a state transform from future seasons. Within each response, divide each team-game's recency weight equally among its eligible plays. Its total weight is the existing `.92**max(0, years*18 + query_week - history_week) * .65**years`; years is nonnegative query-season minus historical-season. Thus a game with more recorded plays does not receive extra team-rating weight solely for that reason. Team maps use training data only. Missing historical teams contribute zero; there is no additional PBP history eligibility threshold. Keep response-specific coverage and fit metadata.

For each response, the one final matchup predictor is half the sum of home offense, away defense, away offense and home defense coefficients, excluding intercept, home advantage and state terms. The two predictors are clock seconds and conversion **percentage points** (raw conversion rating multiplied by 100). This unit conversion prevents the inherited final-model scale floor of one from effectively suppressing a rate-valued input. Neither sign nor a points-per-second conversion is imposed.

## Fixed matchup comparison

Use the verified 5,008-game 2020–2025 cache and its existing five-prior-game eligibility condition, including integer market lines. Evaluate exactly the same games for all configurations; no target-game PBP coverage gate or candidate-specific subset. Configurations, in tie-breaking order, are `market_only`, `opponent_adjusted_ridge`, `pbp_state_ridge`.

The new model adds the two features above to the existing 11 predictors. Hold the final fit method fixed: predict score residual versus the market; training-only medians, means and population standard deviations with scale at least one; ridge penalty 200 on every coefficient including intercept; clip the residual prediction to ±10 points. The existing 11-feature fit must reproduce its saved repaired out-of-fold predictions before the new comparison proceeds. Only strictly earlier seasons train each annual fold. No hyperparameter, threshold, alternate feature or model search is permitted in this experiment.

Select by pooled game-weighted MSE across 2021–2024, using 2020 as warm-up. Commit that choice before computing new 2025 matchup forecasts or their scores. The 2025 check remains reused development data and cannot change the earlier choice. Preserve all three configurations in both periods and annual/source summaries.

Primary contrast is the added-feature model minus existing ridge in squared-error loss. Also report its market contrast and MAE differences. Use 10,000 PCG64 draws, seed 20260909, resampling whole Eastern Monday–Sunday calendar weeks; calculate ratios of resampled loss sums to game counts. Report descriptive 95% intervals and 99% sensitivity intervals. These do not adjust for the project's larger search history or eliminate shared-team dependence. No interval is produced with fewer than two week blocks.

No historical EV, assumed-price ROI, alternate-line/push probabilities, staking recommendation, active model replacement or forward-ledger modification is produced by this point-prediction study. Any improvement would need a separate probability and executable-price assessment and prospective evidence. “Earlier information” describes the feature construction; these regressions do not identify causal effects of coaching choices.
