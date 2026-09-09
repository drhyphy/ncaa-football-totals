# Forecast-weather shadow research

The public-data implementation is feasible. `ncaaf_model/weather_shadow.py` now fetches and immutably archives a fixed GFS forecast product, verifies the actual game venue, checks temporal availability, and returns a frozen weather research flag. It does not return a betting probability, expected profit, eligible bet, or stake. The 22 focused tests pass.

## Research hypothesis and limits

Salaga and Howley's published Table 4 reports a 58.92% under win rate in 409 games meeting all three conditions: wind above 7.78 mph, temperature below 64.81°F, and relative humidity above 56.8%. Their sample covers 2012–2015; their wind measure averages **actual** game-duration conditions, while temperature and humidity are measured at the start. The weather thresholds use sample means and were evaluated in that same sample, so this is motivation for a new test, not a calibrated 2026 win probability or a clean forecast-based holdout. [Author-uploaded paper](https://www.researchgate.net/publication/365476539_The_impact_of_weather_on_betting_outcomes_and_market_behaviour_in_the_NCAA_football_totals_market).

The new version freezes those three thresholds, uses kickoff-hour forecast temperature and humidity, and averages forecast wind at kickoff hour and the next three hours. A half-hour kickoff uses the hour immediately before it. The four-hour window is fixed before outcomes and does not use realized game length. This is a forecast adaptation of the research hypothesis rather than an exact replication. No alternative thresholds or windows were tested to select a favorable outcome.

## Genuine forecast evidence

Open-Meteo's Previous Runs API documents `previous_day2` as predictions at a fixed 48-hour offset before each valid hour. Most variables have history beginning in January 2024; GFS temperature has longer history, but that alone does not establish equally long wind/humidity coverage. A bounded request for Scott Stadium's grid on September 7, 2024 returned all 24 hourly values for all three required variables. This confirms one archive sample, not complete nationwide or season coverage. [Previous Runs documentation](https://open-meteo.com/en/docs/previous-runs-api).

The archive provides lead-time provenance rather than an original public dissemination timestamp. We retain that distinction and add a six-hour delay buffer after the latest nominal forecast reference time among the four wind hours. Reusing a later-fetched archive for an earlier decision requires explicit `allow_historical_lead_proxy=True` and remains labeled a research proxy. Live forecast replay always requires the recorded receipt to precede the decision, even if historical-proxy permission is set.

Model initialization also precedes actual public availability. Open-Meteo describes typical global-model delays of four to six hours and identifies early ECMWF Single Runs coverage as Cycle 49R1 hindcasts. This module fixes `gfs_global`, rejects reanalysis and unapproved endpoints/models, and does not substitute those ECMWF hindcasts for operational history. [Single Runs provenance and timing](https://open-meteo.com/en/docs/single-runs-api).

## Actual captured forecasts

At **2026-09-09 00:24 UTC / September 8, 8:24 p.m. Eastern**, we captured these current GFS forecasts for September 11 at 7 p.m. Eastern:

| Game | Verified venue | Kickoff temperature | Kickoff RH | Fixed four-hour wind mean | Frozen rule |
| --- | --- | --- | --- | --- | --- |
| Norfolk State at Virginia, ESPN 401858220 | Scott Stadium, 3923 | 75.9°F | 69% | 2.600 mph | False |
| Richmond at NC State, ESPN 401858222 | Carter-Finley Stadium, 3670 | 89.5°F | 44% | 7.875 mph | False |

These are archived **live forecast** illustrations, labeled separately from the fixed-lead study. The corresponding day2 product is correctly marked unavailable for the current decision: the nominal reference time for some requested valid hours is still in the future. Even if an API returns values, the temporal gate prevents treating those values as the prescribed completed 48-hour forecast product. By a game-day morning decision, the fixed-lead window can be evaluated if the required fields exist.

Five immutable compressed responses, including the historical archive probe, are in `data/raw/weather/`. `weather_forecast_capture.json` records exact requests, receipt times, venue IDs, coordinates, provenance, source paths, and rule results. Raw forecasts are local research inputs; the manifest is the portable report. No weather-based position has been posted.

## Venue and validation policy

The current ESPN game summaries identify each actual venue and neutral-site status; the corresponding ESPN venue records confirm outdoor status. Coordinates come from Geoff Boeing's public geocoded stadium dataset, matched to the specific Scott Stadium and Carter-Finley Stadium identities, with only punctuation normalization for the latter. No home-team campus centroid or inferred home stadium is used. [Geocoded source dataset](https://github.com/gboeing/data-visualization/blob/main/ncaa-football-stadiums/data/stadiums-geocoded.csv), [Virginia game identity](https://site.api.espn.com/apis/site/v2/sports/football/college-football/summary?event=401858220), [NC State game identity](https://site.api.espn.com/apis/site/v2/sports/football/college-football/summary?event=401858222).

Unknown roof status, indoor venues, neutral sites or unknown neutral status, mismatched venue IDs, expired venue metadata, wrong units, missing forecast hours, invalid numeric values, and forecast-location mismatches all yield **unavailable**, not a negative weather flag. Current roof metadata is explicitly date bounded and cannot silently validate a 2024 roof configuration. Model-grid coordinates are retained separately from requested stadium coordinates because GFS represents a nearby grid cell rather than field-level airflow.

Future evaluation should use the frozen day2 variant as one prespecified shadow candidate; illustrative live captures must not be pooled into that policy. Store each decision's actual price and game identity, evaluate outcome/ROI uncertainty with week-level dependence, compare against the same games' market-only forecasts, and apply the overall prospective program's multiple-candidate correction. Forecast error, stadium exposure, market adaptation since 2015, and forecast-versus-realized measurement differences remain empirical questions. No profitability claim follows from the published historical percentage or these two current examples.
