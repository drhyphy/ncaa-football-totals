# Score-shape source bookkeeping correction

On September 9, 2026, the first selection attempt stopped in the source check, before loading the experiment's data or fitting/scoring any new shape forecasts. The runner incorrectly included `reports/opponent_adjusted_predictions.csv` among files required to have a Git blob. This derived archive is intentionally local and ignored by Git. It was already present and verified by its exact SHA-256; it was not missing or replaced.

The original implementation commit `e51db91`, first machine-plan commit `c3add67`, original `score_shape_research_plan.json` (SHA-256 `3f24fe02312089db59560d41fca5ba97b5d75f77b64f0c872bd756717eaad3ad`), and original attempt receipts remain preserved. The [correction record](SCORE_SHAPE_SOURCE_CORRECTION.json) retains the failed attempt's start and terminal records and hashes. No selection, result or forecast file existed when this correction was made.

Execution-plan revision 2 removes the incorrect Git-blob requirement for that one local input while retaining its exact checksum requirement. Code, tests and public reports still must match committed Git blobs. The unchanged scientific model and specification retain the same PMF, ratios, solver, cohort, chronology, scoring and selection. A new `score_shape_research_plan_v2.json` pins the corrected runner/tests and both correction records before fitting. The original machine plan is not overwritten.

The source-identity preflight and synthetic tests had completed before the failed selection attempt. All historical outcomes remain previously reused development data; this correction was based on the observed file-tracking error and no new shape-performance result.
