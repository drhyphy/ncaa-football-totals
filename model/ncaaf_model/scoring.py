from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np
import pandas as pd

from .config import Settings
from .math import (
    american_to_decimal,
    blend_logit,
    devig_pair,
    expected_value,
    kelly_fraction,
    logit,
    expit,
)
from .storage import append_csv, atomic_write_bytes, atomic_write_json, utc_now
from .teams import best_prefix_match, normalize_team, pair_key
from .timing import EASTERN, timing_bucket


CANDIDATES = (
    "market_fpi_residual",
    "market_public_ensemble",
    "fpi_only",
    "ratings_only",
    "market_only",
)


def _market_map(margin: float, total: float | None, metadata: dict[str, Any]) -> float:
    fallback = float(metadata["total_median"])
    game_total = fallback if total is None or not math.isfinite(float(total)) else float(total)
    x = [float(margin), float(margin) / math.sqrt(max(game_total, 1.0))]
    linear = float(metadata["intercept"]) + sum(float(a) * b for a, b in zip(metadata["coefficients"], x))
    return expit(linear)


def _outcomes(market: dict[str, Any], key: str) -> list[dict[str, Any]]:
    for candidate in market.get("markets", []):
        if candidate.get("key") == key:
            return list(candidate.get("outcomes", []))
    return []


def normalize_current_odds(payload: list[dict[str, Any]], allowed_books: tuple[str, ...]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    allowed = set(allowed_books)
    for event in payload:
        home_team = str(event["home_team"])
        away_team = str(event["away_team"])
        book_probs: list[float] = []
        home_prices: list[tuple[float, str]] = []
        away_prices: list[tuple[float, str]] = []
        home_spreads: list[float] = []
        totals: list[float] = []
        book_updates: list[str] = []
        for bookmaker in event.get("bookmakers", []):
            book = str(bookmaker.get("key", ""))
            if book not in allowed:
                continue
            h2h = _outcomes(bookmaker, "h2h")
            by_name = {normalize_team(outcome.get("name", "")): outcome for outcome in h2h}
            home_outcome = by_name.get(normalize_team(home_team))
            away_outcome = by_name.get(normalize_team(away_team))
            if home_outcome and away_outcome:
                home_price = float(home_outcome["price"])
                away_price = float(away_outcome["price"])
                try:
                    p_home, _ = devig_pair(home_price, away_price)
                except ValueError:
                    pass
                else:
                    book_probs.append(p_home)
                    home_prices.append((home_price, book))
                    away_prices.append((away_price, book))
                    if bookmaker.get("last_update"):
                        book_updates.append(str(bookmaker["last_update"]))
            spread = _outcomes(bookmaker, "spreads")
            for outcome in spread:
                if normalize_team(outcome.get("name", "")) == normalize_team(home_team) and outcome.get("point") is not None:
                    home_spreads.append(float(outcome["point"]))
            total_market = _outcomes(bookmaker, "totals")
            for outcome in total_market:
                if str(outcome.get("name", "")).lower() == "over" and outcome.get("point") is not None:
                    totals.append(float(outcome["point"]))
        if not book_probs:
            continue
        best_home_price, best_home_book = max(home_prices, key=lambda value: value[0])
        best_away_price, best_away_book = max(away_prices, key=lambda value: value[0])
        rows.append(
            {
                "event_id": event["id"],
                "commence_time": event["commence_time"],
                "home_team": home_team,
                "away_team": away_team,
                "pair_key": pair_key(away_team, home_team),
                "book_count": len(book_probs),
                "market_home_probability": float(median(book_probs)),
                "market_away_probability": 1.0 - float(median(book_probs)),
                "consensus_dispersion": max(book_probs) - min(book_probs),
                "best_home_american": int(best_home_price),
                "best_away_american": int(best_away_price),
                "best_home_book": best_home_book,
                "best_away_book": best_away_book,
                "consensus_home_spread": float(median(home_spreads)) if home_spreads else np.nan,
                "consensus_total": float(median(totals)) if totals else np.nan,
                "latest_book_update": max(book_updates) if book_updates else None,
            }
        )
    return pd.DataFrame(rows)


def _teams_match(left: str, right: str) -> bool:
    a, b = normalize_team(left), normalize_team(right)
    return a == b or (min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a)))


