# Prospective evaluation — totals-v4-20260908

The objective is a reproducible positive-EV strategy with credible evidence of profitability. An operational website, a positive point estimate, and a selected historical backtest do not complete that objective. Version 3 is archived in [the prior protocol](archive/PROSPECTIVE_PROTOCOL_v3.md); its quotes and forecasts remain immutable.

## Why this version exists

The old four-book requirement could not be met by the two selected books in the working feed. Its six-point challenger gate was also incompatible with some model adjustment caps. Those were specification errors. A deeper audit additionally found ESPN live-game totals in the historical scalar archive. All prior market-based profitability claims are quarantined; excluding only extreme totals cannot repair this problem.

The replacement historical sample combines public CFBD provider archives with ESPN historical records whose selected provider is explicitly a pregame provider. Records with only live odds are excluded. Historical observations still lack verified quote times and total-side prices: hypothetical -110 ROI is a development diagnostic, never an executable 6:30 AM return. Known source disagreements, selection exclusions and source hashes remain documented. Reused seasons are not relabeled pristine holdouts.

## Active candidates

1. **opponent_adjusted_ridge** supplies the main experimental pick list. Separate offense and opposing-defense regressions estimate points, points per drive, possessions, possession duration, passing/rushing efficiency and passing share. A regularized residual model combines their matchup predictions with the current market total and spread.
2. **opponent_adjusted_structural** uses a fixed 25% adjustment from opponent-adjusted points-per-drive predictions, capped at five points. It is a competing paper strategy.
3. **market_price_reference** compares the exact offered price and total with the other currently observed sportsbook's paired, proportionally de-vigged price. This explicitly assumes the peer price is informative; it does not establish truth or an independent confidence bound.

The two statistical candidates share a fixed specification rather than an outcome-optimized grid. Team ratings use prior observed games, a Monday 00:00 Eastern weekly cutoff capped by actual run time, shrinkage toward the league, in-season decay and offseason decay. The target game's outcome and subsequent games cannot enter its ratings. Historical game availability uses kickoff plus six hours; this is a conservative proxy, not an audited publication timestamp. Current-season raw observations refresh daily. Prior opaque EPA/power-rating candidates remain in the archived v3 record and are not mixed into the active v4 models.

## Publication rules

A paper selection requires a matched future game, a valid paired total/price at an allowed sportsbook, and at least one other distinct currently observed sportsbook. Book count is an execution/comparison requirement, not evidence of statistical confidence. Duplicate aggregators never add books.

For Odds-API.io, retain both the original market-change timestamp and the actual receipt of a successful full-state response. A full-state receipt can establish current provider presence even when a market has not changed recently. Receipt age must be at most 120 seconds at evaluation, and far-future update clocks fail. Re-parsing a saved response never refreshes it. Other supported sources still require a genuine market timestamp within 60 minutes. Untimestamped public aggregators cannot independently qualify a quote.

Both statistical candidates require at least five prior completed games for each team and a successful current-form refresh. Their training artifact must identify the repaired market source. Every candidate requires estimated EV of at least 3% and stressed EV of at least 1%. Stress shifts the score location one point against the proposed side, varies sigma by ±15%, and checks discrete normal and Student-t(7) distributions. Peer line range may not exceed two points and peer implied-location range may not exceed three. There is no additional six-point rule or minimum line advantage: a favorable price at the same total can have value.

EV is unconditional `P(win) × net payout − P(loss)`; integer pushes refund stake. Exact decimal payout is retained even when the displayed American price is rounded. A stress envelope is a sensitivity test, not a confidence interval. No candidate is described as proven profitable or high confidence merely because it qualifies.

## Paper accounting and independent evidence

The first eligible position per model version, candidate and game is locked. A later line change cannot rewrite entry terms. All first pregame forecasts, including abstentions, are separately stored for MAE, RMSE, conditional Brier/log loss and calibration. Final scores include overtime; corrections can change grades but never entries. Main-paper exposure is at most 0.25% of a hypothetical bankroll per position; comparator positions use zero bankroll allocation and a separate one-unit research ROI. No actual wagers are transmitted.

Morning publication targets 6:30 AM America/New_York with guarded 6:45 and 7:00 recovery attempts. Additional lightweight captures run twice hourly in daytime/evening football-season windows. They train no models and preserve complete quote observations. A same-book observation within 30 minutes before kickoff, after the locked entry, supplies a clearly labeled near-close comparison. It is not an exact close. Missing comparisons stay missing; post-kickoff data never fill them. Daily publication and capture share a workflow concurrency group to avoid ledger overwrites.

The cross-book scanner separately solves the exact two-leg payoff problem over every nonnegative integer score. A dominant quote is not positive EV. A positive hedge floor is conditional on both legs being accepted, compatible overtime/void rules, and applicable costs/limits. A candidate scan never represents an executed wager.

Review full results at the end of the 2026 season, with at least 300 settled positions and 12 calendar-week clusters before considering a high-confidence profitability claim. Use simultaneous intervals across the three active betting candidates, assess concentration, calibrated probabilities and near-close observations, and retain all losing results. Smaller samples remain exploratory. No automatic promotion or real-money execution occurs. Further modeling changes receive new versions and fresh prospective records.
