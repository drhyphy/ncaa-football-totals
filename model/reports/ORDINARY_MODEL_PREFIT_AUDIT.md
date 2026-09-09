# Pre-fit audit of the ordinary-statistics comparison

This review was performed before fitting the two new historical model configurations. It checks the written specification and implementation, not forecast performance. No new historical prediction, outcome correlation, betting threshold, or ROI was selected during this audit.

The [fixed research plan](ORDINARY_MODEL_RESEARCH_PLAN.md) specifies 58 input predictors, two existing comparators and two new estimators, annual expanding fits, 2021–2024 MSE selection, and a separately reported reused 2025 check. The reviewed model and feature modules implement that contract. No material chronology, source-selection, or metric defect was found.

## Feature and source checks

- Ordinary statistics admit only completed reciprocal team-game rows available strictly before the shared weekly cutoff, inside the fixed trailing window. League shrinkage targets use that identical eligible history. Neither the target game's statistics nor other games in the query batch enter the calculation.
- Reciprocal opponent observations come from the other team's row in each historical game. Duplicate rows, schedule identity disagreement, nonfinal status, kickoff differences, and availability timestamps inconsistent with the kickoff-plus-six-hours proxy fail the build.
- The 32 own/opponent forms, ten context features, ten history features and six adjusted features match the explicit 58-column predictor list. Outcome and arbitrary numeric columns do not enter fitting.
- Current total/spread context and the three adjusted projection-minus-market differences are recomputed from the repaired total. Cached adjusted projections attach only to matching game identity and the exact query cutoff; an earlier query cannot reuse a later cache observation.
- The verified cache and history are fingerprinted, with repaired-source parity and schedule checks. Historical revisions and approximate publication timing remain limitations; these checks do not create original quote or statistical publication receipts.

An independent hand calculation checked 32 own/opponent weighted state values on synthetic histories with cross-year decay, shuffled nonconsecutive row indices, and missing per-metric observations. It agreed with the implementation within `2.9e-14`. This tests the numerator, denominator, finite-value league target and effective-observation shrinkage without fitting a model.

One accepted-input defect was identified before freeze: numeric-string IDs passed validation but could remain strings while team lookups used integers, silently producing league defaults or missing adjusted-cache matches. The feature author corrected this by normalizing validated identity columns in copied query, history, schedule and cache frames before joins and duplicate checks. The actual pinned historical files already use integer IDs. The final fix and regression were reviewed: integer and numeric-string representations produce identical features without mutating caller inputs.

The author reports all 21 synthetic feature tests passing and the complete source-parity build passing: 5,008 retained games, 11,926 historical team rows, all 5,008 adjusted cutoffs and prior-game counts matching, and 4,418 games meeting the minimum-history gate across training and evaluation seasons. The completed feature source fingerprint is `ab25cda52c3fb2c23a7f14f6b959ef6d9272f9f5e701b90b0e6ccd8e8869acff`. No new historical model fit, outcome correlation, or return calculation was part of that build.

## Estimation and evaluation checks

Both new estimators fit their preprocessing only on strictly earlier seasons. Median imputation preserves wholly missing training columns and derives missing indicators from training data; target-season predictors cannot change the fit. Ridge uses the stated fitted intercept, standardization and alpha 140. The histogram gradient boosting configuration uses the fixed 220 iterations, declared regularization and no automatic early stopping. Both residual adjustments have the fixed ±10-point cap.

The existing baseline forecasts are reconstructed from earlier seasons before acceptance, and the evaluation requires exact shared game IDs, outcomes and market totals. The formal plan correctly distinguishes this baseline reconstruction from fitting the two new configurations.

Selection uses game-weighted MSE from 2021–2024 only, with the fixed tie order; 2025 is excluded from that decision. The four paired contrasts preserve common games and lines. Monday–Sunday Eastern blocks, 10,000 draws, and the 95%/98.75% percentile endpoints agree with the specification. Source diagnostics are explicitly labeled and also separated by selection/check period, preventing an unlabeled pooled source summary from masquerading as a new holdout.

The parent reports eight focused model tests passing, covering future-label mutation, training-preprocessing invariance to an added test outlier, chronology and disjointness, forbidden predictors, Eastern week boundaries, shared bootstrap denominators and selection isolation. The independent feature-input review above supplements those tests.

## Remaining scope and interpretation

The complete feature build, all 21 author feature tests and the root's combined 29 tests passed. The reviewer also independently reran the numeric-string regression successfully. The reviewed identity fix resolves the only pre-freeze defect identified. Root froze the 5,008-row/58-predictor experiment before new historical fitting with plan SHA-256 `c74130cef9ec2ed994f3cb75f58fc354c9639323c06590eea43d6208bc82a150`. This pre-fit audit does not certify results that do not yet exist. Any eventual prediction and metric audit should independently reconstruct the frozen forecasts and paired scores.

All historical outcomes are reused development data. Point-prediction MSE is appropriate for these conditional-mean estimators but does not establish profitable price-specific probabilities. Prior-game publication and historical market timing remain proxies. Cross-week team dependence, source/era changes and the broader research search are not removed by four-comparison intervals. This experiment does not wire a new candidate into the live model or change the four-policy prospective registry.