def attach_schedule(odds: pd.DataFrame, schedule: pd.DataFrame, power: pd.DataFrame) -> pd.DataFrame:
    schedule = schedule.copy()
    schedule["scheduled_time"] = pd.to_datetime(schedule["game_date"], utc=True)
    home_power = power.rename(
        columns={"team_id": "home_id", "gameprojection": "fpi_home_probability", "teampredptdiff": "fpi_home_margin"}
    )[["game_id", "home_id", "fpi_home_probability", "fpi_home_margin"]]
    schedule = schedule.merge(home_power, on=["game_id", "home_id"], how="left")
    matched: list[dict[str, Any]] = []
    for _, event in odds.iterrows():
        kickoff = pd.to_datetime(event["commence_time"], utc=True)
        # Far-ahead feeds sometimes publish date-only placeholders one UTC day apart.
        window = schedule.loc[(schedule["scheduled_time"] - kickoff).abs().le(pd.Timedelta(hours=36))]
        candidates = window.loc[
            window.apply(
                lambda row: _teams_match(event["home_team"], row["home_team"])
                and _teams_match(event["away_team"], row["away_team"]),
                axis=1,
            )
        ]
        base = event.to_dict()
        if len(candidates) == 1:
            game = candidates.iloc[0]
            base.update(
                {
                    "espn_game_id": int(game["game_id"]),
                    "week": int(game["week"]),
                    "neutral_site": bool(game["neutral_site"]),
                    "schedule_match": True,
                    "fpi_home_probability": float(game["fpi_home_probability"]) / 100.0
                    if pd.notna(game["fpi_home_probability"])
                    else np.nan,
                    "fpi_home_margin": float(game["fpi_home_margin"]) if pd.notna(game["fpi_home_margin"]) else np.nan,
                }
            )
        else:
            base.update(
                {
                    "espn_game_id": np.nan,
                    "week": np.nan,
                    "neutral_site": False,
                    "schedule_match": False,
                    "fpi_home_probability": np.nan,
                    "fpi_home_margin": np.nan,
                }
            )
        matched.append(base)
    return pd.DataFrame(matched)


def rating_strengths(ratings: pd.DataFrame) -> pd.DataFrame:
    frame = ratings.copy()
    point_pair = frame[["fpi", "sp_plus"]].mean(axis=1)
    valid = frame["fei"].notna() & point_pair.notna()
    slope, intercept = np.polyfit(frame.loc[valid, "fei"].astype(float), point_pair.loc[valid].astype(float), 1)
    frame["fei_points"] = frame["fei"].astype(float) * slope + intercept
    frame["public_strength"] = frame[["fpi", "sp_plus", "fei_points"]].median(axis=1, skipna=True)
    return frame


def attach_ratings(frame: pd.DataFrame, ratings: pd.DataFrame, home_field_points: float, market_metadata: dict[str, Any]) -> pd.DataFrame:
    ratings = rating_strengths(ratings)
    teams = ratings["team"].astype(str).tolist()
    lookup = ratings.set_index("team")["public_strength"].to_dict()
    output = frame.copy()
    home_values: list[float] = []
    away_values: list[float] = []
    home_names: list[str | None] = []
    away_names: list[str | None] = []
    rating_probabilities: list[float] = []
    for _, row in output.iterrows():
        home_name = best_prefix_match(str(row["home_team"]), teams)
        away_name = best_prefix_match(str(row["away_team"]), teams)
        home_names.append(home_name)
        away_names.append(away_name)
        home = float(lookup[home_name]) if home_name else np.nan
        away = float(lookup[away_name]) if away_name else np.nan
        home_values.append(home)
        away_values.append(away)
        if math.isfinite(home) and math.isfinite(away):
            margin = home - away + (0.0 if bool(row["neutral_site"]) else home_field_points)
            total = float(row["consensus_total"]) if pd.notna(row["consensus_total"]) else None
            rating_probabilities.append(_market_map(margin, total, market_metadata))
        else:
            rating_probabilities.append(np.nan)
    output["rating_home_team"] = home_names
    output["rating_away_team"] = away_names
    output["rating_home_strength"] = home_values
    output["rating_away_strength"] = away_values
    output["ratings_home_probability"] = rating_probabilities
    return output


def _public_ensemble_probability(row: pd.Series, rating_weight: float) -> float:
    fpi = row.get("fpi_home_probability")
    ratings = row.get("ratings_home_probability")
    if pd.notna(fpi) and pd.notna(ratings):
        return expit((1.0 - rating_weight) * logit(float(fpi)) + rating_weight * logit(float(ratings)))
    if pd.notna(fpi):
        return float(fpi)
    if pd.notna(ratings):
        return float(ratings)
    return np.nan


