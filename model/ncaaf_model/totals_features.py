from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from .config import Settings


# Every value below is converted to a pregame exponentially weighted form.  The
# raw game row is always shifted first; a game's result can never enter its own
# features.
FORM_STATS = (
    "points_for",
    "points_against",
    "drives",
    "ppd_for",
    "ppd_against",
    "scrimmage_plays",
    "epa_per_play",
    "epa_allowed_per_play",
    "explosive_rate",
    "explosive_allowed",
    "yards_per_play",
    "yards_allowed_per_play",
    "pass_rate",
    "special_teams_epa",
)


def _numeric(frame: pd.DataFrame, columns: Iterable[str]) -> None:
    for column in columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")


def load_team_games(settings: Settings, seasons: Iterable[int]) -> pd.DataFrame:
    """Return one completed-game row per team with opponent-facing statistics."""
    root = settings.raw_dir / "sportsdataverse"
    pieces: list[pd.DataFrame] = []
    for season in seasons:
        adv_path = root / f"adv_team_gamelog_{season}.parquet"
        drives_path = root / f"drives_{season}.parquet"
        if not adv_path.exists() or not drives_path.exists():
            continue
        adv = pd.read_parquet(adv_path)
        drives = pd.read_parquet(drives_path)
        schedule_path = root / f"cfb_schedule_{season}.parquet"
        if schedule_path.exists():
            schedule = pd.read_parquet(schedule_path)
            final_ids = schedule.loc[schedule["status"].eq("STATUS_FINAL"), "game_id"]
            adv = adv.loc[adv["game_id"].isin(final_ids)].copy()
        adv = adv.drop_duplicates(["game_id", "team_id"], keep="last")
        drive_counts = (
            drives.loc[drives["team_id"].notna()]
            .assign(team_id=lambda x: pd.to_numeric(x["team_id"], errors="coerce"))
            .groupby(["game_id", "team_id"], as_index=False)["drive_id"]
            .nunique()
            .rename(columns={"drive_id": "drives"})
        )
        keep = [
            "season",
            "week",
            "game_id",
            "start_date",
            "team_id",
            "team",
            "opponent_id",
            "opponent",
            "is_home",
            "neutral_site",
            "points_for",
            "points_against",
            "scrimmage_plays",
            "EPA_per_play",
            "EPA_explosive_rate",
            "yards_per_play",
            "passes_rate",
            "EPA_special_teams",
        ]
        frame = adv[[column for column in keep if column in adv.columns]].copy()
        frame = frame.merge(drive_counts, on=["game_id", "team_id"], how="left")
        pieces.append(frame)
    if not pieces:
        raise FileNotFoundError("No advanced team-game and drive files are available; run totals-bootstrap")
    games = pd.concat(pieces, ignore_index=True)
    games["start_date"] = pd.to_datetime(games["start_date"], utc=True, errors="coerce")
    _numeric(
        games,
        [
            "team_id",
            "opponent_id",
            "points_for",
            "points_against",
            "drives",
            "scrimmage_plays",
            "EPA_per_play",
            "EPA_explosive_rate",
            "yards_per_play",
            "passes_rate",
            "EPA_special_teams",
        ],
    )
    opponent = games[
        ["game_id", "team_id", "drives", "EPA_per_play", "EPA_explosive_rate", "yards_per_play"]
    ].rename(
        columns={
            "team_id": "opponent_id",
            "drives": "opponent_drives",
            "EPA_per_play": "epa_allowed_per_play",
            "EPA_explosive_rate": "explosive_allowed",
            "yards_per_play": "yards_allowed_per_play",
        }
    )
    games = games.merge(opponent, on=["game_id", "opponent_id"], how="left")
    games["ppd_for"] = games["points_for"] / games["drives"].clip(lower=1)
    games["ppd_against"] = games["points_against"] / games["opponent_drives"].clip(lower=1)
    games = games.rename(
        columns={
            "EPA_per_play": "epa_per_play",
            "EPA_explosive_rate": "explosive_rate",
            "passes_rate": "pass_rate",
            "EPA_special_teams": "special_teams_epa",
        }
    )
    return games.sort_values(["start_date", "game_id", "team_id"]).reset_index(drop=True)


