# Locked 2024–25 weather hypothesis request plan

The plan is frozen before downloading the test weather or evaluating outcomes. It includes **1,160 games in 215 batched HTTP requests** from the 1,863-game repaired 2024–25 market universe. Its SHA-256 is `d08836ca73abab7ab47cc63a18005acd1608e772d3ac5f721f15f7640794318f`. The machine-readable `weather_request_plan.json` contains every included identity, exclusion reason, coordinate, roof-source record, request, and source-data hash.

Selection uses actual scheduled venue identity, documented non-live market provider role, time, coordinates, and roof information. Final scores, market totals, forecast values, and betting results do not enter request selection. Tests change prices and outcomes and verify that requests and eligible identities remain identical.

## Scope and exclusions

| Rule | Games excluded |
| --- | ---: |
| Neutral site or unknown neutral status | 115 |
| Unknown or ambiguous stadium coordinates | 489 |
| Unknown or ambiguous venue/roof identity | 43 |
| Indoor or unknown roof status | 56 |

Coordinates come from the archived researcher-geocoded stadium CSV. Matching uses exact normalized stadium names, explicit formal-name/city distinctions, and two verified renames: [Kroger Field/Commonwealth Stadium](https://ukathletics.com/facilities/kroger-field/) and [Mountain America Stadium/Sun Devil Stadium](https://thesundevils.com/facilities-venues/mountain-america-stadium). Ambiguous identical stadium names are resolved only through explicit team identity or uniquely matching state. New replacement stadiums are excluded when the older coordinate dataset describes a different location; there is no home-campus fallback or fuzzy matching.

Current ESPN venue records marked `indoor=false` support present outdoor status. **We explicitly assume that those matched stadiums' outdoor roof configurations were unchanged during 2024–25.** This is not historical roof proof. Unknown present status and ambiguous venue identity are excluded. Current metadata must not be described as contemporaneous 2024–25 evidence.

## Frozen experiment

The one policy takes the under when forecast wind exceeds 7.78 mph, kickoff forecast temperature is below 64.81°F, and kickoff forecast relative humidity exceeds 56.8%. Wind averages the forecast kickoff hour and the next three hours, a fixed duration proxy. Half-hour kickoffs use the hour at or before kickoff. No alternative thresholds or windows are searched.

The predictor is GFS `previous_day2`; all decisions use 06:30 America/New_York on the kickoff's Eastern calendar date. A six-hour delay buffer is applied to the latest nominal reference among the four wind hours. Historical archive reuse remains explicitly labeled a fixed-lead availability proxy; the original public dissemination timestamp is unavailable.

The source writer protects previous-day archives by skipping the first `N × 24` hours of each arriving run. Consequently, a historical day2 value comes from an eligible run with a lead of at least 48 hours; a six-hour model cycle can make it older than exactly 48 hours. Future day2 values can already exist from a longer-lead forecast before the eventual 48-hour run is available. The experiment's availability gate rejects those premature interpretations. This code audit supports the lead-time distinction but does not prove original publication timestamps or that every historical archive file was never reconstructed. [Open-Meteo archive writer](https://github.com/open-meteo/open-meteo/blob/main/Sources/App/Helper/OmFileSplitter.swift), [provider lead-time documentation](https://open-meteo.com/en/docs/previous-runs-api).

## Run stages

From `model/`, after obtaining the referenced geocoded CSV:

```bash
python -m ncaaf_model.weather_research --plan --stadiums /path/to/stadiums-geocoded.csv --allow-current-roof-assumption
python -m ncaaf_model.weather_research --fetch --limit 1
python -m ncaaf_model.weather_research --fetch
python -m ncaaf_model.weather_research --evaluate
```

The existing plan is already written; do not rebuild it during the running fetch. Fetching uses batches of at most ten locations, checks returned point identities, caches successful full responses, and stops on a rate-limit response. A response is bound to its exact request parameters. The default pause scales by locations to avoid treating a ten-location batch as a single-location load. There is no automatic model substitution if a variable is unavailable. Evaluation requires all requests to exist unless explicitly labeled partial with `--allow-partial`.

Evaluation reports 2024, 2025, and pooled results at an assumed −110 price, with one unit risked per bet and integer pushes refunded. The comparison is taking the under on all games with the same valid weather coverage. Uncertainty comes from 5,000 whole Eastern-calendar-week bootstrap draws; selected-versus-all ROI contrasts use the same sampled weeks. Within-week dependence is retained. These are development-data intervals, not a familywise profitability promotion test.

The repaired provider role does not verify an exact closing line or a 06:30 entry price. Both years' outcomes have already been used elsewhere in this project's research. This is a frozen external-hypothesis adaptation on reused development data, with forecast and market-timing assumptions; any promising result requires prospective confirmation. No historical published win percentage is reused as a current forecast probability.

Validation: 35 focused weather tests pass, including venue identity and exclusions, immutable archive handling, post-decision and premature fixed-lead rejection, outcome-invariant request planning, cross-location response rejection, integer pushes, and a bootstrap fixture whose paired within-week wins/losses must remain together.
