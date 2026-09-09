# Frozen NOAA2021–2023 weather request plan

No forecast classification or return evaluation has occurred. This stage pins the earlier market universe, actual historical-event venue metadata,100-venue coordinate catalog and exact forecast requests.

Plan SHA-256: `47747f5a0216b5b0a43e9df5777575bfafb165ad6616332869eda35c2b508258`

Included 1,747 of 2,607 market games at 92 venues; 1,275 shared forecast objects and 3,572 field ranges.
Estimated transfer: 3.140 GB including indexes; $0 public service fee. Actual index sizes will determine the final byte budget before any field download.

| Season | Included games |
|---|---:|
| 2021 | 576 |
| 2022 | 583 |
| 2023 | 588 |

Exclusions: {"actual_venue_outside_frozen_catalog": 697, "historical_event_identity_missing_or_ambiguous": 1, "indoor_or_unknown_roof": 61, "neutral_or_unknown_neutral": 101}

The same initialization supplies all four wind hours. U/V components are interpolated before scalar speed, then the four speeds are averaged. Thresholds remain wind>7.78mph,temperature<64.81°F,RH>56.8%.

## Required before field download or evaluation

Retrieve only the locked inventories and headers; freeze exact byte ranges and sizes. Require S3 Last-Modified strictly before every affected06:30Eastern decision and initialization+6h no later than decision. Original data fields, indexes, headers and response hashes must be archived before weather classification or outcome joins.

- Forecast product, spatial interpolation and same-initialization four-hour window differ from the existing Open-Meteo experiment; thresholds unchanged.
- Historical venue IDs are actual event-specific ESPN metadata retrieved now. Original historical roof status and retrospective schedule corrections are not independently attested.
- Coordinates are the unchanged100-venue catalog; no new venues or favorable geography added.
- Earlier game outcomes have been used by other model experiments. This is a new frozen forecast-source replication, not prospective publication or untouched overall data.
- The source listing contains score fields, but only explicit identity/venue fields are extracted. Repaired market availability and earlier source validation were established before this plan.

Reproduce after ingesting the documented historical venue response files: `PYTHONPATH=model python -m ncaaf_model.noaa_weather_research --root model --plan`. Existing plans are never overwritten.
