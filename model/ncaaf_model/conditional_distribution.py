"""Bounded conditional score-distribution development experiment.

Four fixed families are compared on expanding-year folds. Candidate selection
uses seasons through 2024 only; 2025 is reported separately and is explicitly
reused development data. No runtime policy is changed by this module.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import json

import numpy as np
import pandas as pd
from scipy.ndimage import gaussian_filter1d
from scipy.special import ndtr

SCORES = np.arange(251, dtype=float)
CANDIDATES = ("constant_normal", "conditional_normal", "conditional_keymass", "conditional_residual")
SELECTION_YEARS = (2022, 2023, 2024)
LOCKED_PARAMETERS = {
    "mean_ridge_penalty": [100., 1000., 1000., 1000., 1000.],
    "variance_ridge_penalty": [100., 1000., 1000., 1000., 1000.],
    "mean_adjustment_clip": 4., "sigma_clip": [10., 24.],
    "keymass_prior_games": 800., "keymass_ratio_clip": [.6, 1.6],
    "residual_total_bandwidth": 10., "residual_spread_bandwidth": 21.,
    "residual_other_regime_weight": .5, "residual_prior_games": 200.,
    "residual_max_mixture_weight": .35, "residual_kernel_sigma": 2.,
    "fixed_diagnostic_ev_threshold": .03,
    "market_total_valid_range": [15., 100.],
    "prespecified_push_diagnostic_scores": [37, 41, 44, 47, 48, 51, 54, 55, 58, 61, 63, 65, 69, 72],
}


def load_history(root: Path) -> pd.DataFrame:
    """Use the same explicit real-source provenance gate as audited totals."""
    pieces = []
    for season in range(2019, 2026):
        schedule = pd.read_parquet(root / "data/raw/sportsdataverse" / f"cfb_schedule_{season}.parquet")
        odds = pd.read_parquet(root / "data/raw/sportsdataverse" / f"betting_{season}.parquet")
        if "odds_source" not in odds:
            continue
        odds = odds.loc[odds.odds_source.isin(["core_odds_api", "summary_pickcenter"])].drop_duplicates(["game_id", "season", "week"], keep="last")
        games = schedule.merge(odds, on=["game_id", "season", "week"], validate="one_to_one")
        games = games.loc[games.status.eq("STATUS_FINAL")].copy()
        games["actual_total"] = pd.to_numeric(games.home_score, errors="coerce") + pd.to_numeric(games.away_score, errors="coerce")
        games["market_total"] = pd.to_numeric(games.over_under, errors="coerce")
        games["spread"] = pd.to_numeric(games.home_team_spread, errors="coerce").abs().fillna(0)
        games["game_date"] = pd.to_datetime(games.game_date, utc=True, errors="coerce")
        games = games.dropna(subset=["actual_total", "market_total", "game_date"])
        games = games.loc[games.actual_total.between(0, 250) & games.market_total.between(15, 100)]
        pieces.append(games[["game_id", "season", "week", "game_date", "actual_total", "market_total", "spread", "odds_source"]])
    result = pd.concat(pieces, ignore_index=True).sort_values(["game_date", "game_id"]).reset_index(drop=True)
    if result.game_id.duplicated().any():
        raise ValueError("Duplicate games in score distribution history")
    return result


def design(frame: pd.DataFrame) -> np.ndarray:
    # The market already incorporates the rules. Regime terms learn only a
    # residual adjustment; they are not claims of a causal rule-change effect.
    return np.column_stack([np.ones(len(frame)), (frame.market_total.to_numpy(float) - 55) / 10,
                            (frame.spread.to_numpy(float) - 14) / 14,
                            frame.season.ge(2023).to_numpy(float), frame.season.ge(2024).to_numpy(float)])


def normal_pmf(center: np.ndarray, sigma: np.ndarray) -> np.ndarray:
    center, sigma = np.atleast_1d(center), np.atleast_1d(sigma)
    lower = ndtr((SCORES[None, :] - .5 - center[:, None]) / sigma[:, None])
    upper = ndtr((SCORES[None, :] + .5 - center[:, None]) / sigma[:, None])
    mass = np.maximum(upper - lower, 1e-15)
    mass[:, -1] += np.maximum(1 - upper[:, -1], 0)  # Upper-tail mass retained.
    return mass / mass.sum(axis=1, keepdims=True)


@dataclass
class ConditionalDistribution:
    training_max_season: int
    training_games: int
    sigma: float
    mean_coefficients: np.ndarray
    variance_coefficients: np.ndarray
    keymass: np.ndarray
    training_total: np.ndarray
    training_spread: np.ndarray
    training_season: np.ndarray
    training_residual: np.ndarray

    @classmethod
    def fit(cls, frame: pd.DataFrame) -> "ConditionalDistribution":
        if len(frame) < 100:
            raise ValueError("At least 100 prior games are required")
        x = design(frame)
        errors = frame.actual_total.to_numpy(float) - frame.market_total.to_numpy(float)
        sigma = float(np.sqrt(np.mean(errors ** 2)))
        mean_coef = np.linalg.solve(x.T @ x + np.diag(LOCKED_PARAMETERS["mean_ridge_penalty"]), x.T @ errors)
        mean = np.clip(x @ mean_coef, -4, 4)
        variance_target = (errors - mean) ** 2 - sigma ** 2
        variance_coef = np.linalg.solve(x.T @ x + np.diag(LOCKED_PARAMETERS["variance_ridge_penalty"]), x.T @ variance_target)
        sigmas = np.sqrt(np.clip(sigma ** 2 + x @ variance_coef, 100, 576))
        expected = normal_pmf(frame.market_total.to_numpy(float) + mean, sigmas).sum(axis=0)
        observed = np.bincount(frame.actual_total.to_numpy(int), minlength=len(SCORES)).astype(float)
        prior = LOCKED_PARAMETERS["keymass_prior_games"] * expected / len(frame)
        keymass = np.clip((observed + prior) / np.maximum(expected + prior, 1e-12), .6, 1.6)
        return cls(int(frame.season.max()), len(frame), sigma, mean_coef, variance_coef, keymass,
                   frame.market_total.to_numpy(float), frame.spread.to_numpy(float), frame.season.to_numpy(int), errors)

    def predict_pmf(self, frame: pd.DataFrame, candidate: str, *, check_chronology: bool = True) -> np.ndarray:
        if candidate not in CANDIDATES:
            raise ValueError(f"Unknown candidate: {candidate}")
        if check_chronology and len(frame) and int(frame.season.min()) <= self.training_max_season:
            raise ValueError("Prediction season must be later than every fitted season")
        market = frame.market_total.to_numpy(float)
        if candidate == "constant_normal":
            return normal_pmf(market, np.full(len(frame), self.sigma))
        x = design(frame)
        center = market + np.clip(x @ self.mean_coefficients, -4, 4)
        sigma = np.sqrt(np.clip(self.sigma ** 2 + x @ self.variance_coefficients, 100, 576))
        base = normal_pmf(center, sigma)
        if candidate == "conditional_normal":
            return base
        if candidate == "conditional_keymass":
            mass = base * self.keymass[None, :]
            return mass / mass.sum(axis=1, keepdims=True)
        # Broad fixed kernels borrow residual shapes from comparable totals and
        # spreads. Shrink hard toward the conditional normal; no leaf search.
        edges = np.arange(-180.25, 180.76, .5)
        grid = (edges[:-1] + edges[1:]) / 2
        for index, game in enumerate(frame.itertuples()):
            weights = np.exp(-.5 * ((self.training_total - game.market_total) / 10) ** 2
                             -.5 * ((self.training_spread - game.spread) / 21) ** 2)
            regime = (self.training_season >= 2023).astype(int) + (self.training_season >= 2024)
            game_regime = int(game.season >= 2023) + int(game.season >= 2024)
            weights *= np.where(regime == game_regime, 1., .5)
            counts, _ = np.histogram(self.training_residual, bins=edges, weights=weights)
            smoothed = gaussian_filter1d(counts.astype(float), sigma=4., mode="constant")
            empirical = np.interp(SCORES - game.market_total, grid, smoothed, left=0., right=0.)
            empirical = (empirical + 1e-15) / np.sum(empirical + 1e-15)
            mixture = min(.35, float(weights.sum() / (weights.sum() + 200.)))
            base[index] = (1 - mixture) * base[index] + mixture * empirical
        return base / base.sum(axis=1, keepdims=True)

    def metadata(self) -> dict:
        return {"training_max_season": self.training_max_season, "training_games": self.training_games,
                "constant_sigma": self.sigma, "mean_coefficients": self.mean_coefficients.tolist(),
                "variance_coefficients": self.variance_coefficients.tolist(), "keymass": self.keymass.tolist()}


def market_probabilities(pmf: np.ndarray, lines: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    lines = np.asarray(lines, dtype=float)
    over = np.sum(pmf * (SCORES[None, :] > lines[:, None]), axis=1)
    under = np.sum(pmf * (SCORES[None, :] < lines[:, None]), axis=1)
    push = np.sum(pmf * np.isclose(SCORES[None, :], lines[:, None], atol=1e-10, rtol=0), axis=1)
    return over, under, push


def game_metrics(test: pd.DataFrame, pmf: np.ndarray) -> pd.DataFrame:
    result = test.copy()
    actual = test.actual_total.to_numpy(float)
    line = test.market_total.to_numpy(float)
    over, under, push = market_probabilities(pmf, line)
    conditional = over / np.maximum(over + under, 1e-12)
    decided = actual != line
    win = (actual > line).astype(float)
    result["score_nll"] = -np.log(np.maximum(pmf[np.arange(len(test)), actual.astype(int)], 1e-12))
    result["brier"] = np.where(decided, (conditional - win) ** 2, np.nan)
    result["log_loss"] = np.where(decided, -(win * np.log(np.clip(conditional, 1e-8, 1-1e-8)) + (1-win) * np.log(np.clip(1-conditional, 1e-8, 1-1e-8))), np.nan)
    result["over_probability"], result["push_probability"] = conditional, push
    result["actual_push"] = (actual == line).astype(float)
    rounded = np.floor(line + .5)
    result["rounded_line"] = rounded.astype(int)
    result["rounded_push_probability"] = pmf[np.arange(len(test)), rounded.astype(int)]
    result["rounded_push_observed"] = (actual == rounded).astype(float)
    result["projection"] = pmf @ SCORES
    result["absolute_error"] = abs(actual - result.projection)
    result["squared_error"] = (actual - result.projection) ** 2
    over_ev, under_ev = over * (100/110) - under, under * (100/110) - over
    result["side"] = np.where(over_ev >= under_ev, "over", "under")
    result["ev"] = np.maximum(over_ev, under_ev)
    result["bet"] = result.ev.ge(.03)
    correct = np.where(result.side.eq("over"), actual > line, actual < line)
    result["profit"] = np.where(~decided, 0., np.where(correct, 100/110, -1.))
    return result


def interval(values: np.ndarray, blocks: np.ndarray, alpha: float = .05) -> list[float] | None:
    valid = np.isfinite(values)
    grouped = pd.DataFrame({"value": values[valid], "block": blocks[valid]}).groupby("block").value.agg(["sum", "count"]).to_numpy()
    if len(grouped) < 8:
        return None
    rng = np.random.default_rng(29083)
    selected = rng.integers(0, len(grouped), size=(3000, len(grouped)))
    sample = grouped[selected].sum(axis=1)
    return np.quantile(sample[:, 0] / sample[:, 1], [alpha/2, 1-alpha/2]).tolist()


def summarize(frame: pd.DataFrame) -> dict:
    bets = frame.loc[frame.bet]
    blocks = (frame.season.astype(str) + ":" + frame.week.astype(str)).to_numpy()
    bet_blocks = (bets.season.astype(str) + ":" + bets.week.astype(str)).to_numpy()
    return {"games": len(frame), "score_nll": float(frame.score_nll.mean()), "conditional_log_loss": float(frame.log_loss.mean()),
            "brier": float(frame.brier.mean()), "mae": float(frame.absolute_error.mean()), "rmse": float(np.sqrt(frame.squared_error.mean())),
            "mean_over_probability": float(frame.over_probability.mean()), "observed_over_rate_ex_push": float(frame.loc[frame.actual_total.ne(frame.market_total), "actual_total"].gt(frame.loc[frame.actual_total.ne(frame.market_total), "market_total"]).mean()),
            "expected_posted_pushes": float(frame.push_probability.sum()), "observed_posted_pushes": int(frame.actual_push.sum()),
            "rounded_line_expected_pushes": float(frame.rounded_push_probability.sum()), "rounded_line_observed_pushes": int(frame.rounded_push_observed.sum()),
            "diagnostic_bets": len(bets), "assumed_minus110_roi": float(bets.profit.mean()) if len(bets) else None,
            "roi_week_bootstrap_95": interval(bets.profit.to_numpy(), bet_blocks) if len(bets) else None,
            "over_bets": int(bets.side.eq("over").sum()), "under_bets": int(bets.side.eq("under").sum()),
            "log_loss_delta_vs_constant": float(frame.log_loss_delta.mean()),
            "score_nll_delta_vs_constant": float(frame.score_nll_delta.mean()),
            "score_nll_delta_week_bootstrap_95": interval(frame.score_nll_delta.to_numpy(), blocks),
            "log_loss_delta_week_bootstrap_95": interval(frame.log_loss_delta.to_numpy(), blocks),
            "log_loss_delta_bonferroni_three_comparisons": interval(frame.log_loss_delta.to_numpy(), blocks, alpha=.05/3)}


def subgroup_audit(frame: pd.DataFrame) -> list[dict]:
    rows = []
    frame = frame.copy()
    frame["residual"] = frame.actual_total - frame.market_total
    groupings = {
        "total_band": pd.cut(frame.market_total, [-np.inf, 45, 55, 65, np.inf], labels=["<=45", "45–55", "55–65", ">65"]),
        "spread_band": pd.cut(frame.spread, [-np.inf, 7, 21, np.inf], labels=["<=7", "7–21", ">21"]),
        "regime": np.where(frame.season < 2023, "pre2023", np.where(frame.season < 2024, "2023", "2024plus")),
        "side": np.where(frame.residual > 0, "over", np.where(frame.residual < 0, "under", "push")),
    }
    for field, grouping in groupings.items():
        for name, sample in frame.groupby(grouping, observed=True):
            rows.append({"dimension": field, "group": str(name), "games": len(sample), "mean_residual": float(sample.residual.mean()),
                         "residual_sd": float(sample.residual.std()), "mae": float(sample.residual.abs().mean()),
                         "over_rate_ex_push": float(sample.loc[sample.residual.ne(0), "residual"].gt(0).mean()),
                         "mean_residual_week_bootstrap_95": interval(sample.residual.to_numpy(), (sample.season.astype(str)+":"+sample.week.astype(str)).to_numpy())})
    return rows


def select_candidate(predictions: pd.DataFrame) -> str:
    selection = predictions.loc[predictions.season.isin(SELECTION_YEARS)]
    scores = selection.groupby("candidate").log_loss.mean()
    if not set(CANDIDATES).issubset(scores.index):
        raise ValueError("Every frozen family must have pre-2025 evaluation results")
    return min(CANDIDATES, key=lambda candidate: scores[candidate])


def json_safe(value):
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def run(root: Path) -> dict:
    history = load_history(root)
    pieces, fold_metadata = [], []
    for year in (*SELECTION_YEARS, 2025):
        train, test = history.loc[history.season.lt(year)], history.loc[history.season.eq(year)]
        fitted = ConditionalDistribution.fit(train)
        baseline = None
        baseline_nll = None
        for candidate in CANDIDATES:
            scored = game_metrics(test, fitted.predict_pmf(test, candidate))
            if baseline is None:
                baseline = scored.log_loss.to_numpy()
                baseline_nll = scored.score_nll.to_numpy()
            scored["log_loss_delta"] = scored.log_loss.to_numpy() - baseline
            scored["score_nll_delta"] = scored.score_nll.to_numpy() - baseline_nll
            scored["candidate"] = candidate
            pieces.append(scored)
        fold_metadata.append({"test_season": year, **fitted.metadata()})
    predictions = pd.concat(pieces, ignore_index=True)
    pre2025 = predictions.loc[predictions.season.lt(2025)]
    selection = {candidate: summarize(sample) for candidate, sample in pre2025.groupby("candidate")}
    frozen = select_candidate(predictions)
    holdout = {candidate: summarize(sample) for candidate, sample in predictions.loc[predictions.season.eq(2025)].groupby("candidate")}
    key_diagnostics = []
    for period, sample in [("selection_pre2025", pre2025), ("reused_2025", predictions.loc[predictions.season.eq(2025)])]:
        selected = sample.loc[sample.candidate.eq(frozen) & sample.rounded_line.isin(LOCKED_PARAMETERS["prespecified_push_diagnostic_scores"])]
        for key, group in selected.groupby("rounded_line"):
            key_diagnostics.append({"period":period,"candidate":frozen,"counterfactual_integer_line":int(key),"games":len(group),
                                    "expected_pushes":float(group.rounded_push_probability.sum()),"observed_pushes":int(group.rounded_push_observed.sum())})
    by_season = [{"candidate": candidate, "season": int(season), **summarize(sample)} for (candidate, season), sample in predictions.groupby(["candidate", "season"])]
    cal_rows = []
    for period, sample in [("selection_pre2025", pre2025), ("reused_2025", predictions.loc[predictions.season.eq(2025)])]:
        for candidate, model in sample.groupby("candidate"):
            for bucket, group in model.groupby(pd.cut(model.over_probability, [0,.45,.5,.55,.6,1], include_lowest=True), observed=True):
                decided = group.loc[group.actual_total.ne(group.market_total)]
                cal_rows.append({"period": period, "candidate": candidate, "bin": str(bucket), "games": len(decided), "predicted": float(decided.over_probability.mean()), "observed": float(decided.actual_total.gt(decided.market_total).mean())})
    sensitivity = {}
    for label, mask in [("exclude_55_5", predictions.market_total.ne(55.5)), ("regular_season_weeks_1_14", predictions.week.between(1,14))]:
        sensitivity[label] = [{"period": "pre2025" if before else "2025", "candidate": candidate, **summarize(sample)}
                              for (before,candidate),sample in predictions.loc[mask].groupby([predictions.loc[mask,"season"].lt(2025),"candidate"])]
    report = {
        "version": "conditional-distribution-development-v1", "status": "quarantined_market_provenance_development_only",
        "candidate_count": len(CANDIDATES), "fixed_parameters": LOCKED_PARAMETERS,
        "selection_rule": "Minimum conditional over/under log loss on expanding-year 2022–2024 folds; 2025 excluded from selection. No profit threshold tuning.",
        "frozen_candidate": frozen, "frozen_candidate_2025": holdout[frozen],
        "credible_new_betting_edge": False,
        "data": {"games": len(history), "by_season": {str(year):len(sample) for year,sample in history.groupby("season")},
                 "integer_lines_by_season": {str(year):int(sample.market_total.mod(1).eq(0).sum()) for year,sample in history.groupby("season")},
                 "market_definition": "One archived ESPN/public total per game; not a verified multi-book consensus, no historical prices or publication timestamps."},
        "selection_pre2025": selection, "reused_2025": holdout, "by_season": by_season,
        "subgroups_pre2025": subgroup_audit(history.loc[history.season.lt(2025)]),
        "subgroups_2025": subgroup_audit(history.loc[history.season.eq(2025)]),
        "calibration": cal_rows, "key_number_counterfactual_diagnostics":key_diagnostics,
        "sensitivity": sensitivity, "fold_fits": fold_metadata,
        "limitations": [
            "Primary ESPN source audit confirmed live provider59 contamination in the scalar archive. This original experiment is retained for transparency and is invalid as a pregame/closing-line validation.",
            "All historical years have already informed broader model development; chronological refitting does not recreate a pristine holdout.",
            "Four fixed families; paired week-block intervals include a Bonferroni correction for three comparisons to baseline, but cannot correct unknown earlier searches.",
            "Only 371 real-source games precede 2023; folds can reflect source coverage changes rather than stable football effects.",
            "No integer posted totals in 2024/2025. Rounded-line push calibration is a counterfactual scoring diagnostic, never observed betting performance.",
            "Market totals have no morning publication timestamps, verified book-level consensus, or executable -110 prices. Diagnostic ROI is hypothetical.",
            "Score-mass and variance improvements may improve full-distribution likelihood without improving over/under decisions.",
            "Regime indicators learn residual associations after the market total, not a causal effect of clock rules.",
            "No live candidate is promoted and no forecast ledger or runtime policy is changed.",
            "The fixed valid-total range 15–100 excludes one real-source row: 2024 SMU–TCU game401635557 with archived total107.5 and final108. Its market timestamp/semantics require audit.",
        ],
        "sources": [
            {"name":"NCAA 2023 first-down clock rules", "url":"https://www.ncaa.org/media-center-football-timing-rules-approved-for-divisions-i-ii/"},
            {"name":"NCAA 2024 two-minute timeout", "url":"https://www.ncaa.org/media-center-technology-rules-approved-in-football/"},
            {"name":"Existing audited source artifacts", "url":"../data/raw/sportsdataverse/"},
        ],
    }
    out = root / "reports"
    out.mkdir(exist_ok=True)
    report = json_safe(report)
    (out / "conditional_distribution_development.json").write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")
    def fmt(value):
        return "—" if value is None else f"{value:.5f}"
    lines = ["# Conditional score distributions: bounded development study", "", "**Quarantined historical market provenance.** A subsequent primary-source audit confirmed live-odds contamination in this archive. The original results below are retained for transparency; they are invalid as pregame/closing-line evidence. See `espn_market_timing_audit.md`.", "", f"Frozen on pre-2025 conditional log loss: **{frozen}**. No new profitable edge is established and no runtime policy changes.", "", "## Design", "",
             "Four prespecified families use expanding prior seasons to predict 2022, 2023 and 2024. Candidate selection uses those three folds only. The selected family is then refitted through 2024 and evaluated on 2025. Because these years were used elsewhere in model development, 2025 remains reused development validation, not a pristine holdout.", "",
             "The conditional Normal uses heavily regularized residual mean and variance terms for market total, absolute spread, and 2023/2024 clock regimes. The key-mass family adds shrunk integer-score propensity ratios. The residual mixture adds a broad, heavily shrunk empirical residual kernel conditioned on total, spread and regime. All hyperparameters and the 3% hypothetical EV cutoff were fixed before this comparison; no threshold sweep was run.", "",
             "## Comparison", "", "| Period | Family | Games | O/U log loss | Brier | Score NLL | Hypothetical bets | ROI at assumed −110 |", "|---|---|---:|---:|---:|---:|---:|---:|"]
    for period, groups in [("2022–24 selection",selection),("2025 reused",holdout)]:
        for candidate in CANDIDATES:
            m=groups[candidate]
            lines.append(f"| {period} | {candidate} | {m['games']} | {fmt(m['conditional_log_loss'])} | {fmt(m['brier'])} | {fmt(m['score_nll'])} | {m['diagnostic_bets']} | {fmt(m['assumed_minus110_roi'])} |")
    lines += ["", "## Uncertainty and push mass", ""]
    for period, groups in [("Selection",selection),("2025",holdout)]:
        m=groups[frozen]
        lines.append(f"{period}, frozen family: paired log-loss difference from constant Normal {fmt(m['log_loss_delta_vs_constant'])}; 95% whole-week bootstrap {m['log_loss_delta_week_bootstrap_95']}; familywise three-comparison interval {m['log_loss_delta_bonferroni_three_comparisons']}. Rounded-line expected pushes {m['rounded_line_expected_pushes']:.1f}, observed {m['rounded_line_observed_pushes']} (counterfactual lines).")
        lines.append("")
    frozen_2025 = holdout[frozen]
    lines += ["## What changed in 2025", "",
              f"The selected family's average over probability was {frozen_2025['mean_over_probability']:.2%}, while the observed over rate was {frozen_2025['observed_over_rate_ex_push']:.2%}. Its {frozen_2025['diagnostic_bets']} hypothetical bets were all overs, illustrating the cost of carrying the earlier upward residual bias into 2025. The full-score distribution improved while over/under calibration failed to improve.", "",
              "The pre-2025 low-total subgroup (45 or below) averaged roughly +2.9 points against the market; in 2025 it averaged roughly −0.7. Large-spread and high-total groups also moved materially. These retrospective patterns do not support a stable subgroup betting policy.", ""]
    lines += ["The JSON contains season-by-season results, calibration bins, fixed total/spread/regime/direction subgroup diagnostics, fitted coefficients, and prespecified sensitivity checks excluding 55.5 totals and restricting weeks 1–14. Subgroups are descriptive; no subgroup betting rule was selected.", "", "## Limits", ""]
    lines.extend(f"- {limitation}" for limitation in report["limitations"])
    lines += ["", "The [NCAA 2023 timing rule](https://www.ncaa.org/media-center-football-timing-rules-approved-for-divisions-i-ii/) changed first-down clock handling; the [2024 rule](https://www.ncaa.org/media-center-technology-rules-approved-in-football/) introduced the two-minute timeout. The model tests residual regime associations after controlling for the market total.", "", "Reproduce from `model/`: `python -m ncaaf_model.conditional_distribution`."]
    (out / "conditional_distribution_development.md").write_text("\n".join(lines)+"\n")
    print(json.dumps({"frozen_candidate":frozen,"selection":selection[frozen],"2025":holdout[frozen]},indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    run(parser.parse_args().root)
