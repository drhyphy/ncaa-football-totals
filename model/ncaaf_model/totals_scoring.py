from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from statistics import median
from typing import Any

import joblib
import numpy as np
import pandas as pd

from .config import Settings
from .math import american_to_decimal, devig_pair, kelly_fraction
from .public_ensemble import load_live_public_projection
from .public_features import attach_public_features
from .scoring import _outcomes, _teams_match
from .storage import append_csv, atomic_write_bytes, atomic_write_json, utc_now
from .totals_backtest import RECOMMENDED_TOTAL_MODEL, candidate_projections, probability_over
from .totals_features import live_game_features


def normalize_totals_odds(payload: list[dict[str, Any]], allowed_books: tuple[str, ...]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    allowed = set(allowed_books)
    for event in payload:
        quotes: list[dict[str, Any]] = []
        spreads: list[float] = []
        updates: list[str] = []
        for bookmaker in event.get("bookmakers", []):
            book = str(bookmaker.get("key", ""))
            if book not in allowed:
                continue
            total_outcomes = _outcomes(bookmaker, "totals")
            over_by_line = {
                float(outcome["point"]): outcome
                for outcome in total_outcomes
                if str(outcome.get("name", "")).lower() == "over" and outcome.get("point") is not None
            }
            under_by_line = {
                float(outcome["point"]): outcome
                for outcome in total_outcomes
                if str(outcome.get("name", "")).lower() == "under" and outcome.get("point") is not None
            }
            for line in sorted(set(over_by_line) & set(under_by_line)):
                over_price = float(over_by_line[line]["price"])
                under_price = float(under_by_line[line]["price"])
                try:
                    fair_over, fair_under = devig_pair(over_price, under_price)
                except ValueError:
                    continue
                quotes.append(
                    {
                        "book": book,
                        "line": line,
                        "over_price": over_price,
                        "under_price": under_price,
                        "fair_over": fair_over,
                        "fair_under": fair_under,
                        "last_update": bookmaker.get("last_update"),
                    }
                )
            for outcome in _outcomes(bookmaker, "spreads"):
                if str(outcome.get("name", "")) == str(event.get("home_team", "")) and outcome.get("point") is not None:
                    spreads.append(float(outcome["point"]))
            if bookmaker.get("last_update"):
                updates.append(str(bookmaker["last_update"]))
        if not quotes:
            continue
        lines = [quote["line"] for quote in quotes]
        consensus_line = float(median(lines))
        nearest_distance = min(abs(quote["line"] - consensus_line) for quote in quotes)
        nearest = [quote for quote in quotes if abs(quote["line"] - consensus_line) == nearest_distance]
        rows.append(
            {
                "event_id": str(event["id"]),
                "commence_time": event["commence_time"],
                "home_team": event["home_team"],
                "away_team": event["away_team"],
                "market_total": consensus_line,
                "market_home_spread": float(median(spreads)) if spreads else np.nan,
                "total_book_count": len({quote["book"] for quote in quotes}),
                "line_dispersion": max(lines) - min(lines),
                "market_over_probability": float(median([quote["fair_over"] for quote in nearest])),
                "latest_book_update": max(updates) if updates else None,
                "quotes": quotes,
            }
        )
    return pd.DataFrame(rows)


def attach_totals_schedule(odds: pd.DataFrame, schedule: pd.DataFrame) -> pd.DataFrame:
    schedule = schedule.copy()
    schedule["scheduled_time"] = pd.to_datetime(schedule["game_date"], utc=True, errors="coerce")
    matched: list[dict[str, Any]] = []
    for _, event in odds.iterrows():
        kickoff = pd.to_datetime(event["commence_time"], utc=True)
        window = schedule.loc[(schedule["scheduled_time"] - kickoff).abs().le(pd.Timedelta(hours=36))]
        candidates = window.loc[
            window.apply(
                lambda row: _teams_match(str(event["home_team"]), str(row["home_team"]))
                and _teams_match(str(event["away_team"]), str(row["away_team"])),
                axis=1,
            )
        ]
        record = event.to_dict()
        if len(candidates) == 1:
            game = candidates.iloc[0]
            record.update(
                {
                    "espn_game_id": int(game["game_id"]),
                    "home_id": int(game["home_id"]),
                    "away_id": int(game["away_id"]),
                    "week": int(game["week"]),
                    "season": int(game["season"]),
                    "neutral_site": bool(game["neutral_site"]),
                    "schedule_match": True,
                }
            )
        else:
            record.update(
                {
                    "espn_game_id": np.nan,
                    "home_id": np.nan,
                    "away_id": np.nan,
                    "week": 0,
                    "season": int(pd.to_numeric(schedule["season"], errors="coerce").dropna().max()),
                    "neutral_site": False,
                    "schedule_match": False,
                }
            )
        matched.append(record)
    return pd.DataFrame(matched)


def _best_quote(game: pd.Series, projection: float, side: str, sigma: float, df: int) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for quote in game["quotes"]:
        line = float(quote["line"])
        is_integer = math.isclose(line, round(line), abs_tol=1e-9)
        if is_integer:
            p_over = float(probability_over(projection, line + 0.5, sigma, df))
            p_under = float(1.0 - probability_over(projection, line - 0.5, sigma, df))
            push_probability = max(0.0, 1.0 - p_over - p_under)
        else:
            p_over = float(probability_over(projection, line, sigma, df))
            p_under = 1.0 - p_over
            push_probability = 0.0
        probability = p_over if side == "over" else p_under
        price = float(quote[f"{side}_price"])
        payout = american_to_decimal(price) - 1.0
        loss_probability = max(0.0, 1.0 - probability - push_probability)
        candidates.append(
            {
                "sportsbook": quote["book"],
                "line": line,
                "american_odds": int(price),
                "model_probability": probability,
                "expected_value": probability * payout - loss_probability,
                "market_probability": float(quote[f"fair_{side}"]),
                "push_probability": push_probability,
                "conditional_win_probability": probability / max(probability + loss_probability, 1e-9),
                "last_update": quote.get("last_update"),
            }
        )
    return max(candidates, key=lambda quote: quote["expected_value"])


def score_totals_candidates(
    games: pd.DataFrame,
    models: dict[str, Any],
    artifact: dict[str, Any],
    settings: Settings,
    snapshot_time: str,
    extra_projections: dict[str, np.ndarray] | None = None,
    candidate_thresholds: dict[str, float] | None = None,
) -> pd.DataFrame:
    features = list(artifact["feature_columns"])
    projections = candidate_projections(games, models, features, settings)
    if extra_projections:
        projections.update(extra_projections)
    sigma = float(artifact["distribution"]["sigma"])
    df = int(artifact["distribution"]["df"])
    snapshot_dt = pd.to_datetime(snapshot_time, utc=True)
    records: list[dict[str, Any]] = []
    for row_index, game in games.iterrows():
        kickoff = pd.to_datetime(game["commence_time"], utc=True)
        history_min = min(float(game.get("home_prior_games", 0) or 0), float(game.get("away_prior_games", 0) or 0))
        for candidate, values in projections.items():
            projection = float(values[row_index])
            side_records: list[dict[str, Any]] = []
            for side in ("over", "under"):
                execution = _best_quote(game, projection, side, sigma, df)
                execution_update = pd.to_datetime(execution.get("last_update"), utc=True, errors="coerce")
                quote_age = (
                    (snapshot_dt - execution_update).total_seconds() / 3600.0
                    if pd.notna(execution_update)
                    else math.nan
                )
                model_edge = (
                    projection - float(game["market_total"])
                    if side == "over"
                    else float(game["market_total"]) - projection
                )
                line_value = (
                    float(game["market_total"]) - float(execution["line"])
                    if side == "over"
                    else float(execution["line"]) - float(game["market_total"])
                )
                decision_edge = line_value if candidate == RECOMMENDED_TOTAL_MODEL else model_edge
                flags: list[str] = []
                if not bool(game["schedule_match"]):
                    flags.append("schedule_unmatched")
                if kickoff <= snapshot_dt:
                    flags.append("game_started")
                if int(game["total_book_count"]) < settings.min_books:
                    flags.append("too_few_books")
                if float(game["line_dispersion"]) > settings.totals_max_line_dispersion:
                    flags.append("line_dispersion_high")
                if history_min < settings.totals_min_team_history:
                    flags.append("team_history_sparse")
                required_edge = (
                    settings.totals_min_line_value_points
                    if candidate == RECOMMENDED_TOTAL_MODEL
                    else (candidate_thresholds or {}).get(candidate, settings.totals_min_edge_points)
                )
                if decision_edge < required_edge:
                    flags.append("edge_below_threshold")
                if execution["expected_value"] < settings.totals_min_ev:
                    flags.append("ev_below_threshold")
                if math.isfinite(quote_age) and quote_age > 24.0:
                    flags.append("quote_stale")
                eligible_model = candidate == RECOMMENDED_TOTAL_MODEL
                paper_bet = eligible_model and not flags
                shadow_signal = bool(candidate_thresholds and candidate in candidate_thresholds and not flags)
                kelly = kelly_fraction(execution["conditional_win_probability"], execution["american_odds"])
                stake = (
                    min(kelly * settings.totals_fractional_kelly, settings.totals_max_bankroll_fraction)
                    if paper_bet
                    else 0.0
                )
                prediction_id = hashlib.sha256(
                    f"totals|{snapshot_time}|{game['event_id']}|{candidate}|{side}".encode()
                ).hexdigest()[:20]
                record = {
                    "prediction_id": prediction_id,
                    "snapshot_time": snapshot_time,
                    "event_id": game["event_id"],
                    "espn_game_id": game["espn_game_id"],
                    "commence_time": game["commence_time"],
                    "hours_to_kickoff": (kickoff - snapshot_dt).total_seconds() / 3600.0,
                    "week": int(game["week"]),
                    "away_team": game["away_team"],
                    "home_team": game["home_team"],
                    "candidate": candidate,
                    "bet_eligible_model": eligible_model,
                    "shadow_signal": shadow_signal,
                    "side": side,
                    "sportsbook": execution["sportsbook"],
                    "line": execution["line"],
                    "american_odds": execution["american_odds"],
                    "decimal_odds": american_to_decimal(execution["american_odds"]),
                    "consensus_total": game["market_total"],
                    "projected_total": projection,
                    "model_edge_points": model_edge,
                    "line_value_points": line_value,
                    "decision_edge_points": decision_edge,
                    "validated_edge_threshold": required_edge if math.isfinite(required_edge) else np.nan,
                    "model_probability": execution["model_probability"],
                    "push_probability": execution["push_probability"],
                    "market_probability": execution["market_probability"],
                    "probability_edge": execution["model_probability"] - execution["market_probability"],
                    "expected_value": execution["expected_value"],
                    "total_book_count": int(game["total_book_count"]),
                    "line_dispersion": float(game["line_dispersion"]),
                    "quote_age_hours": quote_age,
                    "home_prior_games": game.get("home_prior_games"),
                    "away_prior_games": game.get("away_prior_games"),
                    "paper_bet": paper_bet,
                    "stake_bankroll_fraction": stake,
                    "quality_flags": "|".join(flags),
                }
                side_records.append(record)
            # At most one position per candidate/game, even if stale or crossed
            # book quotes make both sides appear positive.
            eligible = [record for record in side_records if record["paper_bet"]]
            if len(eligible) > 1:
                winner = max(eligible, key=lambda record: record["expected_value"])["prediction_id"]
                for record in side_records:
                    if record["paper_bet"] and record["prediction_id"] != winner:
                        record["paper_bet"] = False
                        record["stake_bankroll_fraction"] = 0.0
                        record["quality_flags"] = "opposite_side_preferred"
            records.extend(side_records)
    return pd.DataFrame(records)


def render_totals_report(scored: pd.DataFrame, snapshot_time: str) -> str:
    primary = scored.loc[scored["candidate"].eq(RECOMMENDED_TOTAL_MODEL)].copy()
    bets = primary.loc[primary["paper_bet"]].sort_values("expected_value", ascending=False)
    lines = [
        "# NCAA football totals research card",
        "",
        f"Snapshot: {snapshot_time}",
        "",
        "Status: forward paper research. No automated wagering.",
        "",
        f"Qualified consensus-shopping paper bets: {len(bets)}",
        "",
    ]
    if bets.empty:
        lines.append("No totals cleared every precommitted price, edge, freshness, and data-quality gate.")
    else:
        lines.extend(
            [
                "| Game | Pick | Book | Projection | Consensus | Probability | EV | Stake |",
                "|---|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for _, row in bets.iterrows():
            price = f"+{int(row['american_odds'])}" if row["american_odds"] > 0 else str(int(row["american_odds"]))
            lines.append(
                f"| {row['away_team']} at {row['home_team']} | {row['side'].title()} {row['line']:.1f} | "
                f"{row['sportsbook']} {price} | {row['projected_total']:.1f} | {row['consensus_total']:.1f} | "
                f"{row['model_probability']:.1%} | {row['expected_value']:.1%} | {row['stake_bankroll_fraction']:.2%} |"
            )
    lines.extend(
        [
            "",
            "## Largest recommended-candidate line advantages",
            "",
            "| Game | Side | Best line | Projection | Edge | EV | Eligible | Flags |",
            "|---|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    best_side = primary.sort_values("decision_edge_points", ascending=False).drop_duplicates("event_id").head(25)
    for _, row in best_side.iterrows():
        lines.append(
            f"| {row['away_team']} at {row['home_team']} | {row['side'].title()} | {row['line']:.1f} | "
            f"{row['projected_total']:.1f} | {row['decision_edge_points']:.1f} | {row['expected_value']:.1%} | "
            f"{'yes' if row['paper_bet'] else 'no'} | {row['quality_flags'] or '—'} |"
        )
    shadows = scored.loc[scored.get("shadow_signal", False)].sort_values("expected_value", ascending=False)
    if not shadows.empty:
        lines.extend(
            [
                "",
                "## Public-model shadow signals",
                "",
                "These clear their own walk-forward validation gates but are not authorized paper bets.",
                "",
                "| Game | Candidate | Pick | Projection | Edge | EV |",
                "|---|---|---:|---:|---:|---:|",
            ]
        )
        for _, row in shadows.head(25).iterrows():
            lines.append(
                f"| {row['away_team']} at {row['home_team']} | {row['candidate']} | "
                f"{row['side'].title()} {row['line']:.1f} | {row['projected_total']:.1f} | "
                f"{row['model_edge_points']:.1f} | {row['expected_value']:.1%} |"
            )
    lines.extend(
        [
            "",
            "All candidates are appended to the ledger. Only `market_consensus_shop` is paper-bet eligible; the fundamental models remain shadow research.",
            "",
        ]
    )
    return "\n".join(lines)


def run_totals_snapshot(settings: Settings, odds_path: Path) -> dict[str, Any]:
    artifact = json.loads((settings.models_dir / "totals_artifact_latest.json").read_text(encoding="utf-8"))
    models = joblib.load(settings.models_dir / "totals_models_latest.joblib")
    states = pd.read_parquet(settings.models_dir / "totals_team_states_latest.parquet")
    payload = json.loads(odds_path.read_text(encoding="utf-8"))
    odds = normalize_totals_odds(payload, settings.allowed_books)
    schedule = pd.read_parquet(settings.raw_dir / "sportsdataverse" / f"cfb_schedule_{settings.season}.parquet")
    matched = attach_totals_schedule(odds, schedule)
    games = live_game_features(matched, states)
    extra_projections: dict[str, np.ndarray] = {}
    candidate_thresholds: dict[str, float] = {}
    public_path = settings.models_dir / "public_superensemble_v2.joblib"
    if public_path.exists():
        completed = schedule.loc[schedule["status"].eq("STATUS_FINAL")]
        latest_completed_week = int(completed["week"].max()) if not completed.empty else 0
        games = attach_public_features(
            games,
            settings,
            [settings.season],
            live_latest_week=latest_completed_week,
        )
        public_base, public_projection, public_metadata = load_live_public_projection(games, settings)
        market = games["market_total"].to_numpy(float)
        extra_projections = {
            **{f"public_{family}": market + public_base[family].to_numpy(float) for family in public_base},
            "public_superensemble_v2": public_projection,
        }
        for family, selection in public_metadata.get("family_threshold_selection", {}).items():
            value = selection.get("selected", {}).get("threshold")
            candidate_thresholds[f"public_{family}"] = float(value) if value is not None else math.inf
        ensemble_value = public_metadata.get("threshold_selection", {}).get("selected", {}).get("threshold")
        candidate_thresholds["public_superensemble_v2"] = (
            float(ensemble_value) if ensemble_value is not None else math.inf
        )
    snapshot_time = utc_now()
    scored = score_totals_candidates(
        games,
        models,
        artifact,
        settings,
        snapshot_time,
        extra_projections=extra_projections,
        candidate_thresholds=candidate_thresholds,
    )
    stamp = snapshot_time.replace(":", "").replace("-", "")
    settings.snapshots_dir.mkdir(parents=True, exist_ok=True)
    scored.to_parquet(settings.snapshots_dir / f"totals_predictions_{stamp}.parquet", index=False)
    atomic_write_json(settings.snapshots_dir / f"totals_predictions_{stamp}.json", scored.to_dict(orient="records"))
    append_csv(settings.ledger_dir / "totals_predictions.csv", scored, dedupe_key="prediction_id")
    atomic_write_bytes(settings.reports_dir / "totals_latest.md", (render_totals_report(scored, snapshot_time) + "\n").encode())
    return {
        "snapshot_time": snapshot_time,
        "events_with_totals": int(len(odds)),
        "schedule_matches": int(matched["schedule_match"].sum()),
        "candidate_rows": int(len(scored)),
        "qualified_paper_bets": int(scored["paper_bet"].sum()),
        "report": str(settings.reports_dir / "totals_latest.md"),
    }