def score_candidates(frame: pd.DataFrame, artifact: dict[str, Any], settings: Settings, snapshot_time: str) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    snapshot_dt = pd.to_datetime(snapshot_time, utc=True)
    for _, game in frame.iterrows():
        market_home = float(game["market_home_probability"])
        fpi = float(game["fpi_home_probability"]) if pd.notna(game["fpi_home_probability"]) else np.nan
        ratings = float(game["ratings_home_probability"]) if pd.notna(game["ratings_home_probability"]) else np.nan
        public = _public_ensemble_probability(game, settings.rating_ensemble_game_weight)
        week = int(game["week"]) if pd.notna(game["week"]) else 0
        alpha_key = "weeks_0_4" if week <= 4 else "weeks_5_plus"
        alpha = float(artifact["live_alpha"][alpha_key])
        probabilities = {
            "market_fpi_residual": blend_logit(market_home, fpi, alpha) if math.isfinite(fpi) else np.nan,
            "market_public_ensemble": blend_logit(market_home, public, alpha) if math.isfinite(public) else np.nan,
            "fpi_only": fpi,
            "ratings_only": ratings,
            "market_only": market_home,
        }
        kickoff = pd.to_datetime(game["commence_time"], utc=True)
        future = kickoff > pd.Timestamp.now(tz="UTC")
        hours_to_kickoff = (kickoff - snapshot_dt).total_seconds() / 3600.0
        horizon = timing_bucket(hours_to_kickoff)
        base_flags: list[str] = []
        if not bool(game["schedule_match"]):
            base_flags.append("schedule_unmatched")
        if int(game["book_count"]) < settings.min_books:
            base_flags.append("too_few_books")
        if float(game["consensus_dispersion"]) > settings.max_consensus_dispersion:
            base_flags.append("market_dispersion_high")
        if not future:
            base_flags.append("game_started")
        for candidate in CANDIDATES:
            home_probability = probabilities[candidate]
            if not math.isfinite(home_probability):
                continue
            for side in ("home", "away"):
                probability = home_probability if side == "home" else 1.0 - home_probability
                market_probability = market_home if side == "home" else 1.0 - market_home
                american = int(game[f"best_{side}_american"])
                sportsbook = str(game[f"best_{side}_book"])
                ev = expected_value(probability, american)
                edge = probability - market_probability
                flags = list(base_flags)
                if candidate == "market_fpi_residual" and not math.isfinite(fpi):
                    flags.append("fpi_missing")
                if edge < settings.min_probability_edge:
                    flags.append("edge_below_threshold")
                if ev < settings.min_ev:
                    flags.append("ev_below_threshold")
                eligible_model = candidate == "market_fpi_residual"
                paper_bet = eligible_model and not flags
                raw_kelly = kelly_fraction(probability, american)
                stake = min(raw_kelly * settings.fractional_kelly, settings.max_bankroll_fraction) if paper_bet else 0.0
                identifier = hashlib.sha256(
                    f"{snapshot_time}|{game['event_id']}|{candidate}|{side}".encode()
                ).hexdigest()[:20]
                records.append(
                    {
                        "prediction_id": identifier,
                        "snapshot_time": snapshot_time,
                        "event_id": game["event_id"],
                        "espn_game_id": game["espn_game_id"],
                        "commence_time": game["commence_time"],
                        "hours_to_kickoff": hours_to_kickoff,
                        "timing_bucket": horizon,
                        "snapshot_date_et": snapshot_dt.tz_convert(EASTERN).strftime("%Y-%m-%d"),
                        "kickoff_date_et": kickoff.tz_convert(EASTERN).strftime("%Y-%m-%d"),
                        "week": week,
                        "away_team": game["away_team"],
                        "home_team": game["home_team"],
                        "candidate": candidate,
                        "bet_eligible_model": eligible_model,
                        "side": side,
                        "selection": game[f"{side}_team"],
                        "model_artifact_version": artifact.get("artifact_version", "unknown"),
                        "model_artifact_created_at": artifact.get("created_at"),
                        "sportsbook": sportsbook,
                        "american_odds": american,
                        "decimal_odds": american_to_decimal(american),
                        "market_probability": market_probability,
                        "model_probability": probability,
                        "probability_edge": edge,
                        "expected_value": ev,
                        "book_count": int(game["book_count"]),
                        "consensus_dispersion": float(game["consensus_dispersion"]),
                        "consensus_home_spread": game["consensus_home_spread"],
                        "consensus_total": game["consensus_total"],
                        "fpi_home_probability": fpi,
                        "ratings_home_probability": ratings,
                        "public_ensemble_home_probability": public,
                        "market_alpha": alpha if candidate.startswith("market_") and candidate != "market_only" else 0.0,
                        "paper_bet": paper_bet,
                        "stake_bankroll_fraction": stake,
                        "quality_flags": "|".join(flags),
                    }
                )
    return pd.DataFrame(records)


