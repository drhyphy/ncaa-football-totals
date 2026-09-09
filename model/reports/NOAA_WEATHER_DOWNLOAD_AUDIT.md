# Independent NOAA download audit

Passed: 3,572 GRIB messages from 1,275 forecast objects; 3,151,825,716 field bytes. No exclusions or missing ranges. All 7,144 field/receipt files match the completed fetch manifest.

Fetch manifest: `3a8639154a7d19719fa3c6798ae3b9cd60bc05e46b7a960fdcb6c8f35060652a`

| Field | Messages | Bytes |
|---|---:|---:|
| relative_humidity | 511 | 401,647,828 |
| temperature | 511 | 330,605,010 |
| u_wind | 1,275 | 1,223,006,069 |
| v_wind | 1,275 | 1,196,566,809 |

Every recorded HTTP response is206 with the exact frozen range, object size, ETag and source Last-Modified. Every binary matches its own receipt and the final manifest SHA-256, declared size, GRIB2 framing/encoded length and terminator. Source/request relationships, valid times, field/level/units and scan-order metadata agree with the frozen plan. No alternate object or later cycle was substituted.

Source Last-Modified is 3.704–4.050 hours after initialization and 30.571–43.796 hours before the earliest applicable decision. There are 0 multipart-pattern field receipts. Actual new download receipts span 2026-09-09T02:19:46.366382+00:00 through 2026-09-09T02:22:54.037002+00:00.

This audit read no weather values, classifications, final scores or betting results. Historical source timestamps remain availability proxies; they do not establish certified dissemination or an executable historical wager. Meteorological extraction and outcome checks remain separate.

Reproduce from the repository root: `python scripts/audit_noaa_download.py --root model`.
