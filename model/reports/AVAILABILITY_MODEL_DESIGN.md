# ACC quarterback availability: probability model specification

**Prepared September 9, 2026, before any real-data fit or target labels for this study. The controller is not activated. No profitable edge is established.** Committing this document fixes the numerical and semantic candidate; a separately committed execution and season-collection protocol must precede prospective forecasts. The existing seven-day collection pilot and its original receipts remain unchanged.

The hypothesis is narrow: current quarterback absences, and changes in those absences, may improve a probability forecast beyond paired market prices and shared timing controls when this collector receives the report. The source may already be priced correctly. There is one challenger and one fitted reference; only the challenger can have a paper ledger.

## Source contract

The [public renderer audit](ACC_CURRENT_RENDERER_CONTRACT.md) establishes literal `Initial` and `Game Day` phases, `games[].rows[].name/status`, and a nonempty all-Available team declaration. Those exact phases are **source-posted** metadata. There is no required backend completed bit or jersey field. Pending, unrecognized phase, empty arrays, malformed rows and unknown statuses remain unavailable. A future populated body is checked against this fixed parser; a discretionary human schema approval is not an eligibility requirement.

Reconcile the unordered team pair and footer date/time to the current official event, interpreting literal ET as America/New_York. Do not infer home/away from block order. Primary eligibility additionally requires ESPN `competition.conferenceCompetition=true`, `groups.id="1"`, `groups.name="Atlantic Coast Conference"`, `groups.isConference=true`, and both competitors' `team.conferenceId="1"`. Exact canonical event/team IDs are required. Optional nonconference reports remain in raw collection. Order future eligible targets by kickoff then numeric game ID and cap at 20 before role details, statuses or quotes.

Known statuses are exactly Out, Out - (1st Half), Doubtful, Questionable, Probable, Game Time Decision, and Available. Only literal full-game Out contributes to this feature. Category percentages do not become participation probabilities. Source receipt, issue labels, HTTP cache dates and policy deadlines remain separate. Repeated cached content is not a certified new publication.

The [roster](ACC_QB_ROLE_PREFLIGHT.md) and [public box-score](ACC_QB_ROLE_WEB_PREFLIGHT.md) preflights support an observed-link role path: inventory team roster link; target summary's prior-game Gamecast link; Gamecast's explicit box-score link. No guessed URL, credential-bearing legacy route or retry of the denied summary API belongs to this path. Retain original decoded HTML and actual receipts, then parse its single static `window['__espnfitt__']` JSON assignment without executing JavaScript.

For each team select the latest dated entry in the observed `lastFiveGames` list before the decision, within **[2026-08-01T00:00:00Z, 2027-02-01T00:00:00Z)**. If season is not explicit, record `fixed_date_window_inference`, not source certification or an exhaustive schedule search. Select using IDs, participants and date, without scores/gameResult. Do not substitute an earlier game if the selected page fails. Verify selected Gamecast/box-score identities, date and explicit Final/post state before participation use. Preserve the roster's explicit 2026 season evidence separately.

Require a unique complete passing table with aligned `completions/passingAttempts` and `C/ATT` labels. All passing athletes, including non-QBs, must sum to published team completions and attempts. This measures prior participation, not expected snaps or starter quality. A roster QB's weight is his positive attempts divided by team attempts. An absent/zero-attempt QB has zero weight only when the complete valid table establishes that fact. A verified zero-attempt team table has no positive-attempt QB weights; never evaluate 0/0. Missing or unreconciled tables are not zero participation.

Join full-game Out names to the same team's verified roster by **NFKD → ASCII → lowercase → retain alphanumerics**, retaining every name token/suffix and the raw name. No fuzzy, prefix, initials, suffix-dropping or jersey-only substitution. A collision, unresolved Out identity or unknown Out-player position makes the burden unknown. Athlete ID supplies the subsequent passing join; non-QB passers still count in the team denominator.

Two zero cases do not need irrelevant role evidence: a valid nonempty known-status table without literal full-game Out rows has defined burden zero; a verified non-QB Out player contributes zero without a passing profile. All-Available additionally records the renderer's explicit none declaration, including possible placeholder names. Other no-Out tables have zero **full-game-Out burden** without claiming nobody is injured. An Out QB needs the complete passing profile even to establish his zero weight. Missing evidence is otherwise unknown.

## Fixed opportunities and features

At most two primary opportunities exist per game: first archived **scheduled** receipt with reconciled exact Initial metadata, and first with reconciled exact Game Day metadata. Designate before checking player rows/statuses, role coverage, prices or artifacts. An unavailable designation has no later replacement. Duplicate same-phase records at that first receipt make that opportunity ambiguous permanently. If both phases first appear in one invocation, process Initial before Game Day. Manual captures can supply actually earlier source history; they cannot acquire retrospective primary forecasts or paper entries. The execution protocol must define reconstructable training input scope before fitting.

The kickoff at first designation becomes permanent `group_kickoff` for chronology and uncertainty. Official rescheduling changes current kickoff without moving that anchor or discarding the game. Every decision precedes current verified kickoff. Final labels preserve actual final kickoff too; training decisions must precede that actual kickoff.

Choose a unique paired positive half-point main full-game total by **DraftKings, then FanDuel**. One book suffices. Define raw Under reference `q=(1/d_under)/(1/d_under+1/d_over)`. This is a benchmark, not known truth. Target is final total Under the exact half-point line, including overtime. Integer-line observations remain archived but cannot use this binary model.

Both models share the same covered observations and these ordered controls:

1. Current total.
2. `log1p(hours_to_kickoff)` from actual quote receipt to current verified kickoff.
3. Current minus immediately preceding same-book main total.
4. Game Day indicator.
5. Missing previous report/burden indicator.
6. Missing previous quote indicator.

