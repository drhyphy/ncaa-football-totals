# Movement-history preflight: quota metadata amendment

September 9, 2026 UTC. This narrow operational amendment supplements the [original plan](MOVEMENT_HISTORY_PREFLIGHT_PLAN.md), frozen in commit `4e6eb95` with internal plan SHA-256 `4446aedf08bf2448044ab17ff9861238bc0dfb24021e31e3a3fc04aef5da5886`. The original plan and machine-readable file remain unchanged.

The first planned `/odds/multi` request succeeded with HTTP 200 at approximately **03:58:00 UTC**, but its response omitted rate-limit metadata. The runner correctly stopped after that one authenticated call. No historical event, closing-price or movement response had been requested, and no historical outcomes were inspected. Missing quota headers are an operational limitation, not a finding about historical-data availability.

Permit **one additional read-only request**:

```text
GET https://api.odds-api.io/v3/bookmakers/selected
```

Authentication must remain in the existing private request mechanism. This endpoint reads already selected books; no selection, subscription, entitlement or quota mutation is permitted. The existing implementation uses it for this purpose, and the [earlier quota preflight](weather_revision_quota_preflight.json) received HTTP 200 with a remaining-allowance header and the existing DraftKings/FanDuel selection. That earlier receipt establishes the endpoint's usefulness, not the current remaining allowance.

Resume the original remaining six calls only if this new response is successful, supplies a valid fresh `X-RateLimit-Remaining` integer **at least 27**, and confirms the two required books are already selected. Missing/invalid quota metadata, insufficient remaining allowance, authentication failure, HTTP 429 or unavailable required books ends this preflight. Do not retry, change books or purchase access to proceed. Retain the new original body, sanitized request, status/headers, actual receipt and hash.

The complete budget remains **eight authenticated calls maximum**:

1. The already completed full-state request.
2. This one selected-books/quota request.
3. The original six remaining calls, in their original order and subject to their identity, schema and quota guards.

This consumes the original eighth-call reserve. **No discovery-continuation call or other extra request remains available.** If older-event discovery is incomplete or ambiguous, retain the failure and skip its dependent requests. Continue checking quota after each response under the original plan; stop rather than infer an allowance when the required metadata is unavailable.

Upcoming and older game selections, provider matching, sportsbooks, fixed upcoming lines, the older retention-probe line rule, original receipt evidence and outcome exclusions are unchanged. The older closing-conditioned line remains a schema/retention probe only and cannot define a backtest cohort. The live policies and frozen weather-revision pilot are unaffected.

This amendment was prepared from existing local notes and the stopped-run report. Preparing it made no API calls and inspected no outcomes.
