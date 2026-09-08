"""Fixed, portable drive/clock challengers and development-only evaluation.

Raw score and drive histories are filtered before any feature is calculated.
This module deliberately does not use EPA, opaque season ratings, or oriented
field-position coordinates. It does not confer betting eligibility.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from scipy.stats import t as student_t

VERSION = "drive-clock-v1"
CANDIDATES = ("drive_clock_shrink", "drive_clock_ridge")
TERMINAL = {"END OF GAME", "END OF HALF", "END OF QUARTER", "END OF 4TH QUARTER", "KICKOFF", "UNKNOWN", ""}
TURNOVERS = {"INT", "INT TD", "FUMBLE", "FUMBLE RETURN TD"}
# Fixed weak priors, not estimated from the evaluation seasons.
PRIORS = {"td_rate": .27, "fg_rate": .10, "turnover_rate": .11,
          "drives": 11.5, "seconds_per_drive": 145., "plays_per_drive": 5.5,
          "seconds_per_play": 26.5, "other_points": 1.5}
FEATURES = ["drive_minus_market", "clock_minus_market", "td_matchup_sum",
            "fg_matchup_sum", "turnover_matchup_sum", "seconds_per_play_sum",
            "plays_per_drive_sum", "other_points_sum", "market_total",
            "abs_spread", "week", "clock_rule_2023", "two_minute_rule_2024"]
RIDGE_ALPHA = 500.0
MIN_HISTORY = 5
EDGE_GATE = 6.0
EV_GATE = .03


def _seconds(value: Any) -> float:
    try:
        minutes, seconds = str(value).split(":")
        minutes, seconds = int(minutes), int(seconds)
        return float(60 * minutes + seconds) if 0 <= seconds < 60 and 0 <= minutes <= 15 else np.nan
    except (ValueError, TypeError):
        return np.nan


def aggregate_drive_team_games(drives: pd.DataFrame, schedule: pd.DataFrame) -> pd.DataFrame:
    """One team-game row from finished games with adequate regulation data.

    TD means exactly the offensive TD result; return scores and overtime are left
    in a separately regressed score component. Terminal possessions contribute
    clock time but do not dilute scoring opportunities. Six hours
