# Prospective evaluation protocol — September 9, 2026

This protocol is recorded before the next eligible game's kickoff. It does not establish an edge or change which paper signals the daily pipeline can publish. Historical development results and the reconstructed 2026 price replays remain outside this evaluation.

## Fixed cohort and audit

Include every first qualifying paper position from the current four candidate policies for games kicking off from September 9, 2026 at 00:00 America/New_York through January 31, 2027 at 23:59:59 America/New_York. Positions already recorded for future games are included; their outcomes were unknown when this protocol was written. Evaluate once on February 8, 2027 at 12:00 UTC, using final outcomes available then. Publish running descriptive results throughout, without treating an interim positive interval as a confirmatory success. The date specifies an evaluation protocol; it is not a separately installed reminder.

The four policies are the current market-price reference, opponent-adjusted ridge, opponent-adjusted structural, and published weather Under. Keep the scoring candidates at `totals-v4-20260908` and the weather candidate at `weather-under-v1-20260908`. The scoring coefficients, residual distribution, thresholds, bookmaker rules and weather criterion are fixed. Updating opponent ratings from newly completed prior games is part of the existing rule. Changes that alter selection, probabilities or payouts create a new exploratory version; do not combine its results with this cohort or substitute it for an unsuccessful candidate.

The Git commit publishing this protocol fixes the implementation. Model artifact SHA256: `bf8af58e3d927acf5ad780ac4725f87dede802359ac747622a49de662615d99c`; distribution SHA256: `7e028b30a38308d66456e2559cc4262deab954a982b8fc2d19ef3a64366bd529`; shared repaired-data fingerprint: `cf764f803b38ecfd`. The committed weather request plan and source audit describe the fixed forecast transformation.

[The existing-position manifest](prospective_existing_positions.json) pins the four existing, still-pending scoring positions and their shared decision archive, whose eligible forecasts match each recorded line, price, side, bookmaker, projection and EV. Its model and distribution hashes match the frozen artifacts above. Preserve these IDs and evidence; exact policy provenance remains an audit requirement, and any exclusion must be reported with its reason. There are no existing weather positions.

## Records and estimand

Risk one hypothetical unit per locked position at its actual recorded decimal price. A win returns decimal odds minus one unit, a loss returns minus one, and a push returns zero. The primary estimand is aggregate profit divided by total units risked. Evaluate candidates separately, including the benchmark; do not pool overlapping games across candidates. Keep missing, void, postponed and ungraded records visible with reasons, rather than deleting losses or inventing settlements.

The reported settled ROI denominator includes all settled positions, including pushes. Report pending and void positions separately. Before any positive promotion review, also show the return and interval with every unresolved in-cohort position charged a full-unit loss; incomplete settlement cannot make the evidence stronger by removing unresolved risk.

Require a pre-kickoff immutable decision record, canonical game identity, paired offer, source receipt and the candidate's original eligibility checks. Audit first-entry locking and exclude reconstruction records. Preserve the first entry when later prices improve or deteriorate. Report observed prices separately from accepted wagers: this experiment supplies paper returns, not proof of sportsbook acceptance, limits or realized cash profit.

## Uncertainty and decision rule

Report week-cluster uncertainty because bets within a football week can share weather and other shocks. At the fixed audit, calculate the aggregate-ROI cluster-ratio-score t interval using **active betting weeks**, with degrees of freedom equal to active weeks minus one. Use two-sided 98.75% intervals for each of the four candidates, a Bonferroni allocation targeting 95% simultaneous coverage under the cluster model. At least 12 active weeks and 50 settled positions are required to consider this approximate interval for a promotion review; fewer observations remain inconclusive. These minimums do not prevent publishing or recording qualifying signals.

Weeks run Monday through Sunday according to kickoff's America/New_York date. An active week contains at least one qualifying locked position. For settled risk `N_g` and profit `P_g` in week `g`, use `theta = sum(P_g)/sum(N_g)`, `u_g = P_g - theta*N_g`, and `SE = sqrt(G/(G-1)*sum(u_g^2))/sum(N_g)`. Apply the t critical value with `G-1` degrees of freedom. If any active week has no settled positions, flag incomplete settlement and withhold a positive promotion review pending the unresolved-loss sensitivity.

A positive lower bound is necessary for a stronger evidence claim. Also disclose the point estimate and interval after removing each individual week, performance by season segment and sportsbook, missing-data rates, and available closing-price comparisons. A result whose profit disappears upon removal of one week remains fragile. Calibration and forecast-error metrics are secondary diagnostics; they cannot substitute for returns at recorded prices. There is no automatic promotion to a high-confidence betting recommendation.

The t interval assumes sufficiently independent, representative weekly blocks and adequate finite-sample approximation. Bonferroni addresses this fixed prospective family; it cannot repair contamination in the previously searched historical data. Changes in teams, markets or execution can defeat a historical edge even after a positive future test. If this cohort is inconclusive, publish that outcome and register a new future cohort before inspecting its outcomes; do not extend the endpoint until significance appears.

## What would count as progress toward the original goal

A credible positive result at this fixed audit would provide prospective evidence for the observed-price paper policy. Confidence in executable profitability additionally requires documented price acceptance and realistic limits/costs. No actual wagering is authorized or performed by this project. The operational website, a positive development backtest, and a model-generated EV percentage alone do not achieve the original profitability goal.
