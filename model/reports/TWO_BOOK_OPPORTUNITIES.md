# Two-book opportunity audit — September 8, 2026

Fresh provider responses completed at **2026-09-09 00:06:01 UTC / September 8, 8:06:01 p.m. Eastern**. The site's exact matchup list matched 79 provider events; 77 returned totals, comprising 127 book markets. Fifty games had both DraftKings and FanDuel. The scanner evaluated 100 opposing two-book pairs and found **zero score arbitrages or nonnegative hedges with upside**. Eighty-two pairwise comparisons identified statewise better quotes; these are shopping improvements, not evidence of positive EV. No bets were submitted.

The immutable evidence is `two_book_source_20260909T000601Z.json.gz`, and full calculations are in `two_book_opportunities_latest.json`. An earlier broad scan truncated 312 college events to the first 150 and found only 22 quoted events. The final scan above uses the site's specific slate and has no truncation or provider errors. Results describe those two selected books and captured markets only.

## Actual prices and implications

| Matchup | Captured legs | Optimal opposing allocation | Payoff implication |
| --- | --- | --- | --- |
| Alabama at Kentucky | DraftKings Over 49.5 @ 1.950; FanDuel Under 49.5 @ 1.952 | 50.0256% Over; 49.9744% Under | Every integer result loses 2.4500% of total paired stake. This is the best worst-case return in the scan. |
| UNLV at North Texas | DraftKings Over 56.5 @ 1.890; FanDuel Under 57.5 @ 1.909 | 50.2501% Over; 49.7499% Under | Total 57 wins 89.9452%; every other total loses 5.0274%. Positive EV needs probability of exactly 57 greater than 5.2935%. No such probability bound is established. |
| Penn State at Temple | Under 49.5 @ 1.920 DraftKings versus @ 1.877 FanDuel | Single-side comparison | DraftKings pays 0.043 more per unit on a win and has the same loss payoff. Both quotes can still have negative EV. |

Decimal prices retain provider precision. Converting them to rounded American odds before calculations would introduce avoidable errors. The minimax problem forces one unit of total opposing stake to compare quotes; if holding cash is allowed, cash's guaranteed zero return dominates every negative hedge floor here.

## Executable strategy

`ncaaf_model.market_opportunities` provides three separate analyses:

1. **Dominance:** compare complete payoff vectors across every nonnegative integer final total. A quote dominates only when it never pays less and pays more in at least one state. A better line at a worse price is evaluated state by state rather than assumed superior.
2. **Hedge and arbitrage:** allocate one total stake unit between different books' Over and Under. In each score state, profit is `x × OverProfit + (1 − x) × UnderProfit`. The minimum over states is concave in allocation `x`; endpoints and intersections enumerate its exact maximum. All payoff changes occur adjacent to the quoted lines, so finitely many representative integer states cover the unbounded score range. Integer pushes refund stakes. Identical integer lines can have zero return on a double push even when other totals earn money; reversed middles can lose both bets despite an apparently favorable reciprocal-price sum.
3. **Probability bounds:** for decimal price `d`, unconditional win probability `w`, and push probability `p`, unit EV is `d × w + p − 1`. `probability_ev_bounds` computes sharp minimum and maximum EV over explicitly supplied probability intervals and `w + p ≤ 1`. `bounded_probability_signal` requires named external provenance and refuses impossible nonzero push probabilities at half-point lines. A peer book's implied price is never silently treated as a probability bound. With no probability information, a single bet's worst-case loss remains the entire stake.

`scan_market_opportunities` requires only the selected two books and keeps modeled probability signals distinct from score arbitrage. `refetch_io_events` reads replacement full-state odds for up to ten explicit event IDs, without changing account selections. `confirm_hedge` requires both original book/side/line identities to remain present and recalculates profit at the new exact prices. A missing, duplicated, or no-longer-recent leg fails confirmation. Provider presence still does not verify a sportsbook would accept the stake.

Arbitrage claims are conditional on both legs being accepted at the stated prices and stake fractions, identical game and overtime settlement, compatible cancellation rules, and no fees, taxes, limits, or rounding losses. An all-void refund produces zero profit and is not a strict score-profit state. Different sportsbook or jurisdiction rules must be reconciled before any actual execution; execution is outside this project.

## Timestamp evidence

Odds-API.io documents `/odds/updated` as changed-market retrieval and recommends pre-match caching for 30–60 seconds. Its complete `/odds` and `/odds/multi` responses are available separately. The adapter therefore preserves `last_update` as the provider's market timestamp and `observed_at` as the actual receipt of a successful full-state response. Re-parsing archived data never invents a new receipt time. [Official fetching guide](https://docs.odds-api.io/guides/fetching-odds).

The provider's stream documentation says updates contain the complete current event/book market set and consumers should replace state; suspended or removed markets disappear. This supports requiring quote presence in newly fetched full state rather than rejecting every unchanged quote solely because its `updatedAt` is old. It does not establish sportsbook acceptance, zero provider lag, or a timestamp heartbeat guarantee. [Official stream semantics](https://docs.odds-api.io/guides/websockets).

The scanner permits at most 120 seconds since full-state receipt to allow a bounded slate fetch, and independently rejects a market update clock more than five minutes in the future. This is an engineering screen, not evidence of 120-second execution validity. The provider's 30–60-second caching recommendation limits what an immediate confirming refetch can establish: a repeated response may still reflect the provider's current cache. Any claimed transaction would need direct book verification; none is made here.

## Prospective closing observations

`python -m ncaaf_model.closing_collector` is a separate lightweight collector. It fetches the next 24 hours of known matchups, retains immutable compressed full-state evidence, appends exact paired quotes, and compares locked entries only with the same book's observed quotes after entry and strictly before kickoff. Eligible comparisons must fall within 30 minutes of kickoff; the last such observation wins. Entry fields remain unchanged. This is a near-kickoff proxy, not an exact closing price.

Point CLV has the bettor's sign: close minus entry for Over, entry minus close for Under. A separately labeled `closing_fair_ev_at_entry` diagnostic uses proportional no-vig closing prices and a fixed normal integer-score distribution with sigma 16. It is conditional on that assumption; it is not proof the closing sportsbook price equals truth. Full future forecast calibration and realized paper ROI remain necessary to test a probability-driven candidate.

The focused suite passes 36 tests covering integer pushes, reverse-middle false arbitrages, optimal allocations against dense grids, statewise dominance, probability feasibility, stale/missing receipts, confirmation after prices change or legs disappear, immutable observations, exact book/identity matching, entry-before-close ordering, near-kickoff bounds, and preservation of locked positions. The current price scan establishes no profitable hedge. A future two-book probability candidate can generate testable paper signals, but its edge must be measured prospectively rather than inferred from the number of sportsbooks.