after kickoff is an explicit conservative availability proxy, not a historical
publication timestamp. Source archives can contain later corrections.
"""
    if drives.empty or schedule.empty:
        return pd.DataFrame()
    schedule = schedule.drop_duplicates("game_id", keep="last").copy()
    for col in ("home_score", "away_score", "home_id", "away_id", "season"):
        schedule[col] = pd.to_numeric(schedule[col], errors="coerce")
    completed = schedule["status"].astype(str).str.upper().str.contains("FINAL", na=False)
    schedule = schedule.loc[completed & schedule[["home_score", "away_score"]].notna().all(axis=1)].copy()
    schedule["game_date"] = pd.to_datetime(schedule["game_date"], utc=True, errors="coerce")
    schedule = schedule.loc[schedule["game_date"].notna()]
    d = drives.drop_duplicates(["game_id", "drive_id"]).copy()
    for col in ("team_id", "start_period", "end_period", "offensive_plays"):
        d[col] = pd.to_numeric(d[col], errors="coerce")
    d = d.loc[d["start_period"].between(1, 4) & d["end_period"].between(1, 4) & d["team_id"].notna()].copy()
    d["result"] = d["result"].fillna("").astype(str).str.upper().str.strip()
    d["active"] = ~d["result"].isin(TERMINAL) & d["offensive_plays"].gt(0)
    d["td"] = (d["result"] == "TD").astype(float)
    d["fg"] = (d["result"] == "FG").astype(float)
    d["turnover"] = d["result"].isin(TURNOVERS).astype(float)
    d["seconds"] = d["time_elapsed"].map(_seconds)
    rows: list[dict[str, Any]] = []
    schedules = schedule.set_index("game_id")
    for (game_id, team_id), group in d.groupby(["game_id", "team_id"], sort=False):
        if game_id not in schedules.index:
            continue
        game = schedules.loc[game_id]
        if team_id not in (game["home_id"], game["away_id"]):
            continue
        active = group.loc[group["active"]]
        if len(active) < 5 or active["offensive_plays"].sum() < 20:
            continue
        is_home = team_id == game["home_id"]
        points = float(game["home_score"] if is_home else game["away_score"])
        seconds = group["seconds"].dropna()
        plays = group["offensive_plays"].clip(lower=0).sum()
        td, fg = active["td"].sum(), active["fg"].sum()
        rows.append({"game_id": int(game_id), "team_id": int(team_id),
                     "opponent_id": int(game["away_id"] if is_home else game["home_id"]),
                     "season": int(game["season"]), "game_date": game["game_date"],
                     "available_at": game["game_date"] + pd.Timedelta(hours=6),
                     "td_rate": td / len(active), "fg_rate": fg / len(active),
                     "turnover_rate": active["turnover"].mean(),
                     "drives": float(len(active)),
                     "seconds_per_drive": float(seconds.sum() / len(active)) if len(seconds) >= .8 * len(group) else np.nan,
                     "plays_per_drive": float(plays / len(active)),
                     "seconds_per_play": float(seconds.sum() / plays) if plays > 0 and len(seconds) >= .8 * len(group) else np.nan,
                     # Includes XP differences, defense/ST scores and overtime.
                     "other_points": float(np.clip(points - 7 * td - 3 * fg, -4, 25)),
                     "active_drives": len(active), "terminal_drives": len(group) - len(active)})
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    # Both opponents must have sufficient drive coverage for defense features.
    opponents = frame[["game_id", "team_id"] + list(PRIORS)].rename(
        columns={"team_id": "opponent_id", **{x: "def_" + x for x in PRIORS}})
    return frame.merge(opponents, on=["game_id", "opponent_id"], how="inner").sort_values(["game_date", "game_id", "team_id"]).reset_index(drop=True)


def load_drive_team_games(raw_dir: Path, seasons: Iterable[int]) -> pd.DataFrame:
    pieces = []
    for season in seasons:
        dp = Path(raw_dir) / f"drives_{season}.parquet"
        sp = Path(raw_dir) / f"cfb_schedule_{season}.parquet"
        if dp.exists() and sp.exists():
            piece = aggregate_drive_team_games(pd.read_parquet(dp), pd.read_parquet(sp))
            if not piece.empty:
                pieces.append(piece)
    return pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame()


def _state(history: pd.DataFrame, season: int) -> dict[str, Any]:
    if len(history):
        history = history.sort_values(["available_at", "game_id"]).tail(24)
    # Older games decay by game count and again across offseason roster turnover.
    ages = np.arange(len(history) - 1, -1, -1)
    years = np.maximum(0, season - history["season"].to_numpy(float)) if len(history) else np.array([])
    weights = .85 ** ages * .5 ** years
    state: dict[str, Any] = {"history_games": len(history),
                            "current_season_games": int((history["season"] == season).sum()) if len(history) else 0,
                            "last_available_at": history["available_at"].max() if len(history) else pd.NaT}
    for prefix in ("", "def_"):
        for name, default in PRIORS.items():
            column = prefix + name
            values = history[column].to_numpy(float) if len(history) else np.array([])
            valid = np.isfinite(values)
            state[column] = float((3. * default + np.dot(weights[valid], values[valid])) / (3. + weights[valid].sum()))
    return state


def build_drive_features(games: pd.DataFrame, team_games: pd.DataFrame, as_of: Any = None) -> pd.DataFrame:
    """Identical historical/live transformation; as_of caps all live histories.

    Output retains input rows/order and supplies eligibility plus history audit
    fields. A game never contributes to its own features, even after it ends.
    """
    out = games.copy().reset_index(drop=True)
    grouped = {} if team_games.empty else {int(k): v for k, v in team_games.groupby("team_id")}
    as_of_ts = pd.to_datetime(as_of, utc=True) if as_of is not None else None
    records = []
    for _, game in out.iterrows():
        kickoff = pd.to_datetime(game.get("game_date", game.get("commence_time")), utc=True, errors="coerce")
        if pd.isna(kickoff):
            kickoff = pd.to_datetime(game.get("commence_time"), utc=True, errors="coerce")
        if pd.isna(kickoff):
            raise ValueError("Each drive-model matchup needs a valid game_date/commence_time")
        cutoff = min(kickoff, as_of_ts) if as_of_ts is not None else kickoff
        season = int(game.get("season", kickoff.year))
        states = {}
        record = {}
        for side in ("home", "away"):
            team_id = pd.to_numeric(game.get(f"{side}_id"), errors="coerce")
            history = grouped.get(int(team_id), pd.DataFrame()) if pd.notna(team_id) else pd.DataFrame()
            if len(history):
                history = history.loc[(history["available_at"] <= cutoff) & (history["game_id"] != game.get("game_id", game.get("espn_game_id")))]
            state = _state(history, season)
            states[side] = state
            record[f"{side}_drive_history_games"] = state["history_games"]
            record[f"{side}_drive_season_games"] = state["current_season_games"]
            record[f"{side}_drive_last_available_at"] = state["last_available_at"]
        h, a = states["home"], states["away"]
        def matchup(side: dict, opponent: dict, metric: str) -> float:
            return .5 * (side[metric] + opponent["def_" + metric])
        home_td, away_td = matchup(h, a, "td_rate"), matchup(a, h, "td_rate")
        home_fg, away_fg = matchup(h, a, "fg_rate"), matchup(a, h, "fg_rate")
        home_ppd, away_ppd = 7 * home_td + 3 * home_fg, 7 * away_td + 3 * away_fg
        projected_drives = np.clip(.25 * (h["drives"] + a["def_drives"] + a["drives"] + h["def_drives"]), 7, 17)
        duration_sum = matchup(h, a, "seconds_per_drive") + matchup(a, h, "seconds_per_drive")
        clock_drives = float(np.clip(3600 / max(duration_sum, 120), 7, 17))
        other = h["other_points"] + a["other_points"]
        raw_total = projected_drives * (home_ppd + away_ppd) + other
        clock_total = (.5 * projected_drives + .5 * clock_drives) * (home_ppd + away_ppd) + other
        market = float(game["market_total"])
        spread = pd.to_numeric(game.get("market_home_spread", 0), errors="coerce")
        record.update({"drive_raw_total": raw_total, "drive_clock_total": clock_total,
                       "drive_projected_possessions": projected_drives, "drive_clock_possessions": clock_drives,
                       "drive_home_points_per_drive": home_ppd, "drive_away_points_per_drive": away_ppd,
                       "drive_minus_market": raw_total - market, "clock_minus_market": clock_total - market,
                       "td_matchup_sum": home_td + away_td, "fg_matchup_sum": home_fg + away_fg,
                       "turnover_matchup_sum": matchup(h, a, "turnover_rate") + matchup(a, h, "turnover_rate"),
                       "seconds_per_play_sum": h["seconds_per_play"] + a["seconds_per_play"],
                       "plays_per_drive_sum": h["plays_per_drive"] + a["plays_per_drive"],
                       "other_points_sum": other, "abs_spread": float(abs(spread)) if pd.notna(spread) else 0.,
                       "clock_rule_2023": float(season >= 2023), "two_minute_rule_2024": float(season >= 2024),
                       "drive_features_as_of": cutoff,
                       "drive_history_eligible": min(h["history_games"], a["history_games"]) >= MIN_HISTORY})
        records.append(record)
    for column in pd.DataFrame(records):
        out[column] = [record[column] for record in records]
    if "week" not in out:
        out["week"] = 0.
    return out


def fit_drive_artifact(features: pd.DataFrame, allow_prior_fallback: bool = False) -> dict[str, Any]:
    """Fixed strongly regularized linear residual; portable NumPy coefficients."""
    train = features.loc[features["drive_history_eligible"] & features[["actual_total", "market_total"]].notna().all(axis=1)]
    if len(train) < 100:
        if not allow_prior_fallback:
            raise ValueError("At least 100 history-qualified completed games required")
        return {"artifact_version": VERSION, "candidates": list(CANDIDATES), "feature_columns": FEATURES,
                "median": [0.] * len(FEATURES), "center": [0.] * len(FEATURES), "scale": [1.] * len(FEATURES),
                "intercept": 0., "coefficients": [0.] * len(FEATURES), "ridge_alpha": RIDGE_ALPHA,
                "shrink_weight": .25, "maximum_adjustment": 8., "residual_sigma": 16., "student_df": 7,
                "training_games": len(train), "training_seasons": sorted(train["season"].astype(int).unique().tolist()),
                "edge_gate_points": EDGE_GATE, "ev_gate": EV_GATE, "minimum_history": MIN_HISTORY,
                "bet_eligible": False, "status": "insufficient_training_ridge_equals_market",
                "sigma_status": "fixed_16_point_prior_not_estimated"}
    x = train[FEATURES].to_numpy(float)
    median = np.nanmedian(x, axis=0)
    median = np.where(np.isfinite(median), median, 0.)
    x = np.where(np.isfinite(x), x, median)
    center, scale = x.mean(axis=0), x.std(axis=0)
    scale = np.where(scale > 1e-8, scale, 1.)
    z = (x - center) / scale
    y = (train["actual_total"] - train["market_total"]).to_numpy(float)
    # Intercept is also regularized: absence of evidence retains market center.
    design = np.column_stack([np.ones(len(z)), z])
    penalty = np.eye(design.shape[1]) * RIDGE_ALPHA
    coefficients = np.linalg.solve(design.T @ design + penalty, design.T @ y)
    return {"artifact_version": VERSION, "candidates": list(CANDIDATES), "feature_columns": FEATURES,
            "median": median.tolist(), "center": center.tolist(), "scale": scale.tolist(),
            "intercept": float(coefficients[0]), "coefficients": coefficients[1:].tolist(),
            "ridge_alpha": RIDGE_ALPHA, "shrink_weight": .25, "maximum_adjustment": 8.,
            "residual_sigma": float(max(10., np.std(y, ddof=1))), "student_df": 7,
            "training_games": len(train), "training_seasons": sorted(train["season"].astype(int).unique().tolist()),
            "edge_gate_points": EDGE_GATE, "ev_gate": EV_GATE, "minimum_history": MIN_HISTORY,
            "bet_eligible": False, "status": "prospective_shadow_only",
            "availability": "Prior completed game kickoff + 6 hours; publication timestamps unavailable",
            "feature_contract": {"history_limit": 24, "game_decay": .85, "offseason_decay": .5, "prior_weight": 3., "priors": PRIORS}}


def drive_projections(features: pd.DataFrame, artifact: dict[str, Any]) -> dict[str, np.ndarray]:
    if features.empty:
        return {candidate: np.array([], dtype=float) for candidate in CANDIDATES}
    market = features["market_total"].to_numpy(float)
    x = features[artifact["feature_columns"]].to_numpy(float)
    x = np.where(np.isfinite(x), x, np.asarray(artifact["median"]))
    z = (x - np.asarray(artifact["center"])) / np.asarray(artifact["scale"])
    residual = artifact["intercept"] + z @ np.asarray(artifact["coefficients"])
    eligible = features["drive_history_eligible"].to_numpy(bool)
    shrink = artifact["shrink_weight"] * np.clip(features["clock_minus_market"].to_numpy(float), -16, 16)
    return {"drive_clock_shrink": market + np.where(eligible, shrink, 0.),
            "drive_clock_ridge": market + np.where(eligible, np.clip(residual, -artifact["maximum_adjustment"], artifact["maximum_adjustment"]), 0.)}


def drive_probabilities(projection: Any, line: Any, sigma: float, df: int = 7) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Integer score support, including exactly one integer-line push bin."""
    if sigma <= 0 or df <= 2:
        raise ValueError("Positive sigma and df > 2 required")
    projection, line = np.asarray(projection, float), np.asarray(line, float)
    integer = np.isclose(line, np.round(line), atol=1e-8, rtol=0.)
    # Works for arbitrary positive total lines, not just integers/half-points.
    over_cut = np.floor(line) + .5
    under_cut = np.ceil(line) - .5
    scale = sigma * np.sqrt((df - 2.) / df)
    over = student_t.sf((over_cut - projection) / scale, df)
    under = student_t.cdf((under_cut - projection) / scale, df)
    push = np.where(integer, np.maximum(0., 1. - over - under), 0.)
    return over, under, push


