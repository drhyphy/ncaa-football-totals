from __future__ import annotations

import math
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .config import Settings
from .storage import atomic_write_bytes, atomic_write_csv, atomic_write_json, utc_now


EASTERN = ZoneInfo("America/New_York")
HORIZON_ORDER = ["D8+", "D7", "D6", "D5", "D4", "D3", "D2", "D1", "D0"]
HORIZON_WINDOWS = {
    "D8+": "192+ hours",
    "D7": "168–192 hours",
    "D6": "144–168 hours",
    "D5": "120–144 hours",
    "D4": "96–120 hours",
    "D3": "72–96 hours",
    "D2": "48–72 hours",
    "D1": "24–48 hours",
    "D0": "0–24 hours",
}


def timing_bucket(hours_to_kickoff: float) -> str:
    hours = float(hours_to_kickoff)
    if not math.isfinite(hours) or hours < 0:
        return "started"
    day = int(hours // 24)
    return "D8+" if day >= 8 else f"D{day}"


def _as_bool(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.strip().str.lower().isin({"1", "true", "yes"})


def add_timing_columns(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    output["snapshot_dt"] = pd.to_datetime(output["snapshot_time"], utc=True)
    output["kickoff_dt"] = pd.to_datetime(output["commence_time"], utc=True)
    derived_hours = (output["kickoff_dt"] - output["snapshot_dt"]).dt.total_seconds() / 3600.0
    output["hours_to_kickoff"] = derived_hours
    output["timing_bucket"] = derived_hours.map(timing_bucket)
    output["snapshot_date_et"] = output["snapshot_dt"].dt.tz_convert(EASTERN).dt.strftime("%Y-%m-%d")
    output["kickoff_date_et"] = output["kickoff_dt"].dt.tz_convert(EASTERN).dt.strftime("%Y-%m-%d")
    return output


def prepare_timing_dataset(predictions: pd.DataFrame, grades: pd.DataFrame | None = None) -> pd.DataFrame:
    frame = add_timing_columns(predictions)
    frame = frame.loc[
        frame["candidate"].eq("market_fpi_residual")
        & frame["timing_bucket"].isin(HORIZON_ORDER)
        & frame["espn_game_id"].notna()
    ].copy()
    if "model_artifact_created_at" in frame and frame["model_artifact_created_at"].notna().any():
        latest_row = frame.sort_values("snapshot_dt").iloc[-1]
        latest_artifact = latest_row.get("model_artifact_created_at")
        if pd.notna(latest_artifact):
            frame = frame.loc[frame["model_artifact_created_at"].eq(latest_artifact)].copy()
    frame["paper_bet"] = _as_bool(frame["paper_bet"])
    frame = frame.sort_values("snapshot_dt").drop_duplicates(
        ["espn_game_id", "side", "timing_bucket"], keep="last"
    )
    if grades is not None and not grades.empty:
        grade_columns = [
            "prediction_id",
            "result",
            "flat_profit_units",
            "price_clv",
            "probability_clv",
            "home_score",
            "away_score",
        ]
        available = [column for column in grade_columns if column in grades.columns]
        frame = frame.merge(grades[available], on="prediction_id", how="left")
    return frame.reset_index(drop=True)


def _binary_log_loss(outcomes: pd.Series, probabilities: pd.Series) -> float:
    p = probabilities.astype(float).clip(0.005, 0.995)
    y = outcomes.astype(float)
    return float(-(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)).mean())


def summarize_timing(frame: pd.DataFrame, minimum_bets: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bucket in HORIZON_ORDER:
        group = frame.loc[frame["timing_bucket"].eq(bucket)]
        home = group.loc[group["side"].eq("home")].copy()
        if "result" in home:
            home_graded = home.loc[home["result"].isin(["win", "loss"])].copy()
        else:
            home_graded = home.iloc[0:0].copy()
        if not home_graded.empty:
            home_graded["outcome"] = home_graded["result"].eq("win").astype(float)
            brier = float(((home_graded["model_probability"] - home_graded["outcome"]) ** 2).mean())
            log_loss = _binary_log_loss(home_graded["outcome"], home_graded["model_probability"])
            calibration_gap = float(home_graded["model_probability"].mean() - home_graded["outcome"].mean())
        else:
            brier = log_loss = calibration_gap = np.nan
        bets = group.loc[group["paper_bet"]].copy()
        if "result" in bets:
            graded_bets = bets.loc[bets["result"].isin(["win", "loss", "push"])].copy()
        else:
            graded_bets = bets.iloc[0:0].copy()
        n_graded_bets = len(graded_bets)
        wins = int(graded_bets["result"].eq("win").sum()) if n_graded_bets else 0
        row = {
            "timing_bucket": bucket,
            "window": HORIZON_WINDOWS[bucket],
            "captured_games": int(home["espn_game_id"].nunique()),
            "graded_game_predictions": int(len(home_graded)),
            "brier": brier,
            "log_loss": log_loss,
            "calibration_gap": calibration_gap,
            "signals": int(len(bets)),
            "graded_signals": int(n_graded_bets),
            "wins": wins,
            "hit_rate": wins / n_graded_bets if n_graded_bets else np.nan,
            "flat_profit_units": float(graded_bets["flat_profit_units"].sum()) if n_graded_bets else np.nan,
            "flat_roi": float(graded_bets["flat_profit_units"].sum() / n_graded_bets) if n_graded_bets else np.nan,
            "mean_price_clv": float(graded_bets["price_clv"].mean()) if n_graded_bets else np.nan,
            "mean_probability_clv": float(graded_bets["probability_clv"].mean()) if n_graded_bets else np.nan,
            "mean_entry_ev": float(bets["expected_value"].mean()) if len(bets) else np.nan,
            "enough_for_comparison": n_graded_bets >= minimum_bets,
        }
        rows.append(row)
    summary = pd.DataFrame(rows)
    eligible = summary.loc[summary["enough_for_comparison"] & summary["mean_price_clv"].notna()]
    if eligible.empty:
        conclusion = {
            "status": "collecting",
            "recommended_timing_bucket": None,
            "reason": f"No timing bucket has {minimum_bets} graded signals yet.",
        }
    else:
        best = eligible.sort_values(["mean_price_clv", "flat_roi"], ascending=False).iloc[0]
        conclusion = {
            "status": "provisional",
            "recommended_timing_bucket": best["timing_bucket"],
            "reason": "Highest mean price CLV among sufficiently sampled buckets; ROI is a secondary tiebreaker.",
        }
    return summary, conclusion


def build_lifecycle(frame: pd.DataFrame) -> pd.DataFrame:
    bets = frame.loc[frame["paper_bet"]].sort_values("snapshot_dt")
    rows: list[dict[str, Any]] = []
    for game_id, group in frame.groupby("espn_game_id", sort=False):
        game_bets = bets.loc[bets["espn_game_id"].eq(game_id)]
        ordered = game_bets.sort_values("snapshot_dt")
        sides = ordered["side"].tolist()
        rows.append(
            {
                "espn_game_id": int(float(game_id)),
                "away_team": group.iloc[-1]["away_team"],
                "home_team": group.iloc[-1]["home_team"],
                "snapshots": int(group["snapshot_time"].nunique()),
                "signal_snapshots": int(len(ordered)),
                "first_signal_bucket": ordered.iloc[0]["timing_bucket"] if len(ordered) else None,
                "last_signal_bucket": ordered.iloc[-1]["timing_bucket"] if len(ordered) else None,
                "first_signal_side": ordered.iloc[0]["side"] if len(ordered) else None,
                "last_signal_side": ordered.iloc[-1]["side"] if len(ordered) else None,
                "signal_flips": int(sum(left != right for left, right in zip(sides, sides[1:]))),
                "best_observed_decimal": float(ordered["decimal_odds"].max()) if len(ordered) else np.nan,
                "best_observed_ev": float(ordered["expected_value"].max()) if len(ordered) else np.nan,
            }
        )
    return pd.DataFrame(rows)


def build_pairwise_price_comparison(frame: pd.DataFrame) -> pd.DataFrame:
    bets = frame.loc[frame["paper_bet"]].copy()
    rows: list[dict[str, Any]] = []
    for earlier_index, earlier_bucket in enumerate(HORIZON_ORDER[:-1]):
        earlier = bets.loc[bets["timing_bucket"].eq(earlier_bucket)]
        for later_bucket in HORIZON_ORDER[earlier_index + 1 :]:
            later = bets.loc[bets["timing_bucket"].eq(later_bucket)]
            paired = earlier.merge(
                later,
                on=["espn_game_id", "side"],
                suffixes=("_earlier", "_later"),
            )
            if paired.empty:
                rows.append(
                    {
                        "earlier_bucket": earlier_bucket,
                        "later_bucket": later_bucket,
                        "persistent_signals": 0,
                        "mean_earlier_price_advantage": np.nan,
                        "earlier_price_better_rate": np.nan,
                        "mean_ev_change_later_minus_earlier": np.nan,
                    }
                )
                continue
            price_advantage = paired["decimal_odds_earlier"] / paired["decimal_odds_later"] - 1.0
            rows.append(
                {
                    "earlier_bucket": earlier_bucket,
                    "later_bucket": later_bucket,
                    "persistent_signals": int(len(paired)),
                    "mean_earlier_price_advantage": float(price_advantage.mean()),
                    "earlier_price_better_rate": float((price_advantage > 0).mean()),
                    "mean_ev_change_later_minus_earlier": float(
                        (paired["expected_value_later"] - paired["expected_value_earlier"]).mean()
                    ),
                }
            )
    return pd.DataFrame(rows)


def _fmt(value: Any, kind: str = "number") -> str:
    if value is None or pd.isna(value):
        return "—"
    if kind == "pct":
        return f"{float(value):.1%}"
    if kind == "decimal":
        return f"{float(value):.4f}"
    return str(value)


def render_timing_report(
    summary: pd.DataFrame, pairwise: pd.DataFrame, conclusion: dict[str, Any], generated_at: str
) -> str:
    lines = [
        "# NCAA football moneyline timing report",
        "",
        f"Generated: {generated_at}",
        "",
        "Each bucket represents a separate hypothetical entry strategy. Duplicate same-game snapshots inside a bucket are reduced to the latest snapshot. Price CLV is the primary timing metric; ROI and hit rate are secondary.",
        "",
        f"Status: **{conclusion['status']}** — {conclusion['reason']}",
        "",
        "| Horizon | Window | Games captured | Signals | Graded signals | Brier | ROI | Price CLV | Prob. CLV |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in summary.iterrows():
        lines.append(
            f"| {row['timing_bucket']} | {row['window']} | {int(row['captured_games'])} | {int(row['signals'])} | "
            f"{int(row['graded_signals'])} | {_fmt(row['brier'], 'decimal')} | {_fmt(row['flat_roi'], 'pct')} | "
            f"{_fmt(row['mean_price_clv'], 'pct')} | {_fmt(row['mean_probability_clv'], 'pct')} |"
        )
    adjacent = pairwise.loc[
        pairwise.apply(
            lambda row: HORIZON_ORDER.index(row["later_bucket"])
            == HORIZON_ORDER.index(row["earlier_bucket"]) + 1,
            axis=1,
        )
    ]
    lines.extend(
        [
            "",
            "## Persistent-signal price drift",
            "",
            "This matched comparison uses only games where the same side remained qualified in both adjacent horizons.",
            "",
            "| Earlier | Later | Persistent signals | Earlier price advantage | Earlier price better | Later EV change |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for _, row in adjacent.iterrows():
        lines.append(
            f"| {row['earlier_bucket']} | {row['later_bucket']} | {int(row['persistent_signals'])} | "
            f"{_fmt(row['mean_earlier_price_advantage'], 'pct')} | {_fmt(row['earlier_price_better_rate'], 'pct')} | "
            f"{_fmt(row['mean_ev_change_later_minus_earlier'], 'pct')} |"
        )
    lines.extend(
        [
            "",
            "Interpretation rules:",
            "",
            "- Do not declare an optimal window until a bucket reaches the configured minimum graded-signal count.",
            "- Prefer positive price CLV that persists across conferences, favorite/underdog bands, and weeks.",
            "- Compare timing buckets on the same model version and deduplicate to one hypothetical bet per game per bucket.",
            "- A later recommendation is not automatically better: it may have more accurate information but a worse price.",
            "",
        ]
    )
    return "\n".join(lines)


def run_timing_report(settings: Settings) -> dict[str, Any]:
    predictions_path = settings.ledger_dir / "predictions.csv"
    if not predictions_path.exists():
        return {"status": "unavailable", "message": "prediction ledger does not exist"}
    predictions = pd.read_csv(predictions_path)
    grades_path = settings.ledger_dir / "grades.csv"
    grades = pd.read_csv(grades_path) if grades_path.exists() else None
    frame = prepare_timing_dataset(predictions, grades)
    summary, conclusion = summarize_timing(frame, settings.timing_min_graded_signals)
    lifecycle = build_lifecycle(frame)
    pairwise = build_pairwise_price_comparison(frame)
    generated_at = utc_now()
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_csv(settings.reports_dir / "timing_by_horizon.csv", summary)
    atomic_write_csv(settings.reports_dir / "timing_game_lifecycle.csv", lifecycle)
    atomic_write_csv(settings.reports_dir / "timing_pairwise_prices.csv", pairwise)
    atomic_write_json(
        settings.reports_dir / "timing_summary.json",
        {
            "generated_at": generated_at,
            "conclusion": conclusion,
            "minimum_graded_signals": settings.timing_min_graded_signals,
            "model_artifact_created_at": (
                frame["model_artifact_created_at"].dropna().iloc[-1]
                if "model_artifact_created_at" in frame and frame["model_artifact_created_at"].notna().any()
                else None
            ),
            "buckets": summary.to_dict(orient="records"),
        },
    )
    report = render_timing_report(summary, pairwise, conclusion, generated_at)
    atomic_write_bytes(settings.reports_dir / "timing_report.md", (report + "\n").encode())
    return {
        "status": conclusion["status"],
        "recommended_timing_bucket": conclusion["recommended_timing_bucket"],
        "ledger_rows_analyzed": int(len(frame)),
        "games_analyzed": int(frame["espn_game_id"].nunique()),
        "report": str(settings.reports_dir / "timing_report.md"),
    }