def add_pregame_forms(team_games: pd.DataFrame) -> pd.DataFrame:
    """Create prior-only team form, blending current season with decayed carryover."""
    frame = team_games.sort_values(["start_date", "game_id", "team_id"]).copy()
    frame["prior_games"] = frame.groupby("team_id").cumcount()
    frame["season_games"] = frame.groupby(["team_id", "season"]).cumcount()
    for stat in FORM_STATS:
        long_form = frame.groupby("team_id", sort=False)[stat].transform(
            lambda values: values.shift(1).ewm(span=8, adjust=False, min_periods=1).mean()
        )
        season_form = frame.groupby(["team_id", "season"], sort=False)[stat].transform(
            lambda values: values.shift(1).ewm(span=5, adjust=False, min_periods=1).mean()
        )
        season_weight = frame["season_games"] / (frame["season_games"] + 3.0)
        frame[f"{stat}_form"] = season_form.mul(season_weight).add(long_form.mul(1.0 - season_weight))
        frame[f"{stat}_form"] = frame[f"{stat}_form"].fillna(long_form)
    return frame


def latest_team_states(
    team_games: pd.DataFrame, season: int | None = None, as_of: str | pd.Timestamp | None = None
) -> pd.DataFrame:
    """Use the identical historical pregame transform for the next live game.

    Pass all completed games, including the current season, on each daily run.
    A synthetic future row receives the shifted historical transform, preserving
    both current-season blending and preseason carryover without duplicating it.
    """
    frame = team_games.copy()
    frame["start_date"] = pd.to_datetime(frame["start_date"], utc=True, errors="coerce")
    if as_of is not None:
        cutoff = pd.to_datetime(as_of, utc=True)
        frame = frame.loc[(frame["start_date"] + pd.Timedelta(hours=6)).le(cutoff)].copy()
    else:
        cutoff = frame["start_date"].max() + pd.Timedelta(days=1)
    season = int(season if season is not None else frame["season"].max())
    latest = frame.sort_values(["start_date", "game_id", "team_id"]).groupby("team_id", as_index=False).tail(1)
    future = latest[["team_id", "team"]].copy()
    future["season"] = season
    future["game_id"] = -1
    future["start_date"] = max(cutoff, frame["start_date"].max() + pd.Timedelta(seconds=1))
    future["_live_row"] = True
    frame["_live_row"] = False
    states = add_pregame_forms(pd.concat([frame, future], ignore_index=True))
    columns = ["team_id", "team", "prior_games", "season_games"] + [f"{stat}_form" for stat in FORM_STATS]
    result = states.loc[states["_live_row"], columns].reset_index(drop=True)
    last_dates = latest.set_index("team_id")["start_date"]
    result["last_game_date"] = result["team_id"].map(last_dates)
    result["state_season"] = season
    return result


def _attach_sides(games: pd.DataFrame, states: pd.DataFrame) -> pd.DataFrame:
    state_columns = ["prior_games", "season_games"] + [f"{stat}_form" for stat in FORM_STATS]
    home = states[["team_id"] + state_columns].rename(
        columns={"team_id": "home_id", **{column: f"home_{column}" for column in state_columns}}
    )
    away = states[["team_id"] + state_columns].rename(
        columns={"team_id": "away_id", **{column: f"away_{column}" for column in state_columns}}
    )
    return games.merge(home, on="home_id", how="left").merge(away, on="away_id", how="left")


