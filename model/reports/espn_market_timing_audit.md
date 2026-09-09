# ESPN historical market timing audit

**Confirmed live-market contamination.** Real-source provenance does not establish a pregame total. Historical price-based results using the scalar archive need quarantine and replacement.

The16-game sample includes four extreme2024totals, one ordinary2025game, one2023multi-provider control, and five evenly spaced week/game-ID positions from each2024and2025archive. This deliberate sample cannot estimate prevalence.

| Season / Game | Matchup | Archived | Opening | Selected provider |
|---|---|---:|---:|---|
| 2024 / [401635557](https://sports.core.api.espn.com/v2/sports/football/leagues/college-football/events/401635557/competitions/401635557/odds?limit=100) | TCU Horned Frogs at SMU Mustangs | 107.5 | 57.5 | ESPN Bet - Live Odds |
| 2023 / [401524053](https://sports.core.api.espn.com/v2/sports/football/leagues/college-football/events/401524053/competitions/401524053/odds?limit=100) | USC Trojans at Oregon Ducks | 78.5 | 74.5 | ESPN BET |
| 2024 / [401628319](https://sports.core.api.espn.com/v2/sports/football/leagues/college-football/events/401628319/competitions/401628319/odds?limit=100) | Western Kentucky Hilltoppers at Alabama Crimson Tide | 60.5 | 60.5 | ESPN BET |
| 2024 / [401628367](https://sports.core.api.espn.com/v2/sports/football/leagues/college-football/events/401628367/competitions/401628367/odds?limit=100) | Florida Gators at Mississippi State Bulldogs | 76.5 | 56.5 | ESPN Bet - Live Odds |
| 2024 / [401628479](https://sports.core.api.espn.com/v2/sports/football/leagues/college-football/events/401628479/competitions/401628479/odds?limit=100) | Arkansas State Red Wolves at Michigan Wolverines | 39.5 | 46.5 | ESPN Bet - Live Odds |
| 2024 / [401628516](https://sports.core.api.espn.com/v2/sports/football/leagues/college-football/events/401628516/competitions/401628516/odds?limit=100) | Penn State Nittany Lions at USC Trojans | 51.5 | 48.5 | ESPN BET |
| 2024 / [401635606](https://sports.core.api.espn.com/v2/sports/football/leagues/college-football/events/401635606/competitions/401635606/odds?limit=100) | Syracuse Orange at Boston College Eagles | 51.5 | 53.5 | ESPN BET |
| 2024 / [401636868](https://sports.core.api.espn.com/v2/sports/football/leagues/college-football/events/401636868/competitions/401636868/odds?limit=100) | North Texas Mean Green at Texas Tech Red Raiders | 93.5 | 68.5 | ESPN Bet - Live Odds |
| 2024 / [401643714](https://sports.core.api.espn.com/v2/sports/football/leagues/college-football/events/401643714/competitions/401643714/odds?limit=100) | San José State Spartans at Washington State Cougars | 84.5 | 55.5 | ESPN Bet - Live Odds |
| 2024 / [401729785](https://sports.core.api.espn.com/v2/sports/football/leagues/college-football/events/401729785/competitions/401729785/odds?limit=100) | Mercer Bears at North Dakota State Bison | 52.5 | 53.5 | ESPN BET |
| 2025 / [401752665](https://sports.core.api.espn.com/v2/sports/football/leagues/college-football/events/401752665/competitions/401752665/odds?limit=100) | Alabama Crimson Tide at Florida State Seminoles | 46.5 | 50.5 | ESPN BET |
| 2025 / [401752830](https://sports.core.api.espn.com/v2/sports/football/leagues/college-football/events/401752830/competitions/401752830/odds?limit=100) | Massachusetts Minutemen at Iowa Hawkeyes | 44.5 | 43.5 | ESPN BET |
| 2025 / [401752869](https://sports.core.api.espn.com/v2/sports/football/leagues/college-football/events/401752869/competitions/401752869/odds?limit=100) | Iowa Hawkeyes at Wisconsin Badgers | 37.5 | 37.5 | ESPN BET |
| 2025 / [401756943](https://sports.core.api.espn.com/v2/sports/football/leagues/college-football/events/401756943/competitions/401756943/odds?limit=100) | Kansas Jayhawks at Arizona Wildcats | 56.5 | 56.5 | ESPN BET |
| 2025 / [401762521](https://sports.core.api.espn.com/v2/sports/football/leagues/college-football/events/401762521/competitions/401762521/odds?limit=100) | Army Black Knights at Navy Midshipmen | 37.5 | 38.5 | DraftKings |
| 2025 / [401767509](https://sports.core.api.espn.com/v2/sports/football/leagues/college-football/events/401767509/competitions/401767509/odds?limit=100) | North Dakota State Bison at The Citadel Bulldogs | 52.5 | 52.5 | ESPN Bet - Live Odds |

6 of 16 inspected games resolve to provider59 (ESPN Bet - Live Odds). All 16 matching scalar values agree with the archived market. This confirms a parser path that can use live prices after kickoff; opening/current equality in a particular row cannot establish pregame timing.

The four extreme2024examples show current/opening totals107.5/57.5,93.5/68.5,84.5/55.5, and76.5/56.5. A simple extreme-total cutoff cannot fix the archive because many selected live-provider totals remain plausible.

The [upstream parser](https://github.com/sportsdataverse/sportsdataverse-py/blob/main/sportsdataverse/cfb/cfb_pbp.py#L2570) prefers the exact ESPN BET provider name, falls back to the first provider, and reads scalar spread/total fields. It does not verify pregame market timing or reject the live-odds provider. The2023control selects provider58 ESPN BET, while provider59 separately exposes a changed live total.

Replace the archive for market-conditioned fits and profitability evaluation. Opening fields, when present, can support a separately labeled opening-line analysis; they are not a recovered6:30AM snapshot or guaranteed closing price. Retain prior experiments as quarantined development artifacts. The companion JSON preserves provider fields, retrieval time, source URLs, and response hashes.
