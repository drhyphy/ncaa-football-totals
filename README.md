# NCAA football totals laboratory

Daily public-data forecasts, timestamped sportsbook comparisons, and a prospective paper ledger. GitHub Actions targets **6:30 AM America/New_York** each morning and publishes to GitHub Pages. Guarded 6:45 and 7:00 attempts recover failed refreshes or deployments. GitHub can delay scheduled jobs; the displayed timestamp records the actual run.

**No model has established high-confidence profitability.** This repository reports that result rather than presenting a selected backtest as a proven betting edge. The dashboard can correctly publish no picks.

## What was developed

- A market probability model compares each offered line and price with at least **three other fresh sportsbooks**, using paired no-vig prices and a discrete final-score distribution. The execution book never helps establish its own edge.
- Four pace and residual models and six corrected public-rating models remain competing forecasts.
- Two new drive/clock models estimate regulation scoring opportunities, offensive TD/FG rates, opponent defense, elapsed clock, and offseason regression. They use identical historical and live feature construction.
- Normal and Student-t distributions were compared on expanding-season score log loss. The discrete normal model was selected for the frozen prospective specification; both distributions enter stress testing. Integer totals include the probability of a refunded push.

The 2019–2025 archive is reused **retrospective development data**. Corrected 2023–2025 comparisons cover 2,781 games. The market MAE is 12.463; the corrected public ensemble is 12.498 and public boosted model is 12.770. On the drive models' common 2,597-game sample, market MAE is 12.517, drive shrink 12.493, and drive ridge 12.495. The small drive improvements have week-bootstrap intervals crossing zero. No public model passes the profitability confidence gate.

Previous positive headline ROI was superseded after finding post-kickoff FPI revisions and synthetic default odds. See [the audit](model/docs/DATA_AUDIT.md), [model card](model/docs/TOTALS_MODEL_CARD.md), [drive study](model/reports/drive_clock_development.md), and [prospective protocol](docs/PROSPECTIVE_PROTOCOL.md).

## Daily decisions

Main picks are experimental market comparisons, with a 60-minute quote-age limit, schedule match, three other independent fresh books, narrow peer disagreement, at least one point of line value, at least 3% modeled EV, and at least 1% EV after fixed adverse sensitivity assumptions. These sensitivity values are **not statistical confidence bounds**. Main picks use small paper exposure only; challengers have zero betting stake.

Snapshots preserve book quotes, exact model features, model hashes and predictions. The first qualifying candidate/game entry is locked; daily repeats cannot increase its bet count. Results include pushes, actual offered prices, and calendar-week bootstrap intervals. Morning snapshots are not labeled closing lines. There is no automatic real-money promotion and no wagering integration.

## Data and credentials

Historical and current statistics use the no-key SportsDataverse release archive and ESPN public scoreboard. Timestamped current prices prefer an existing Odds API credential supplied as repository secret `ODDS_API_KEY`; both its FBS and separate FCS keys are requested. Public fallback sources preserve missing timestamps as missing. Missing or stale timestamps force abstention. The site reports partial source/coverage failures.

No credential is committed. The original moneyline site and research directory are unchanged. Source responses and cached raw historical files are excluded from the public source checkout; compact model history seeds and frozen artifacts make daily runners independent of a local computer.

## Run and reproduce

Use Python 3.12 and the committed dependency lock:

```bash
python -m pip install -r model/requirements-lock.txt
cd model
python -m pytest tests -q
python -m ncaaf_model.runtime daily
```

To reproduce development studies, first fetch historical public sources, then rerun the frozen studies. This overwrites development artifacts; use a separate research branch and a new version before changing the prospective specification.

```bash
python -m ncaaf_model.cli totals-bootstrap
python -m ncaaf_model.cli totals-backtest
python -m ncaaf_model.cli totals-public-backtest
python -m ncaaf_model.drive_model --root .
```

The historical SportsDataverse source is mutable, and earlier publication timestamps are not fully available. Archived source hashes in the audit identify the inspected files. This is not proof of executable historical morning returns.

The static dashboard reads `site/data/board.json`. The workflow persists only public board data, compact immutable runtime snapshots, and the forward ledger. Failed ingestion publishes an explicit unavailable state before marking the workflow failed.
