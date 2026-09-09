# Direct probability experiment: no replacement justified

The four fixed direct classifiers did not justify a new model or policy. **The 2021–2024 selection chose the existing raw ridge probability reference**, before this experiment computed its 2025 scores. All four new classifiers had worse 2025 log loss and Brier loss than the constant 50/50 reference. No historical EV or ROI was calculated, and no profitable edge was established.

This is reused historical development data, including 2025. The [frozen plan](DIRECT_PROBABILITY_RESEARCH_PLAN.md) specified two logistic classifiers and two histogram gradient classifiers, using either five market-context predictors or the eleven existing opponent-adjusted/context predictors. Each predicts Under at one exact half-point reference total. The experiment does not provide alternate-line probabilities or an integer-total push model.

Training used only earlier seasons, with 2020 as the initial training year. The selection period contains 2,406 games: 428 in 2021, 403 in 2022, 777 in 2023 and 798 in 2024. The separate reused 2025 check contains 852 games. Every reported configuration is evaluated on the same games within each period; no favorable model-specific subset was selected.

## All six configurations

Lower mean binary log loss (NLL) and Brier loss are better. The fixed ordering is preserved.

| Configuration | 2021–2024 NLL | 2021–2024 Brier | 2025 NLL | 2025 Brier |
|---|---:|---:|---:|---:|
| Constant 50/50 (`raw50`) | 0.693147 | 0.250000 | 0.693147 | 0.250000 |
| Existing ridge (`rawridge`; preselected) | 0.691899 | 0.249375 | 0.697509 | 0.252163 |
| Context logistic | 0.692463 | 0.249657 | 0.695176 | 0.251007 |
| Opponent logistic | 0.694645 | 0.250732 | 0.694379 | 0.250610 |
| Context histogram gradient classifier | 0.703267 | 0.254897 | 0.698217 | 0.252477 |
| Opponent histogram gradient classifier | 0.713164 | 0.259498 | 0.696273 | 0.251467 |

The [immutable selection record](direct_probability_selection.json) chose `rawridge` by game-weighted pooled NLL, with 2025 metrics still uncomputed. It remains the selected configuration for this experiment. Opponent logistic's lower 2025 NLL than ridge cannot be used to switch the selection after seeing that result.

## All four paired 2025 comparisons

Differences are the first model's NLL minus the reference's NLL; negative values favor the first model. Intervals resample all paired games together in their Monday–Sunday Eastern weeks: 10,000 whole-week draws across 22 observed weeks, retaining the game-weighted estimand.

| Comparison | NLL difference | Descriptive 95% interval | Local 98.75% interval |
|---|---:|---:|---:|
| Opponent logistic − context logistic | −0.000797 | [−0.001800, +0.000181] | [−0.002111, +0.000399] |
| Opponent tree − context tree | −0.001944 | [−0.008949, +0.004479] | [−0.010759, +0.006082] |
| Opponent logistic − existing ridge | −0.003131 | [−0.005643, −0.000655] | [−0.006305, +0.000034] |
| Opponent tree − existing ridge | −0.001237 | [−0.007485, +0.004737] | [−0.009158, +0.006512] |

Opponent logistic improves on ridge in the 2025 point estimate and its unadjusted 95% interval excludes zero. Its 98.75% interval includes zero, as do all four locally adjusted 2025 intervals. The four paired selection-period point differences were positive, favoring the references; those comparisons and their intervals across 73 weeks remain in the full JSON. The local four-comparison adjustment does not cover choosing among six configurations, inspecting both periods, the wider earlier research search, or all recurring-team dependence. These intervals do not establish a project-wide confidence claim.

## Interpretation and reproducibility

`raw50` is a statistical reference, not a verified historical no-vig bookmaker probability. The saved ridge benchmark originally trained on its larger prior-season score cohort, including integer lines; the new classifiers share a smaller half-point training cohort. Opponent-versus-context comparisons within each classifier family hold that training cohort fixed. Historical provider roles were repaired, but original morning offer prices and receipt times remain unavailable. Better probability scores alone would not establish profitable executable bets.

The [complete results JSON](direct_probability_results.json) retains annual/source metrics, reliability bins, all paired comparisons and fitted coefficient/convergence or tree-iteration metadata. The [independent audit](DIRECT_PROBABILITY_RESEARCH_AUDIT.md) reproduced all new fits, benchmark fits, scores and uncertainty comparisons without a material discrepancy. The results reference selection SHA-256 `f444d8c04790a66beb73a069858304834c34763be16aea140ee0990bd40d77be` and the [pinned machine plan](direct_probability_research_plan.json). The implementation and plan were frozen at commit `586423c`; commit `33e1c5d` subsequently locked the selection record before the 2025 check. Source code was unchanged between those commits. Existing live candidates and their separate prospective ledgers remain unchanged.
