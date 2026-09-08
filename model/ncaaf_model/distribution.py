"""Discrete final-score probabilities and leave-one-book-out price inference.

Probabilities include overtime and integer-total pushes. These are model
estimates, not confidence intervals for a profitable betting strategy.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.stats import norm, t

SCORES = np.arange(0, 251, dtype=float)


def probabilities(center: float, line: float, sigma: float, family: str = "normal") -> tuple[float, float, float]:
    """Return unconditional over, under, push probabilities on score integers."""
    sigma = max(6., min(float(sigma), 30.))
    scale = sigma * np.sqrt(5 / 7) if family == "student_t7" else sigma
    dist = t(df=7) if family == "student_t7" else norm
    # Truncation at zero, with all upper-tail mass retained.
    lower_mass = dist.cdf((-.5 - center) / scale)
    def cdf(boundary: float) -> float:
        return float(np.clip((dist.cdf((boundary - center) / scale) - lower_mass) / (1 - lower_mass), 0, 1))
    if abs(line - round(line)) < 1e-8:
        under = cdf(line - .5)
        over = 1 - cdf(line + .5)
        return over, under, max(0., 1 - over - under)
    under = cdf(np.floor(line) + .5)
    return 1 - under, under, 0.


def expected_value(center: float, line: float, american: float, side: str, sigma: float, family: str = "normal") -> tuple[float, float, float]:
    over, under, push = probabilities(center, line, sigma, family)
    win, loss = (over, under) if side == "over" else (under, over)
    payout = american / 100 if american > 0 else 100 / abs(american)
    return win * payout - loss, win, push


def infer_center(line: float, fair_over: float, sigma: float, family: str = "normal") -> float:
    """Match paired no-vig price CONDITIONAL on no push, not 1/decimal directly."""
    def objective(center: float) -> float:
        over, under, _ = probabilities(center, line, sigma, family)
        return over / max(over + under, 1e-12) - fair_over
    return float(brentq(objective, line - 45, line + 45))


def fit_distribution(frame: pd.DataFrame, family: str = "normal") -> dict:
    errors = (frame.actual_total - frame.market_total).to_numpy(float)
    sigma = float(np.sqrt(np.mean(errors ** 2)))
    return {"family": family, "sigma": sigma, "training_games": len(frame),
            "training_max_season": int(frame.season.max()), "integer_pushes": True,
            "status": "retrospective_development_only"}


def evaluate_distributions(frame: pd.DataFrame, root: Path) -> dict:
    rows = []
    # Fixed two-family comparison. No profit threshold optimized here.
    for season in (2023, 2024, 2025):
        train = frame.loc[frame.season.lt(season)]
        test = frame.loc[frame.season.eq(season)]
        for family in ("normal", "student_t7"):
            artifact = fit_distribution(train, family)
            sigma = artifact["sigma"]
            nll, brier, cover80, cover95 = [], [], [], []
            for game in test.itertuples():
                _, _, mass = probabilities(game.market_total, game.actual_total, sigma, family)
                nll.append(-np.log(max(mass, 1e-12)))
                po, pu, _ = probabilities(game.market_total, game.market_total, sigma, family)
                if game.actual_total != game.market_total:
                    brier.append((po / (po + pu) - float(game.actual_total > game.market_total)) ** 2)
                scale = sigma * np.sqrt(5/7) if family == "student_t7" else sigma
                dist = t(df=7) if family == "student_t7" else norm
                residual = abs(game.actual_total - game.market_total)
                cover80.append(residual <= dist.ppf(.90) * scale)
                cover95.append(residual <= dist.ppf(.975) * scale)
            rows.append({"season": season, "family": family, "games": len(test),
                         "nll": float(np.mean(nll)), "brier": float(np.mean(brier)),
                         "coverage_80": float(np.mean(cover80)), "coverage_95": float(np.mean(cover95)),
                         "sigma": sigma})
    metrics = pd.DataFrame(rows)
    # Selection is a development choice; only future snapshots test this frozen decision.
    family = metrics.groupby("family").apply(lambda g: np.average(g.nll, weights=g.games), include_groups=False).idxmin()
    artifact = fit_distribution(frame.loc[frame.season.ge(2023)], family)
    artifact["development_folds"] = rows
    artifact["selection"] = "Lowest 2023–2025 expanding-year score log loss; reused development data, not a new holdout."
    path = root / "data/models/score_distribution_v1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact, indent=2) + "\n")
    return artifact
