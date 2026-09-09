# PBP state parser: fixed real-record audit

The independent reconstruction matches all 1,709 retained states, 1,177 clock responses, and 1,708 conversion responses, with **zero field or exclusion-count mismatches**. This verifies the frozen implementation on the selected sample; it does not establish model accuracy or a betting edge.

The sample was fixed as the two numerically smallest canonical final-game IDs in each season from 2019 through 2025, before reading play values. All 14 selections are retained. Game 401281943 has no archived plays and was not replaced. Only the frozen raw-field allowlist and canonical schedule identity/context fields were read. No 2026 data, matchup model fits, prediction metrics, or betting results were examined.

The frozen experiment commit is `2277702` and machine-plan SHA-256 is `7a2124bf2de2380423a29d765961dc4b9e79384c82cb2e6ab9551853495c8c69`. All seven raw-body hashes match the pinned source inventory. The selected schedule hashes match the machine plan. The parser, formal plan and source inventory match their frozen Git blobs. Arithmetic and attribution were reconstructed separately; parser helpers, dataset loading, ratings, and the study runner were not used for the reconstruction. The parser's public `prepare_rows` output supplied only the comparison values.

| Season | Game ID | Raw rows | Retained states | Clock responses | Conversion responses |
| --- | --- | ---: | ---: | ---: | ---: |
| 2019 | 401110720 | 169 | 128 | 92 | 128 |
| 2019 | 401110721 | 182 | 129 | 88 | 129 |
| 2020 | 401207098 | 213 | 169 | 122 | 168 |
| 2020 | 401207101 | 179 | 133 | 96 | 133 |
| 2021 | 401281942 | 176 | 134 | 99 | 134 |
| 2021 | 401281943 | 0 | 0 | 0 | 0 |
| 2022 | 401403853 | 179 | 144 | 96 | 144 |
| 2022 | 401403854 | 170 | 124 | 84 | 124 |
| 2023 | 401520145 | 165 | 130 | 87 | 130 |
| 2023 | 401520146 | 167 | 120 | 78 | 120 |
| 2024 | 401628319 | 168 | 128 | 88 | 128 |
| 2024 | 401628320 | 164 | 112 | 68 | 112 |
| 2025 | 401752665 | 172 | 131 | 95 | 131 |
| 2025 | 401752666 | 186 | 127 | 84 | 127 |

The replay checked exact integer identities/order, canonical season/home/away/week/venue-neutral flag, and kickoff plus six hours as the stated availability proxy. It reconstructed pre-score from the immediately preceding consecutive archived post-score, not the current score. Twenty-three states immediately after numbered gaps were withheld. Clock responses used only literal adjacent legal records with the same drive, period and possession, retained current possession, and a 0–60 second clock decrease. Administrative and penalty exclusions were never bridged. The independent comparison also covered down, distance, field position, half-clock units, possession-relative margin, and pass/rush attribution. Two retained fumble plays had unknown pass/rush attribution; no sampled exclusion-helper flags were null.

There is **source scoreboard noise, but no systematic pre/post-score inversion in this sample**. Across 89 raw offensive-touchdown labels, 88 produce an attributed success, one produces an unknown conversion, and none are excluded or labeled failures. Of 128 archived score changes, 117 occur on scoring rows and 11 occur on non-scoring rows. The latter comprise one premature seven-point increment and five seven-point rollback/restoration pairs around administrative or special-team records. Those pairs can temporarily corrupt the lagged score-margin nuisance variable even though the touchdown itself is attributed correctly.

Concrete checks retained in the JSON:

- In 2020 game 401207098, play 149 already changes the score to 43–10; the touchdown at play 152 repeats that score. The frozen parser leaves that touchdown's conversion unknown instead of inventing its scoring attribution.
- In 2022 game 401403853, plays 43 and 108 are defensive fumble-return touchdowns. Both are conversion failures for the original offense.
- In 2025 game 401752666, play 146 has an opponent-recovery type and changed possession despite `isTurnover=false` and an original own-recovery label. The frozen explicit lost-fumble rule correctly returns failure.
- Score rollback/restoration episodes occur in 2021 game 401281942, 2022 game 401403853 (twice), and 2025 game 401752666 (twice). Every observed episode is preserved; no retroactive repair was applied.

The public machine-readable [summary](pbp_state_parser_audit.json) preserves all 14 game selections, source/Git fingerprints, counts, exclusions, mismatches, and concise score-anomaly facts. Its SHA-256 is `54b50ac4473ee7cca59aa6e5aa2e6fc8db7f3c810f4c74ed6976f317017e1482`. It does not republish full play descriptions or row traces.

The original detailed JSON bytes are preserved unchanged in the ignored local archive `model/data/normalized/pbp_state_v1/parser_audit_full.json` (1,902,338 bytes; SHA-256 `f0a058def5968b65f9692576abf67c0e23fb6de7c2f69b1ef0380ee21c1cd298`). That trace retains every independent row, exclusion decision and detailed anomaly record. The zero-row game remains explicit in both versions. Full-cohort feature coverage still needs review before matchup-model evaluation.

The independent [replay script](../../scripts/research/audit_pbp_state_parser.py) is preserved with SHA-256 `f1307e823ce54d58fb3e39b7c16e997edefa5dfce2922fea0cd7a867e3748d00`. It verifies frozen source hashes and Git blobs, selects the same 14 games, reads only allowed fields, and writes a new trace using exclusive creation. Its packaged rerun matched the original game results, all retained rows/decisions, score-semantic counts, and source checks exactly. Run `PYTHONPATH=model python scripts/research/audit_pbp_state_parser.py --output /tmp/pbp-parser-audit-replay.json` from the repository; the retained raw archives and frozen Git commit are required.

This is a small, deterministic ID sample, disproportionately representing particular schools/conferences and early-season games. It is not a representative coverage estimate. Archived clock depletion is not measured snap-to-snap tempo, current publisher snapshots are not verified historical publication receipts, and internal score checks do not certify the underlying official score. The frozen short parser docstring still says gaps are retained for efficiency; the executable behavior and formal plan correctly withhold the first state after a numbered gap. No frozen code or study policy was changed during this audit.