Select the immediately preceding archived scheduled game observation before validating its quote. Require its same-book receipt 15–45 minutes before the new quote. Missing, ambiguous, failed or out-of-window prior evidence gives change=0 plus the missing-quote indicator. No older successful quote substitutes. A failed source or inventory invocation retains a missing snapshot for previously observed games, including after their old kickoff has passed; a later rescheduling cannot erase that failure from the preceding-quote chronology. These metadata barriers do not expand the current official target or HTTP request cap. Its line may be integer because it supplies a movement control, not settlement probability.

The challenger adds exactly current full-game-Out QB burden `B` across both teams, and `B-B_previous`. B lies in [0,2]. Recompute the immediately preceding source-posted report using the **same current pre-decision roster and passing weights**. Do not search further back if that burden is unknown. Missing previous report/burden gives change=0 and its indicator; it does not assert prior B=0. Unknown current B makes both models unavailable so comparison remains paired. Partial-game Out remains separate and contributes no full-game weight.

Role receipts and both artifact-availability receipts must precede the new quote. Prepare role evidence before report/quote collection; role failure must not stop raw collection. Later HTML cannot backfill a forecast. Preserve report → current official context → quote order.

## Estimation and chronology

Reference: `logit(p)=logit(q)+alpha+beta'C`. Challenger adds `gamma'A`. The q offset coefficient is one and fitted coefficient signs are unrestricted. Standardize with training-only game-weighted means and population SDs; exactly constant columns use scale one. Clip standardized values at ±5. Each game's one or two unique phases together have weight one.

Minimize game-weighted **mean** binary log loss plus `0.1/2 * ||theta||²`, including intercept. No penalty grid. Initialize at zero; L-BFGS-B maxiter=1000, gtol=1e-9, ftol=1e-12, maxls=50. Require success, finite parameters/objective and maximum absolute gradient below 1e-5. Failed fit has no fallback probability.

Minimum: **20 distinct covered games across two completed Eastern kickoff weeks**, not 20 snapshots. This permits experimental estimation, not reliable calibration. There is **no both-class gate**: positive L2 on every coefficient makes the objective strongly convex and coercive even with all-Under or all-Over labels. A class gate would unnecessarily delay fitting based on outcomes. Record class counts and feature variation; rare/constant burden does not establish information.

Refit at Monday 00:00 America/New_York cutoffs. Use only completed permanent-anchor weeks and actual final-label receipts before cutoff. Decisions must precede actual final kickoff; labels must arrive after it. No phase can train another phase of its own game. Require the cutoff for the current inference week; an unavailable current-week artifact cannot silently use an older fit. Freeze one pair per cutoff, retaining training observation IDs/labels/source hashes, transforms, coefficients, convergence diagnostics and actual durable availability. A post-quote artifact cannot predict that quote.

## Economic decision

Both forecasts must be generated during the same scheduled invocation as their designated observation. After inference, verify original immutable context/offer bytes again. Record decision and any paper lock within 120 seconds of original quote receipt and before kickoff. This verifies an observed offer's integrity and age; it does not claim a second live quote or accepted bet. Publication latency remains visible.

Only the challenger may create **one flat-unit paper entry per game**, at the earliest designated opportunity with a qualifying offer. At the modeled line, Under estimated EV=`d*p-1`; Over=`d*(1-p)-1`. Choose maximum strictly positive estimated EV. Fixed ties: Under before Over, then DraftKings before FanDuel. A peer at another line cannot borrow this probability. Invalid/stale offers fail. There is no extra two-book, sample-count, stress-EV or positive-confidence-bound gate. Positive estimated EV remains a hypothesis.

An initial entry cannot be replaced, improved or doubled at Game Day. Later forecast still belongs in proper scoring. Preserve immutable failures, missingness, original stakes and prices. Void positions keep unit risk and zero profit; pending positions remain unresolved. The label controller and execution protocol must specify cancellation/correction evidence before activation.

Planned endpoint: January 31, 2027; formal report February 8, 2027 at 12:00 UTC. A committed season/execution protocol must fix the exact interval first. No interim promotion, threshold tuning or pooling with existing policies. A small or uninformative cohort may remain inconclusive.

## Evaluation

Score every locked forecast, including no-bet cases, using stable log loss and Brier for challenger, fitted reference and raw q. Average labeled phases within game, then games. Display missing/partially labeled games; do not fill a label implicitly from another record.

Local endpoints: challenger minus reference log loss and challenger paper ROI. Bootstrap complete permanent-anchor Eastern weeks: 10,000 draws, NumPy PCG64 seed20260909, ratios of aggregated numerators/denominators, percentile intervals with linear quantiles. Use 97.5% intervals per endpoint as a **local two-endpoint sensitivity**, plus descriptive 95%/99% ROI intervals. Fewer than two weeks yields no interval. This approximate bootstrap is not an exact finite-sample guarantee, especially with few/dependent weeks, and the local adjustment does not cover the project's prior model searches.

Each locked position risks one unit. Full-cohort ROI/interval remain unavailable while any position is pending. Separately show resolved subset including voids and the sensitivity charging all pending entries a full loss. Show leave-one-week-out ROI, book/side/phase counts, raw q scores and fixed-decile reliability. CLV, movement or improved exact-score density alone cannot establish probability accuracy or profit. Evaluation never automatically promotes the policy.

## Current implementation boundary

Pure source parsers, HTML receipt transport, penalized mathematics and grouped evaluation are implemented and being independently tested. They are not yet a live study. Remaining: receipt-verifying role controller/dataset adapter, append-only opportunities/forecasts/labels, weekly artifact orchestration, explicit season execution protocol, failure-preserving workflow integration and public status. Two-team source feasibility is not universal coverage evidence.
