"""Presentation metadata for the four frozen prospective policies.

Selecting a main display does not change a policy, its eligibility, or its ledger.
"""
from __future__ import annotations

import json

PROTOCOL_URL = "https://github.com/drhyphy/ncaa-football-totals/blob/main/model/reports/PROSPECTIVE_EVALUATION_PROTOCOL.md"
POLICIES = (
    ("opponent_adjusted_ridge", "Market-anchored opponent-adjusted ridge", "primary", "totals-v4-20260908"),
    ("opponent_adjusted_structural", "Opponent-adjusted structural", "comparison", "totals-v4-20260908"),
    ("market_price_reference", "Market-price reference", "comparison", "totals-v4-20260908"),
    ("published_weather_under", "Published weather Under", "comparison", "weather-under-v1-20260908"),
)


def attach_tracking(board: dict, settings, positions: list, forecast_metrics: list) -> None:
    """Always include every active policy, even with no qualifying entries."""
    from .runtime import performance

    weather_path = settings.ledger_dir / "weather_positions.json"
    weather_positions = json.loads(weather_path.read_text()) if weather_path.exists() else []
    metrics = {(row["candidate"], row["model_version"]): row for row in forecast_metrics}
    tracking = []
    for candidate, label, role, version in POLICIES:
        ledger = weather_positions if candidate == "published_weather_under" else positions
        tracking.append({
            "candidate": candidate, "label": label, "role": role, "status": "active_paper",
            "model_version": version, "performance": performance(ledger, candidate, version),
            "forecast_performance": metrics.get((candidate, version)), "protocol_url": PROTOCOL_URL,
        })
    board["primary_model"] = {key: tracking[0][key] for key in
                              ("candidate", "label", "status", "model_version", "protocol_url")}
    board["primary_model"]["description"] = (
        "Estimates opponent-adjusted scoring, efficiency and tempo from prior completed games, "
        "then combines them with the current market. Selected as the main model for prospective evaluation."
    )
    board["candidate_tracking"] = tracking
    board["registered_evaluation"] = {
        "cohort_start": "2026-09-09T04:00:00Z", "cohort_end": "2027-02-01T04:59:59Z",
        "formal_evaluation_at": "2027-02-08T12:00:00Z", "protocol_url": PROTOCOL_URL,
        "interim_status": "descriptive_only", "report_url": "data/prospective-evaluation.json",
    }
