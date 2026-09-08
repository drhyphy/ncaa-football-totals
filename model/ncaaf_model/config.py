from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Settings:
    root: Path
    season: int
    historical_start_season: int
    historical_full_coverage_start: int
    home_field_points: float
    rating_ensemble_game_weight: float
    live_alpha_shrink: float
    alpha_grid_step: float
    min_books: int
    min_probability_edge: float
    min_ev: float
    max_consensus_dispersion: float
    fractional_kelly: float
    max_bankroll_fraction: float
    timing_min_graded_signals: int
    totals_min_edge_points: float
    totals_min_line_value_points: float
    totals_min_ev: float
    totals_max_line_dispersion: float
    totals_min_team_history: int
    totals_fractional_kelly: float
    totals_max_bankroll_fraction: float
    totals_student_df: int
    totals_ridge_weight: float
    totals_hgb_weight: float
    allowed_books: tuple[str, ...]
    data_urls: dict[str, str]

    @property
    def raw_dir(self) -> Path:
        return self.root / "data" / "raw"

    @property
    def normalized_dir(self) -> Path:
        return self.root / "data" / "normalized"

    @property
    def models_dir(self) -> Path:
        return self.root / "data" / "models"

    @property
    def snapshots_dir(self) -> Path:
        return self.root / "data" / "snapshots"

    @property
    def ledger_dir(self) -> Path:
        return self.root / "ledger"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"


def load_settings(path: Path | None = None) -> Settings:
    root = Path(__file__).resolve().parents[1]
    config_path = path or root / "config" / "default.yaml"
    payload: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    return Settings(
        root=root,
        season=int(payload["season"]),
        historical_start_season=int(payload["historical_start_season"]),
        historical_full_coverage_start=int(payload["historical_full_coverage_start"]),
        home_field_points=float(payload["home_field_points"]),
        rating_ensemble_game_weight=float(payload["rating_ensemble_game_weight"]),
        live_alpha_shrink=float(payload["live_alpha_shrink"]),
        alpha_grid_step=float(payload["alpha_grid_step"]),
        min_books=int(payload["min_books"]),
        min_probability_edge=float(payload["min_probability_edge"]),
        min_ev=float(payload["min_ev"]),
        max_consensus_dispersion=float(payload["max_consensus_dispersion"]),
        fractional_kelly=float(payload["fractional_kelly"]),
        max_bankroll_fraction=float(payload["max_bankroll_fraction"]),
        timing_min_graded_signals=int(payload["timing_min_graded_signals"]),
        totals_min_edge_points=float(payload["totals_min_edge_points"]),
        totals_min_line_value_points=float(payload["totals_min_line_value_points"]),
        totals_min_ev=float(payload["totals_min_ev"]),
        totals_max_line_dispersion=float(payload["totals_max_line_dispersion"]),
        totals_min_team_history=int(payload["totals_min_team_history"]),
        totals_fractional_kelly=float(payload["totals_fractional_kelly"]),
        totals_max_bankroll_fraction=float(payload["totals_max_bankroll_fraction"]),
        totals_student_df=int(payload["totals_student_df"]),
        totals_ridge_weight=float(payload["totals_ridge_weight"]),
        totals_hgb_weight=float(payload["totals_hgb_weight"]),
        allowed_books=tuple(payload["allowed_books"]),
        data_urls=dict(payload["data_urls"]),
    )
