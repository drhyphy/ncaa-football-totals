# Fixed original NOAA forecast replication

Separate 2021–2023 development replication using original operational GFS fields. Fixed classifications were archived before this evaluation. All returns assume −110; this does not establish executable positive EV.

| Period | Covered | Rule W–L–P | Rule bets | Rule ROI | Descriptive 95% interval | Descriptive 99% interval | All-Under ROI |
|---|---:|---|---:|---:|---|---|---:|
| Pooled | 1747 | 68–62–0 | 130 | -0.14% | -16.12% to +14.26% | -21.68% to +18.11% | -6.37% |
| 2021 | 576 | 21–14–0 | 35 | +14.55% | -10.91% to +34.76% | -28.41% to +43.18% | -2.35% |
| 2022 | 583 | 31–25–0 | 56 | +5.68% | -19.01% to +32.47% | -24.74% to +38.10% | -10.06% |
| 2023 | 588 | 16–23–0 | 39 | -21.68% | -58.50% to -0.40% | -73.97% to +5.00% | -6.65% |

Selected minus same-coverage all-Under ROI: +6.23%; descriptive paired-week 95% interval -9.33% to +20.09%, 99% interval -14.97% to +23.53%.

Active-week cluster-t 95% interval: -16.18% to +15.90%; 99% interval: -21.76% to +21.48%.
Leave-one-calendar-week-out ROI range: -4.55% to +3.68%.

The JSON report retains every source group, nonselected benchmark, week, exclusion and valid/omitted bootstrap draw count. No favorable source is selected.

## Coverage

```json
{
  "repaired_market_games": 2607,
  "planned_games": 1747,
  "planning_excluded_games": 860,
  "planning_exclusion_reasons": {
    "actual_venue_outside_frozen_catalog": 697,
    "neutral_or_unknown_neutral": 101,
    "indoor_or_unknown_roof": 61,
    "historical_event_identity_missing_or_ambiguous": 1
  },
  "weather_available_games": 1747,
  "weather_unavailable_games": 0,
  "weather_available_with_final_valid_market_games": 1747,
  "weather_available_but_invalid_market_or_outcome": 0,
  "selected_games": 130,
  "nonselected_games": 1617,
  "evaluation_exclusion_reasons": {},
  "reasons_may_overlap": true
}
```

## Limitations

- All stakes assume one-unit risk at −110, including pushes; historical offered prices and morning entry timing are unverified.
- Weather classifications were saved before this outcome join. Earlier outcomes were reused in other research, so this is not prospective performance or an untouched overall sample.
- Calendar-week bootstrap and active-week cluster-t intervals are descriptive; cross-week dependence and the unknown full earlier search remain outside their uncertainty estimates.
- No combined estimate with 2024–25 Open-Meteo or 2026 archived-price cohorts: forecast grid, cycle, interpolation, era and source differ.
- S3 Last-Modified is not an independently certified publication receipt; multipart objects record upload initiation, which may precede completion. Multipart storage does not change eligibility.
- Venue roof state and schedule metadata were retrospectively retrieved. No station, cycle, threshold or stadium substitutions were selected from outcomes.

From the repository root, run two separate invocations after complete downloads: `PYTHONPATH=model python -m ncaaf_model.noaa_weather_evaluate --root model --extract`, then replace `--extract` with `--evaluate`. Existing classifications and results are never overwritten.
