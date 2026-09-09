# Weather hypothesis: fixed line-source sensitivity

The weather selections and forecast inputs are unchanged. Only the archived total is replaced with an independently published CFBD-derived total. Both sources assume −110; offered prices and quote timestamps are unavailable. These are reused development results, not evidence of an established live edge.

The primary weather cohort contains 1,160 games. The shared comparison contains 993; 167 primary games, including 5 weather selections, lack a secondary line.

| Period | Source/cohort | Covered games | Weather bets | W–L–P | Assumed ROI | Descriptive 95% week ROI interval | All-under ROI, same cohort |
|---|---|---:|---:|---|---:|---|---:|
| 2024–25 | primary_all_weather_covered | 1160 | 85 | 55–30–0 | +23.53% | +1.42% to +43.92% | -4.55% |
| 2024–25 | primary_shared | 993 | 80 | 53–27–0 | +26.48% | +6.06% to +46.23% | -3.87% |
| 2024–25 | secondary_shared | 993 | 80 | 52–27–1 | +25.34% | +4.64% to +44.71% | -2.21% |
| 2024 | primary_all_weather_covered | 566 | 40 | 24–16–0 | +14.55% | -20.45% to +49.15% | -4.55% |
| 2024 | primary_shared | 484 | 38 | 24–14–0 | +20.57% | -13.22% to +51.41% | -4.94% |
| 2024 | secondary_shared | 484 | 38 | 23–14–1 | +18.18% | -17.00% to +47.42% | -3.89% |
| 2025 | primary_all_weather_covered | 594 | 45 | 31–14–0 | +31.52% | +2.27% to +58.18% | -4.55% |
| 2025 | primary_shared | 509 | 42 | 29–13–0 | +31.82% | +5.33% to +59.09% | -2.86% |
| 2025 | secondary_shared | 509 | 42 | 29–13–0 | +31.82% | +5.33% to +59.09% | -0.61% |

## Paired source changes

| Period | Changed selected settlements | Secondary minus primary ROI | Descriptive 95% paired week interval |
|---|---:|---:|---|
| pooled | 1 | -1.14% | -4.78% to +0.00% |
| 2024 | 1 | -2.39% | -11.36% to +0.00% |
| 2025 | 0 | +0.00% | +0.00% to +0.00% |

## Limits

- Exact weather flags, forecast windows, thresholds and eligible weather cohort are inherited unchanged from the fixed hypothesis.
- Secondary CFBD derivative retains its total but discards bookmaker identity, quote timestamp and total-side prices. All returns assume -110.
- Shared cohorts compare identical games; the all-primary weather cohort is reported separately so missing secondary coverage is visible.
- Fixed-lead weather archive is a pregame availability proxy, not proof of original public dissemination time. Historical roof stability is assumed.
- 2024–25 outcomes are reused development data. Week-bootstrap intervals do not correct for overall research selection or establish high-confidence profitability.
- No probabilities, live expected values, new thresholds or wagering recommendations were fitted by this check.

Reproduce with `python scripts/weather_source_sensitivity.py --root .`; optional `--secondary-file` accepts the same normalized market-only schema. No download or model training occurs.
Frozen weather plan: `d08836ca73abab7ab47cc63a18005acd1608e772d3ac5f721f15f7640794318f`.