def render_latest(scored: pd.DataFrame, snapshot_time: str, artifact: dict[str, Any]) -> str:
    recommended = scored.loc[scored["candidate"].eq("market_fpi_residual")].sort_values("expected_value", ascending=False)
    bets = recommended.loc[recommended["paper_bet"]]
    lines = [
        "# NCAA football moneyline research card",
        "",
        f"Snapshot: {snapshot_time}",
        "",
        "Status: forward paper research only. Historical moneyline ROI has not been validated.",
        "",
        (
            "Leading specification: 75% of fitted FPI residual "
            f"(alpha {float(artifact['live_alpha']['weeks_0_4']):.1%} in weeks 0–4; "
            f"{float(artifact['live_alpha']['weeks_5_plus']):.1%} in weeks 5+)."
        ),
        "",
        f"Qualified paper bets: {len(bets)}",
        "",
    ]
    if bets.empty:
        lines.append("No recommended-model selections cleared every precommitted gate.")
    else:
        lines.extend(["| Game | Selection | Best price | Model | Market | EV | Stake |", "|---|---:|---:|---:|---:|---:|---:|"])
        for _, row in bets.iterrows():
            game = f"{row['away_team']} at {row['home_team']}"
            odds = f"+{int(row['american_odds'])}" if row["american_odds"] > 0 else str(int(row["american_odds"]))
            lines.append(
                f"| {game} | {row['selection']} | {odds} ({row['sportsbook']}) | {row['model_probability']:.1%} | "
                f"{row['market_probability']:.1%} | {row['expected_value']:.1%} | {row['stake_bankroll_fraction']:.2%} |"
            )
    lines.extend(
        [
            "",
            "## Highest model-vs-market disagreements",
            "",
            "| Game | Side | Model | Market | Edge | EV | Eligible | Flags |",
            "|---|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for _, row in recommended.sort_values("probability_edge", ascending=False).head(20).iterrows():
        lines.append(
            f"| {row['away_team']} at {row['home_team']} | {row['selection']} | {row['model_probability']:.1%} | "
            f"{row['market_probability']:.1%} | {row['probability_edge']:.1%} | {row['expected_value']:.1%} | "
            f"{'yes' if row['paper_bet'] else 'no'} | {row['quality_flags'] or '—'} |"
        )
    lines.extend(
        [
            "",
            "Challengers (`market_public_ensemble`, `fpi_only`, `ratings_only`, `market_only`) are stored in the ledger but cannot trigger paper bets.",
            "",
        ]
    )
    return "\n".join(lines)


def run_snapshot(settings: Settings, odds_path: Path) -> dict[str, Any]:
    artifact = json.loads((settings.models_dir / "market_residual_latest.json").read_text(encoding="utf-8"))
    payload = json.loads(odds_path.read_text(encoding="utf-8"))
    odds = normalize_current_odds(payload, settings.allowed_books)
    schedule = pd.read_parquet(settings.raw_dir / "sportsdataverse" / f"cfb_schedule_{settings.season}.parquet")
    power = pd.read_parquet(settings.raw_dir / "sportsdataverse" / f"power_index_{settings.season}.parquet")
    ratings = pd.read_csv(settings.raw_dir / "cfbtxt" / f"ratings_preseason_{settings.season}.csv")
    games = attach_schedule(odds, schedule, power)
    games = attach_ratings(games, ratings, settings.home_field_points, artifact["market_win_model"])
    snapshot_time = utc_now()
    scored = score_candidates(games, artifact, settings, snapshot_time)
    stamp = snapshot_time.replace(":", "").replace("-", "")
    settings.snapshots_dir.mkdir(parents=True, exist_ok=True)
    scored.to_parquet(settings.snapshots_dir / f"predictions_{stamp}.parquet", index=False)
    atomic_write_json(settings.snapshots_dir / f"predictions_{stamp}.json", scored.to_dict(orient="records"))
    append_csv(settings.ledger_dir / "predictions.csv", scored, dedupe_key="prediction_id")
    report = render_latest(scored, snapshot_time, artifact)
    atomic_write_bytes(settings.reports_dir / "latest.md", (report + "\n").encode())
    return {
        "snapshot_time": snapshot_time,
        "events_with_h2h": int(len(odds)),
        "schedule_matches": int(games["schedule_match"].sum()),
        "predictions": int(len(scored)),
        "qualified_paper_bets": int(scored["paper_bet"].sum()),
        "report": str(settings.reports_dir / "latest.md"),
    }
