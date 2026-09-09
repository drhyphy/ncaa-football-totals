# Independent hosted capture audit

GitHub run `34307520971`, attempt `1`, passed independent reconstruction of its retained data. Its collection status remains **partial**. This report is separate from the first local capture audit and does not change that report, the protocol or archived inputs.

The auditor called `Audit.execute` in [audit_weather_revision_capture.py](../../scripts/audit_weather_revision_capture.py) directly against the immutable hosted manifest, without importing the collector or forecast/quote parsers. All 291 receipt hashes and available original body hashes matched. Frozen cohort selection, recorded event/venue context, all 46 retained explicit-run game measurements, 128 same-book Over/Under price pairs, and 71 weather-to-price associations were reconstructed. All six source hashes matched their Git blobs at capture commit `a8fa0151e6d0d3209d7eee59d735bb9bae541f11`.

| Operational measure | Independently verified result |
|---|---:|
| Frozen official cohort | 86 games |
| Validated Single Runs weather | 46 games |
| Mature previous-day comparators | 0 games |
| Both books quoted, regardless of weather | 51 games |
| Weather and at least one later same-book price pair | 44 games |
| Weather and later pairs from both books | 27 games |
| HTTP requests | 291 |
| Successful HTTP 200 responses | 275 |
| Transport failures | 16 |
| Odds API IO requests | 10 |
| Capture duration | 139.25 seconds |

All 16 transport failures were `ReadTimeout` during `weather_single_run` requests. Each failure retains its original requested/received times and explicit error class; no response status or body is invented. No automatic retries or different forecast products were substituted. The derived missingness records contain 16 failed Single Runs validations, 24 games outside the frozen venue catalog, one indoor/unknown-roof flag, and nine games without a valid totals quote. These reason counts can overlap and are not additional independent games.

For the 71 retained weather/price links, receipt gaps ranged from **6.08 to 130.30 seconds**, with a **128.37-second median**. Each linked weather response preceded the official context recheck, which preceded the fresh quote request and response; every quote remained pregame. These are actual response-receipt gaps. They do not certify when a forecast or sportsbook price first became public. The long gaps are retained rather than concealed by retimestamping or retrospective pairing.

The 10 Odds API IO requests were within the fixed 17-request cap. Recorded quota headroom and stopping checks passed. The gzip compatibility correction preserved original response bytes and their hashes; historical source verification used the capture's recorded Git commit, rather than requiring the current checkout to be identical.

This is an operational integrity and missingness audit. It does not establish complete provider coverage, forecast accuracy, profitable EV or successful future scheduling. No scores or outcomes were extracted, no model was evaluated, and no betting policy changed.

Manifest SHA-256: `f69d4a2e473d0be1a88411830531d6077eebbac7e298bb328e3a07b16fcae2cb`.

Machine-readable evidence: [weather_revision_hosted_capture_audit.json](weather_revision_hosted_capture_audit.json).
