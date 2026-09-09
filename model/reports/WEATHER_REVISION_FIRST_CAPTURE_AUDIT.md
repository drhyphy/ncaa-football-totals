# Independent pilot capture receipt audit

Run `local-20260909T032436108097Z` attempt `1` passed the independent receipt audit. Capture status remains **partial**.

Verified 307 current HTTP receipts, 62 explicit-run game measurements, 128 same-book quote pairs and 94 weather/quote links. The collector used 10 Odds API IO requests. All original body/receipt hashes, retained numeric quotes, game-hour measurements, applicable cohort/timing checks and reported counts matched.

All recorded source hashes matched their blobs in capture commit `2a73347d4d969d9e01c984249afbfa930287352f`. Current-tree matches are informational, allowing later versioned implementation corrections without invalidating original receipts. Frozen alias and venue bytes match their capture-recorded hashes. No source history was fetched over the network.

This is an operational integrity check. It does not establish complete source coverage, certified first publication, forecast accuracy or positive EV. No outcomes were extracted and no policy was changed.

Manifest SHA-256: `b180532c3d7d7fb73eea3aa84991ec3f934fef430d15874bb923cccfa9f0e42a`. Machine-readable details: [weather_revision_capture_audit.json](weather_revision_capture_audit.json).