def _week_interval(frame: pd.DataFrame, values: str, denominator: str | None = None) -> list[float] | None:
    """Percentile interval resampling whole season/week blocks, 2,000 draws."""
    frame = frame.copy()
    if "game_date" in frame:
        dates = pd.to_datetime(frame["game_date"], utc=True)
        frame["_calendar_week"] = dates.dt.strftime("%G-%V")
        grouping = ["season", "_calendar_week"]
    else:
        grouping = ["season", "week"]
    cols = [values] + ([denominator] if denominator else [])
    blocks = frame.groupby(grouping)[cols].sum()
    if denominator is None:
        blocks["_n"] = frame.groupby(grouping).size()
        denominator = "_n"
    if len(blocks) < 3 or blocks[denominator].sum() <= 0:
        return None
    rng = np.random.default_rng(20260908)
    idx = rng.integers(0, len(blocks), size=(2000, len(blocks)))
    numerator = blocks[values].to_numpy()[idx].sum(axis=1)
    den = blocks[denominator].to_numpy()[idx].sum(axis=1)
    valid = den > 0
    return np.quantile(numerator[valid] / den[valid], [.025, .975]).tolist()


def _metrics(frame: pd.DataFrame) -> dict[str, Any]:
    f = frame.copy()
    f["absolute_error"] = abs(f["actual_total"] - f["projected_total"])
    f["paired_mae_delta"] = f["absolute_error"] - abs(f["actual_total"] - f["market_total"])
    bets = f.loc[f["research_signal"]]
    decided = f["actual_total"] != f["market_total"]
    p = np.clip(f.loc[decided, "p_over"] / (1 - f.loc[decided, "p_push"]), 1e-8, 1 - 1e-8)
    y = (f.loc[decided, "actual_total"] > f.loc[decided, "market_total"]).astype(float)
    return {"games": len(f), "mae": float(f["absolute_error"].mean()),
            "rmse": float(np.sqrt(np.mean((f["actual_total"] - f["projected_total"]) ** 2))),
            "paired_mae_delta": float(f["paired_mae_delta"].mean()),
            "paired_mae_delta_week_bootstrap_95": _week_interval(f, "paired_mae_delta"),
            "conditional_over_brier": float(np.mean((p - y) ** 2)),
            "conditional_over_log_loss": float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))),
            "expected_pushes": float(f["p_push"].sum()), "observed_pushes": int((~decided).sum()),
            "bets": len(bets), "wins": int((bets["profit"] > 0).sum()),
            "losses": int((bets["profit"] < 0).sum()), "pushes": int((bets["profit"] == 0).sum()),
            "profit_units": float(bets["profit"].sum()), "assumed_minus110_roi": float(bets["profit"].mean()) if len(bets) else None,
            "roi_week_bootstrap_95": _week_interval(bets, "profit") if len(bets) else None}