def add_matchup_features(games: pd.DataFrame) -> pd.DataFrame:
    frame = games.copy()
    frame["market_total"] = pd.to_numeric(frame["market_total"], errors="coerce")
    frame["market_home_spread"] = pd.to_numeric(frame["market_home_spread"], errors="coerce")
    frame["abs_spread"] = frame["market_home_spread"].abs()
    frame["spread_total_interaction"] = frame["abs_spread"] / np.sqrt(frame["market_total"].clip(lower=1))
    frame["home_implied_points"] = (frame["market_total"] - frame["market_home_spread"]) / 2.0
    frame["away_implied_points"] = (frame["market_total"] + frame["market_home_spread"]) / 2.0
    frame["projected_drives"] = frame[["home_drives_form", "away_drives_form"]].mean(axis=1)
    frame["home_matchup_ppd"] = frame[["home_ppd_for_form", "away_ppd_against_form"]].mean(axis=1)
    frame["away_matchup_ppd"] = frame[["away_ppd_for_form", "home_ppd_against_form"]].mean(axis=1)
    frame["structural_total"] = (
        frame["projected_drives"] * (frame["home_matchup_ppd"] + frame["away_matchup_ppd"])
    ).clip(lower=20.0, upper=100.0)
    frame["pace_sum"] = frame["home_scrimmage_plays_form"] + frame["away_scrimmage_plays_form"]
    frame["offense_epa_sum"] = frame["home_epa_per_play_form"] + frame["away_epa_per_play_form"]
    frame["defense_epa_sum"] = frame["home_epa_allowed_per_play_form"] + frame["away_epa_allowed_per_play_form"]
    frame["explosive_matchup_sum"] = (
        frame["home_explosive_rate_form"]
        + frame["away_explosive_rate_form"]
        + frame["home_explosive_allowed_form"]
        + frame["away_explosive_allowed_form"]
    )
    return frame


def historical_game_features(settings: Settings, seasons: Iterable[int]) -> pd.DataFrame:
    seasons = list(seasons)
    root = settings.raw_dir / "sportsdataverse"
    team_forms = add_pregame_forms(load_team_games(settings, seasons))
    state_columns = ["prior_games", "season_games"] + [f"{stat}_form" for stat in FORM_STATS]
    states = team_forms[["game_id", "team_id"] + state_columns]
    pieces: list[pd.DataFrame] = []
    for season in seasons:
        schedule = pd.read_parquet(root / f"cfb_schedule_{season}.parquet")
        betting = pd.read_parquet(root / f"betting_{season}.parquet")
        # The archive uses synthetic 55.5 / +/-2.5 defaults when odds are absent.
        # Missing provenance also fails closed: an invented line is not a market.
        if "odds_source" not in betting:
            continue
        betting = betting.loc[betting["odds_source"].isin(["core_odds_api", "summary_pickcenter"])].copy()
        betting = betting.drop_duplicates(["game_id", "season", "week"], keep="last")
        games = schedule.merge(betting, on=["game_id", "season", "week"], how="inner")
        games = games.loc[
            games["status"].eq("STATUS_FINAL")
            & games["home_score"].notna()
            & games["away_score"].notna()
            & games["over_under"].notna()
        ].copy()
        games["actual_total"] = pd.to_numeric(games["home_score"]) + pd.to_numeric(games["away_score"])
        games["market_total"] = pd.to_numeric(games["over_under"], errors="coerce")
        games["market_home_spread"] = pd.to_numeric(games["home_team_spread"], errors="coerce")
        games["game_date"] = pd.to_datetime(games["game_date"], utc=True, errors="coerce")
        pieces.append(games)
    games = pd.concat(pieces, ignore_index=True)
    home = states.rename(
        columns={
            "team_id": "home_id",
            **{column: f"home_{column}" for column in state_columns},
        }
    )
    away = states.rename(
        columns={
            "team_id": "away_id",
            **{column: f"away_{column}" for column in state_columns},
        }
    )
    games = games.merge(home, on=["game_id", "home_id"], how="left").merge(
        away, on=["game_id", "away_id"], how="left"
    )
    return add_matchup_features(games).sort_values(["season", "week", "game_date"]).reset_index(drop=True)


def live_game_features(games: pd.DataFrame, team_states: pd.DataFrame) -> pd.DataFrame:
    frame = _attach_sides(games, team_states)
    return add_matchup_features(frame)


def model_feature_columns() -> list[str]:
    columns = [
        "market_total",
        "market_home_spread",
        "abs_spread",
        "spread_total_interaction",
        "home_implied_points",
        "away_implied_points",
        "week",
        "neutral_site",
        "home_prior_games",
        "away_prior_games",
        "home_season_games",
        "away_season_games",
        "structural_total",
        "projected_drives",
        "home_matchup_ppd",
        "away_matchup_ppd",
        "pace_sum",
        "offense_epa_sum",
        "defense_epa_sum",
        "explosive_matchup_sum",
    ]
    columns.extend(f"{side}_{stat}_form" for side in ("home", "away") for stat in FORM_STATS)
    return columns
