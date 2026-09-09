# Weather criterion and forecast provenance audit

Audited 2026-09-09 UTC. Read-only method review; no thresholds, selection rules, forecasts, or model results were changed.

The combined criterion is correctly transcribed. The historical result remains a forecast adaptation of an externally published hypothesis, with unresolved archival provenance and execution differences; it is not independent proof of a current profitable strategy.

## Published criterion verified

Salaga and Howley, published online November 14, 2022, **Table 4, first row (PDF page 4)**: take the under when **wind > 7.78 mph AND temperature < 64.81°F AND relative humidity > 0.568** (56.8%). All comparisons are strict. The row reports **409 games and 58.92% wins**; Section V explicitly confirms the intersection.

Section II (page 2) covers 2012–2015 regular/postseason FBS games, excluding indoor games, missing closing totals, and pushes: 3,038 observations. Weather came from the nearest reporting location. Section III measures realized wind over actual contest duration and temperature/humidity at contest start. Table 1 gives means 7.781, 64.812, and 0.568; Table 4 rounds the first two. Section V selects significant variables using this sample and also evaluates two stricter threshold combinations. No later-period forecast holdout is reported. [Author-uploaded primary paper, Sections II–V and Tables 1/4](https://www.researchgate.net/publication/365476539_The_impact_of_weather_on_betting_outcomes_and_market_behaviour_in_the_NCAA_football_totals_market); [DOI](https://doi.org/10.1080/13504851.2022.2146651).

## What this implementation tests

Local `weather_shadow.py` uses the displayed first-row thresholds, with forecast kickoff-hour temperature/humidity and a fixed four-hour mean wind. Half-hour kickoffs use the preceding whole hour. It uses stadium grid coordinates, not a nearest observed-weather station. These predeclared substitutions avoid using future actual game duration but can change which games qualify. Forecast error, grid exposure, measurement height, temporal interpolation, and within-hour changes can move values across the thresholds. We have not estimated forecast-versus-observation classification agreement and therefore cannot quantify this transport error.

The local backtest uses repaired historical pregame totals with an assumed -110 price, not independently timestamped 06:30 quotes. A current offer paying at least -110 avoids a worse payout assumption but cannot establish equivalence between its total and the historical reference total. The 2024–2025 calculation is temporally external to the publication, but our project inspected those seasons during broader research; it is not an untouched project holdout. Its 85 selected bets out of 1,160 covered games are evidence for prospective paper testing, not a calibrated individual-game probability.

## Archive field and timing audit

The [Previous Runs documentation](https://open-meteo.com/en/docs/previous-runs-api), “Hourly Weather Variables,” lists day-2 relative humidity at 2 m and wind speed at 10 m. “API Documentation” describes fixed 48-hour offsets and six-hour global-model cycles. “Data Availability” says most archives start in January 2024; the March 2021 exception is specifically GFS temperature. It also permits reconstruction of additional historical coverage from upstream sources. This is no blanket certification that every returned record was publicly served at its nominal reference time or never reconstructed.

Current primary implementation corroborates field-level archival support: [GfsVariable.swift, metadata](https://github.com/open-meteo/open-meteo/blob/4455debbb5244c469a39ddf612aa16393b624a42/Sources/App/Gfs/GfsVariable.swift#L136) enables previous-forecast storage for temperature, relative humidity, and both 10 m wind components. The [GFS downloader](https://github.com/open-meteo/open-meteo/blob/4455debbb5244c469a39ddf612aa16393b624a42/Sources/App/Gfs/GfsDownload.swift#L408) derives relative humidity from same-step forecast fields where necessary. This audit found no indication that humidity silently substitutes realized observations.

The [archive writer](https://github.com/open-meteo/open-meteo/blob/4455debbb5244c469a39ddf612aa16393b624a42/Sources/App/Helper/OmFileSplitter.swift#L373) skips each incoming run's first N×24 hours for day-N storage. Under normal ingestion, day-2 history consequently retains a run at least 48 hours old, potentially older at intermediate cycle hours; it is not one common initialization for all four wind hours. Future day-2 values may be provisional longer-lead forecasts. The prospective helper rejects early cached responses rather than allowing them to become mature merely as time passes. These are inferences from the current source, not proof of every historical ingestion event.

The [Single Runs documentation](https://open-meteo.com/en/docs/single-runs-api), “Background,” separates initialization from dissemination and gives typical global-model delays of 4–6 hours. It specifically identifies early ECMWF IFS HRES coverage as Cycle 49R1 hindcasts. This project requests GFS Previous Runs, so that named ECMWF product is excluded. The six-hour buffer is conservative policy, not a verified per-run release timestamp or an absolute upper bound on outages. Day-2 responses do not supply those original timestamps. Local research correctly labels historical availability as a proxy; it must not be relabeled “verified contemporaneous public forecasts.”

Locally, all 215 saved batches explicitly request all three day-2 fields with `models=gfs_global`, Fahrenheit, mph, and GMT. The completed evaluator found usable fields for all 1,160 venue-eligible games. This establishes requested coverage, not archival integrity before receipt in September 2026.

## Independent validation search

A bounded search for the paper/authors plus forecast replication and NCAA weather totals studies found no independent replication of this exact three-condition forecast strategy. That search is not evidence that none exists. A separate author's [2021–2025 totals analysis](https://bluechipanalytics.com/research/what-moves-college-football-betting-lines/), “Part two” and reproducibility appendix, reports that much of a wind association is already reflected in closing totals; its weather is realized, its thresholds differ, and its uncertainty ignores repeated-team/search effects. It neither validates nor directly refutes this candidate. No outside historical win rate is transferred into current probabilities.

Remaining decisive evidence: immutable prospective forecast and executable-price receipts, settlement under fixed rules, enough independent future weeks, and uncertainty that accounts for the research search. No deal-breaking intersection transcription error or demonstrated GFS live-field leakage was found in this bounded review.
