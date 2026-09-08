# Frozen prospective evaluation — totals-v3-20260908

The specification is frozen on September 8, 2026. Its first successful archived live run begins prospective collection. Earlier historical and local-ledger results do not count as validation of this version.

## Candidate set and decisions

Thirteen candidates are recorded: market_consensus_loo; pace_efficiency; market_residual_ridge; market_residual_hgb; market_residual_ensemble; public_fpi; public_fei_epa; public_summary; public_roster_prior (prior ratings only, unaudited current roster fields excluded); public_full_hgb; public_superensemble_v2; drive_clock_shrink; and drive_clock_ridge.

Only market_consensus_loo supplies the main paper pick list. Every execution quote must have a genuine sportsbook/market update timestamp between five minutes in the future (clock tolerance) and 60 minutes in the past; an HTTP retrieval time is not a substitute. The reference contains at least three other unique allowed books with valid paired prices. Maximum peer line range is two points; maximum price-implied center range is three points.

Each book's paired Over/Under probabilities are de-vigged and converted to a location parameter matching the probability conditional on no push. The median peer location is the market projection. EV equals win probability times net payout minus loss probability. Integer pushes refund the unit stake.

The main gate requires modeled EV >=3%, line advantage >=1 point, and stressed EV >=1%. The stress envelope shifts the center one point against the bet, varies residual sigma by ±15%, and checks both discrete normal and Student-t(7) families. This is a fixed sensitivity test, not a 95% confidence bound. It cannot eliminate systematic market bias or bad source data.

Challenger signal gates additionally require a six-point forecast difference from the market and at least five prior games per team. Signals are tracked with zero bankroll allocation and do not populate main picks. The shrink drive candidate cannot reach six points because its forecast adjustment is capped at four; it is explicitly a forecast-only comparator. Missing current statistics disable challenger signals.

One best side and book is considered per model/game/run. The first eligible entry per model version, candidate and game is locked permanently. Further posts are observations, not additional wagers. Flat one-unit returns are used for evaluation. Main positions also show illustrative paper exposure capped at 0.25% of bankroll; it does not change flat-unit ROI.

## Evidence and uncertainty

Primary forward metrics: net unit ROI at the recorded line/price, log loss and Brier score for recorded forecasts, calibration, coverage, and number of abstentions. The result includes overtime when the market's standard game-total rules include it; abnormal void/cancellation decisions require verification rather than inferred settlement.

Daily forecasts use information available by the actual run timestamp. Statistical history is refreshed from completed games; reconstructed historical availability uses a conservative six-hour delay after kickoff, which is only a proxy. Exact live feature snapshots are stored so subsequent source revisions cannot overwrite what the model saw.

The site reports calendar-week block-bootstrap ROI intervals only after at least 50 settled independent game positions across 8 calendar weeks. Earlier results show insufficient evidence. Those intervals are descriptive and do not account for optional stopping or all prior model searches.

No automated promotion occurs. Formal promotion review is precommitted to once after the 2026 season, with a minimum 300 settled positions and 12 calendar-week clusters for the candidate. Any comparison across the 13 candidates must use simultaneous/familywise 95% intervals (for example, Bonferroni-adjusted week bootstrap) and separately inspect calibration, source quality and concentration. If volume is insufficient, the candidate remains unvalidated; thresholds must not be relaxed to manufacture a result. Any later design change starts a new model version and fresh prospective comparison.

Closing-line value remains null because the morning job does not reliably capture an independently timestamped pre-kickoff close. A later morning quote, a different line's price, or a model-selected execution quote must never be called the closing line.

## Operational rules

GitHub Actions targets 06:30 Eastern daily, with 06:45 and 07:00 guarded retries. DST is handled by the IANA timezone. Scheduler latency is possible. The frontend suppresses stale selections after 26 hours or when its board date no longer matches today. A failed feed publishes an unavailable state and a failed workflow. The football-season check permits January 2027 postseason under the frozen 2026 model, then requires a new-season review.

No bets are placed by this repository. No historical profitability claim is made for its line-shopping policy because timestamped multi-book historical prices are absent.
