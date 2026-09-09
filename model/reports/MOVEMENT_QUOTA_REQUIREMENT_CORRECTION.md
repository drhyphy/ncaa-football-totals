# Header-presence requirement correction

Recorded September 9, 2026, after the first two HTTP responses and before any history request. This supersedes only the missing-header stop in the [operational amendment](MOVEMENT_HISTORY_PREFLIGHT_AMENDMENT.md); the original game and line selection remains unchanged.

The original full-state request returned HTTP 200 at 03:58:00.671954Z. The one account-status request returned HTTP 200 at 04:00:22.598403Z. Neither contained rate-limit headers, although the provider documentation says responses carry them. The original receipt bodies and headers are retained. This is an observed contract gap, not an authentication failure or an observed rate-limit rejection.

Requiring fresh quota headers was our discretionary bookkeeping rule. It is not needed to identify prices or estimate a model, and its failure should not prohibit a small otherwise authorized read. Complete the six remaining originally specified requests, for **eight requests total including these two**, without extra retries, discovery continuations, events, lines, account changes or purchases. The connected account previously reported a 100-request hourly limit; absent current headers, remaining allowance and a 20-request reserve cannot be guaranteed. Do not claim otherwise. Stop on HTTP 401, 403 or 429, and stop at the reserve if a later response actually reports remaining allowance of 20 or less.

This correction changes neither an evaluation cohort nor a predictor, outcome, fitted parameter or selection rule. No history was retrieved or outcomes inspected before recording it. Responses obtained later remain provider-reported history retrieved now, never original historical execution receipts.