def run_drive_research(model_root: Path) -> dict[str, Any]:
    root = Path(model_root)
    raw = root / "data" / "raw" / "sportsdataverse"
    team_games = load_drive_team_games(raw, range(2019, 2026))
    pieces = []
    for season in range(2019, 2026):
        schedule = pd.read_parquet(raw / f"cfb_schedule_{season}.parquet")
        lines = pd.read_parquet(raw / f"betting_{season}.parquet")
        # The upstream archive inserts synthetic defaults for missing markets.
        # These are not quoted closing prices and cannot enter a market test.
        lines = lines.loc[lines["odds_source"].isin(["core_odds_api", "summary_pickcenter"])]
        duplicate = lines.duplicated("game_id", keep=False)
        lines = lines.loc[~duplicate]
        lines = lines[["game_id", "over_under", "home_team_spread"]].rename(columns={"over_under": "market_total", "home_team_spread": "market_home_spread"})
        games = schedule.merge(lines.drop_duplicates("game_id"), on="game_id", how="inner")
        for column in ("home_score", "away_score", "market_total"):
            games[column] = pd.to_numeric(games[column], errors="coerce")
        games["actual_total"] = games["home_score"] + games["away_score"]
        games = games.loc[games["status"].astype(str).str.upper().str.contains("FINAL") & games[["actual_total", "market_total"]].notna().all(axis=1)]
        pieces.append(games)
    features = build_drive_features(pd.concat(pieces, ignore_index=True), team_games)
    fold_rows = []
    fold_metrics = []
    for season in (2023, 2024, 2025):
        train = features.loc[features["season"] < season]
        valid = features.loc[(features["season"] == season) & features["drive_history_eligible"]].copy()
        artifact = fit_drive_artifact(train, allow_prior_fallback=True)
        projections = {"market_only": valid["market_total"].to_numpy(float), **drive_projections(valid, artifact)}
        for candidate, prediction in projections.items():
            f = valid[["game_id", "season", "week", "game_date", "home_team", "away_team", "market_total", "actual_total"]].copy()
            f["candidate"], f["projected_total"] = candidate, prediction
            f["training_status"] = artifact["status"]
            f["p_over"], f["p_under"], f["p_push"] = drive_probabilities(prediction, f["market_total"], artifact["residual_sigma"])
            over = f["projected_total"] >= f["market_total"]
            win_probability = np.where(over, f["p_over"], f["p_under"])
            lose_probability = np.where(over, f["p_under"], f["p_over"])
            f["modeled_ev"] = win_probability * 100 / 110 - lose_probability
            f["research_signal"] = (abs(f["projected_total"] - f["market_total"]) >= EDGE_GATE) & (f["modeled_ev"] >= EV_GATE) & (candidate != "market_only")
            win = np.where(over, f["actual_total"] > f["market_total"], f["actual_total"] < f["market_total"])
            f["profit"] = np.where(f["actual_total"] == f["market_total"], 0., np.where(win, 100 / 110, -1.))
            fold_rows.append(f)
            fold_metrics.append({"season": season, "candidate": candidate, "training_games": artifact["training_games"], "training_status": artifact["status"], **_metrics(f)})
    predictions = pd.concat(fold_rows, ignore_index=True)
    aggregate = {candidate: _metrics(f) for candidate, f in predictions.groupby("candidate")}
    fitted_period = predictions.loc[predictions["training_status"] == "prospective_shadow_only"]
    artifact = fit_drive_artifact(features)
    report = {"artifact_version": VERSION, "evaluation_status": "reused_historical_development_only",
              "design": {"folds": [2023, 2024, 2025], "training": "expanding prior seasons starting 2019", "candidate_count": 2,
                         "selection": "No threshold search; fixed 6-point and 3% EV diagnostic gate", "ridge_alpha": RIDGE_ALPHA,
                         "confidence": "2,000 whole season/week block bootstrap resamples; descriptive, not multiplicity-adjusted",
                         "historical_prices": "Resolved closing totals, assumed -110 both sides; not 06:30 AM executable prices"},
              "coverage": {"team_game_rows": len(team_games), "historical_games": len(features),
                           "history_qualified_games": int(features["drive_history_eligible"].sum()), "evaluation_games": int(len(predictions) / 3)},
              "metrics": aggregate, "by_season": fold_metrics,
              "fitted_period_metrics": {candidate: _metrics(f) for candidate, f in fitted_period.groupby("candidate")},
              "promotion": "Neither candidate is promoted by this analysis; record both prospectively with zero betting stake",
              "limitations": ["2019–2025 already informed earlier model development; these are not untouched tests.",
                              "Only 18 pre-2023 games have real totals and five prior drive-covered games for both teams; 2023 ridge stays at market, with fixed 16-point prior sigma.",
                              "No archived publication timestamps: six-hour game-availability proxy and retrospectively corrected public data.",
                              "Clock pace is game-state dependent; no snap-level neutral-score filter in this bounded candidate.",
                              "Offensive TD values approximate XP as seven; separate other-points form absorbs XP, return points and overtime.",
                              "Fixed weak priors and no field-position feature; sparse histories revert to market.",
                              "Week bootstrap intervals do not establish future profitability or adjust for earlier candidate searches."]}
    models_dir, reports_dir = root / "data" / "models", root / "reports"
    models_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    team_games.to_parquet(models_dir / "drive_history.parquet", index=False)
    (models_dir / "drive_clock_v1.json").write_text(json.dumps(artifact, indent=2, allow_nan=False) + "\n")
    (reports_dir / "drive_clock_development_summary.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    (root / "data" / "normalized").mkdir(parents=True, exist_ok=True)
    features.to_parquet(root / "data" / "normalized" / "drive_historical_features.parquet", index=False)
    predictions.to_csv(reports_dir / "drive_clock_development_predictions.csv", index=False)
    pd.DataFrame(fold_metrics).to_csv(reports_dir / "drive_clock_development_by_season.csv", index=False)
    markdown = ["# Drive/clock development study", "", "Two fixed challengers; expanding prior-year fits; reused historical development periods. Neither is approved for live betting.", "",
                "| Candidate | Games | MAE | MAE difference vs market | Bets | Assumed -110 ROI | Week bootstrap ROI interval |",
                "|---|---:|---:|---:|---:|---:|---|"]
    for name, m in aggregate.items():
        roi = f"{m['assumed_minus110_roi']:.2%}" if m["assumed_minus110_roi"] is not None else "—"
        ci = m["roi_week_bootstrap_95"]
        interval = f"{ci[0]:.2%} to {ci[1]:.2%}" if ci else "—"
        markdown.append(f"| {name} | {m['games']} | {m['mae']:.3f} | {m['paired_mae_delta']:+.3f} | {m['bets']} | {roi} | {interval} |")
    markdown += ["", "The shared game sample requires five prior drive-covered games per team. Positive MAE difference means worse forecasting than the market.", "",
                 "Historical prices are resolved closing totals with assumed -110 odds. These results cannot reconstruct a morning line-shopping strategy.", "",
                 "Features use earlier completed-game drive counts, offensive TD/FG/turnover rates, clock per drive/play and separate other points. Fixed shrink: 25% of clock-model discrepancy, clipped to four points. Ridge: 13 standardized features, alpha 500, intercept also regularized, adjustment capped at eight points.", "",
                 "The fixed diagnostic gate is six points and 3% modeled EV. The shrink candidate is consequently a forecast-only comparator: its four-point maximum cannot qualify. The gate was not reduced after seeing these results.", "",
                 *["- " + x for x in report["limitations"]], "",
                 "Source: https://cfbfastr.sportsdataverse.org/ and the existing hashed SportsDataverse raw files."]
    (reports_dir / "drive_clock_development.md").write_text("\n".join(markdown) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fixed drive/clock historical development study")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    result = run_drive_research(args.root)
    print(json.dumps({"status": result["evaluation_status"], "coverage": result["coverage"], "metrics": result["metrics"]}, indent=2))
