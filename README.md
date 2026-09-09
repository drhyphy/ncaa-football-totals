# NCAA football totals laboratory

Public-data NCAA totals forecasts, exact sportsbook-price comparisons, and an immutable prospective paper record. [Live dashboard](https://drhyphy.github.io/ncaa-football-totals/). GitHub Actions targets **6:30 AM America/New_York** daily, with guarded recovery attempts. Additional lightweight observations measure subsequent market movement.

**The goal has not yet been achieved: high-confidence profitability is not established.** The project now separates a workable experimental betting strategy from the evidence needed to trust it.

The strongest new lead is a separately published weather under criterion, replicated with forecast data on 2024–2025 games: **55–30, +23.53% at assumed −110**; descriptive week-bootstrap interval **+1.42% to +43.92%**. Four-candidate multiplicity sensitivity gives **−4.15% to +51.21%**, crossing zero; the full earlier research search count is unknown. A second line archive gives **52–27–1, +25.34%** on the 80 shared selections. It now has its own daily paper strategy and immutable ledger. Its fixed weather and price rules can qualify a selection without inventing individual-game win probabilities. See [weather replication](model/reports/weather_published_hypothesis_results.md), [source sensitivity](model/reports/weather_source_sensitivity.md), [independent audit](model/reports/weather_independent_audit.md), [primary source audit](model/reports/WEATHER_PRIMARY_SOURCE_AUDIT.md), and [statistical robustness](model/reports/weather_robustness.md). Reused development data and unverified historical quote times remain material limits.

## Version 4

- Two opponent-adjusted candidates estimate scoring, efficiency and tempo from prior completed games. Offense and opposing defense are fitted separately, with recency decay and partial pooling. A residual model combines those predictions with the current market; a fixed structural blend provides a competing candidate.
- A price-reference candidate and an exact two-book hedge scanner use the existing DraftKings/FanDuel feed. A better quote is distinguished from positive expected profit and from an all-score hedge floor.
- Publication requires two actual books, at least 3% estimated EV and at least 1% EV under the specified stresses. The unreachable four-book/six-point constraints are removed. These are experimental selections, not claims of proven profitability.
- Quote-change timestamps and receipts of current full-state odds are separate. Unchanged markets can be currently observed. An archived response cannot acquire a new receipt timestamp merely by being read again.

## Historical-data correction

The deeper audit confirmed that the old ESPN scalar archive includes **live-game totals**. For example, SMU–TCU in 2024 used 107.5 from an explicitly live provider; the separate opening field was 57.5. Previous closing-line backtests are quarantined. The replacement sample uses public CFBD archives and verified pregame-provider ESPN records, excluding live-only records. Actual historical total-side prices and quote timestamps remain unavailable, so assumed -110 results are exploratory.

See [the primary-source audit](model/reports/espn_market_timing_audit.md), [opponent-model development](model/reports/opponent_adjusted_development.md), [the two-book audit](model/reports/TWO_BOOK_OPPORTUNITIES.md), and [the current protocol](docs/PROSPECTIVE_PROTOCOL.md). Earlier failed experiments remain available for audit; reused historical data are never called a pristine holdout.

## Prospective evaluation

The [fixed evaluation protocol](model/reports/PROSPECTIVE_EVALUATION_PROTOCOL.md) covers September 9, 2026–January 31, 2027 games and sets February 8, 2027 as the evaluation date for the four current policies. Running results remain descriptive, with no interim promotion; historical results and reconstructed replays are excluded.

Each version/candidate/game keeps its first qualifying selection and exact price. Predictions that abstain are also recorded. Grades account for integer pushes, overtime and score corrections. Calendar-week bootstrap intervals accompany sufficiently large return samples. The near-kickoff collector compares only later same-book observations strictly before kickoff, within 30 minutes; it does not manufacture an exact closing price.

There is no wagering integration. GitHub stores the existing `ODDS_API_IO_KEY` as an encrypted repository secret; no subscription or bookmaker selection was changed. Local runs may read the existing workspace `.env`. Public data, model artifacts and ledgers contain no credentials.

## Run

Use Python 3.12 and the committed dependency lock:

```bash
python -m pip install -r model/requirements-lock.txt
cd model
python -m pytest tests -q
python -m ncaaf_model.runtime daily
python -m ncaaf_model.closing_collector
```

Historical research additionally needs the documented raw public archives, which are cached locally and excluded from Git. The daily cloud runner uses compact committed history and fitted artifacts, refreshing current-season observations. Run model-development commands only in a research checkout; changing a published strategy requires a new version. Failed refreshes publish an explicit unavailable state. GitHub schedules can be delayed; the dashboard shows actual observation/publication times.
