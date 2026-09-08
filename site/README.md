# GitHub Pages daily board

This dependency-free static dashboard reads `data/board.json`. The Python model is run by GitHub Actions; GitHub Pages serves its published output. There is no API key in the browser and no service that places bets.

The first section contains today's qualifying pregame selections. Upcoming selections, historical candidate comparisons, all forecasts, and the forward paper record are separate. Missing metrics render as unavailable, not zero. Research-only status is visible throughout.

## Deploy

1. Push this project as the root of a GitHub repository with default branch `main`.
2. Under **Settings → Pages → Build and deployment**, select **GitHub Actions** as the source. Allow the `github-pages` environment to deploy from `main`.
3. Ensure **Settings → Actions → General** permits the workflow to write repository contents. The workflow explicitly requests contents write only for its publication job, and Pages write / OIDC only for deployment.
4. Ensure `model/requirements-lock.txt`, the trained artifacts in `model/data/models`, and any required seed files documented by the model are committed.
5. Run **Actions → Daily totals and Pages → Run workflow**. The deployment job publishes the final site URL.

The normal run starts at **6:30 AM America/New_York**, including daylight saving changes. Backup attempts at 6:45 and 7:00 AM skip a model refresh if an eligible same-day successful edition already exists, while still retrying deployment. A manual run always refreshes. GitHub can delay or drop scheduled events, so 6:30 is the requested start time, not a guaranteed publication deadline. Public repository schedules can be disabled after 60 days of repository inactivity. See [GitHub scheduled events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule), [native timezone support](https://github.blog/changelog/2026-03-19-github-actions-late-march-2026-updates/), and [custom Pages workflows](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).

The workflow runs tests before fetching live data. It commits only `site/data`, `model/ledger`, and `model/data/runtime`; unrelated files and large raw caches are excluded. A failed live refresh publishes an explicit unavailable state with empty selections, deploys that state, then marks the deployment job failed so it is visible in Actions. An infrastructure or test failure before publication leaves the prior site in place; its browser safeguards suppress current picks when the Eastern date changes or the snapshot exceeds 26 hours.

## Local preview and checks

From the repository root:

```bash
python -m http.server 8765 --directory site
```

Open `http://localhost:8765`. The model must have written `site/data/board.json`; otherwise the dashboard accurately shows an unavailable state.

```bash
node --test site/tests/*.test.cjs
python -m unittest discover -s scripts/tests -v
```

Frontend checks cover stale or failed publications, Eastern date boundaries, kickoff and quote expiry, duplicate ledger rows, and unsafe source links. Publication checks cover summer/winter 6:30 scheduling, same-day idempotence, failure retry, invalid dates, and fail-closed output.

## Data interface

Required root keys are `schema_version: 1`, `generated_at` (ISO timestamp with timezone), `date` (Eastern calendar date), `timezone: America/New_York`, and `status` (`ok` or `unavailable`). Arrays are `today_picks`, `upcoming_picks`, `forecasts`, `candidates`, `sources`, and `limitations`. Additional supported keys are `model_version`, `evidence_status`, `performance`, `diagnostics`, optional `results`, and optional `reports: [{name, url}]`.

The frontend renders all feed strings as DOM text, permits only HTTP(S) or relative source/report links, and rechecks freshness every minute while open. A current pick must have a future kickoff, a quote timestamp no more than 26 hours old, and the correct Eastern game date. Browser checks supplement the model's stricter publication rules; they do not certify a quoted price remains executable.

The forward table deduplicates on `(candidate, game_id)` using the earliest `recorded_at`. The runtime remains responsible for storing immutable original selection terms and updating their grades. Aggregate headline results are for the primary candidate named in `performance.candidate`, defaulting to `market_consensus_loo`.
