from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from .config import Settings


FPI_COLUMNS = (
    "fpi",
    "epaoffense",
    "epadefense",
    "epaspecialteams",
    "offefficiency",
    "defefficiency",
    "stefficiency",
    "totefficiency",
)
RATING_COLUMNS = (
    "adj_off_epa",
    "adj_def_epa",
    "adj_st_epa",
    "adj_net",
    "fei_off",
    "fei_def",
    "fei_net",
    "off_pace",
    "net_z",
    "games",
)
SUMMARY_COLUMNS = (
    "EPAplay_off",
    "EPAplay_def",
    "EPAdrive_off",
    "EPAdrive_def",
    "success_off",
    "success_def",
    "explosive_off",
    "explosive_def",
    "nonExplosiveEpaPerPlay_off",
    "nonExplosiveEpaPerPlay_def",
    "playsgame_off",
    "playsgame_def",
    "drivesgame_off",
    "drivesgame_def",
    "yardsplay_off",
    "yardsplay_def",
    "passrate_off",
    "passrate_def",
    "havoc_off",
    "havoc_def",
    "red_zone_success_off",
    "red_zone_success_def",
)
ROSTER_COLUMNS = (
    "off_returning",
    "def_returning",
    "overall_returning",
    "n_returning",
    "talent_composite",
    "talent_rank",
    "blue_chip_ratio",
    "n_recruits",
)
PRIOR_COLUMNS = (
    "adj_off_epa",
    "adj_def_epa",
    "adj_st_epa",
    "fei_off",
    "fei_def",
    "off_pace",
    "net_z",
)


def _read_existing(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path) if path.exists() else pd.DataFrame()


def _normalize_team_id(frame: pd.DataFrame) -> pd.DataFrame:
    """SportsDataverse files mix numeric and string ESPN team identifiers."""
    output = frame.copy()
    output["team_id"] = pd.to_numeric(output["team_id"], errors="coerce").astype("Int64")
    return output.loc[output["team_id"].notna()].copy()


def load_public_season(settings: Settings, season: int) -> pd.DataFrame:
    """One point-in-time public-model row per team and completed week."""
    root = settings.raw_dir / "sportsdataverse"
    weekly: pd.DataFrame | None = None

    fpi = _read_existing(root / f"fpi_weekly_{season}.parquet")
    if not fpi.empty:
        fpi = _normalize_team_id(fpi)
        if "snapshot_is_contemporaneous" in fpi:
            fpi = fpi.loc[fpi["snapshot_is_contemporaneous"].fillna(False)].copy()
        # The upstream contemporaneous flag means computed *within the season*,
        # not before this week. ESPN can overwrite week 1 with December values.
        if "snapshot_out_of_sequence" in fpi:
            fpi = fpi.loc[~fpi["snapshot_out_of_sequence"].fillna(True)].copy()
        fpi["fpi_available_at"] = pd.to_datetime(fpi.get("last_updated"), utc=True, errors="coerce")
        fpi = fpi.loc[fpi["fpi_available_at"].notna()].copy()
        fpi["run_date_time_key"] = pd.to_numeric(fpi["run_date_time_key"], errors="coerce")
        fpi = fpi.sort_values("run_date_time_key").drop_duplicates(["team_id", "week"], keep="first")
        fpi = fpi[["team_id", "week", "fpi_available_at"] + [column for column in FPI_COLUMNS if column in fpi]].rename(
            columns={"week": "snapshot_week", **{column: f"fpi_{column}" for column in FPI_COLUMNS}}
        )
        weekly = fpi

    ratings = _read_existing(root / f"ratings_weekly_{season}.parquet")
    if not ratings.empty:
        ratings = _normalize_team_id(ratings)
        ratings = ratings[
            ["team_id", "through_week"] + [column for column in RATING_COLUMNS if column in ratings]
        ].rename(
            columns={
                "through_week": "snapshot_week",
                **{column: f"rating_{column}" for column in RATING_COLUMNS},
            }
        )
        weekly = ratings if weekly is None else weekly.merge(ratings, on=["team_id", "snapshot_week"], how="outer")

    summaries = _read_existing(root / f"summaries_weekly_{season}.parquet")
    if not summaries.empty:
        summaries = _normalize_team_id(summaries)
        summaries = summaries[
            ["team_id", "through_week"] + [column for column in SUMMARY_COLUMNS if column in summaries]
        ].rename(
            columns={
                "through_week": "snapshot_week",
                **{column: f"summary_{column}" for column in SUMMARY_COLUMNS},
            }
        )
        weekly = summaries if weekly is None else weekly.merge(
            summaries, on=["team_id", "snapshot_week"], how="outer"
        )

    if weekly is None:
        weekly = pd.DataFrame(columns=["team_id", "snapshot_week"])
    weekly["season"] = season

    # These reconstructed annual roster products have no preseason publication
    # timestamps. They are retained in raw storage but excluded from all scored
    # candidates until a contemporaneous roster source can be demonstrated.
    returning = pd.DataFrame()
    talent = pd.DataFrame()
    roster: pd.DataFrame | None = None
    if not returning.empty:
        returning = _normalize_team_id(returning)
        roster = returning[["team_id"] + [column for column in ROSTER_COLUMNS if column in returning]].copy()
    if not talent.empty:
        talent = _normalize_team_id(talent)
        talent = talent[["team_id"] + [column for column in ROSTER_COLUMNS if column in talent]].copy()
        roster = talent if roster is None else roster.merge(talent, on="team_id", how="outer")
    if roster is not None:
        roster = roster.rename(columns={column: f"roster_{column}" for column in ROSTER_COLUMNS})
        weekly = weekly.merge(roster, on="team_id", how="outer")

    prior = _read_existing(root / f"ratings_final_{season - 1}.parquet")
    if not prior.empty:
        prior = _normalize_team_id(prior)
        prior = prior[["team_id"] + [column for column in PRIOR_COLUMNS if column in prior]].rename(
            columns={column: f"prior_{column}" for column in PRIOR_COLUMNS}
        )
        weekly = weekly.merge(prior, on="team_id", how="outer")
    return weekly


def public_feature_columns() -> list[str]:
    columns = [f"fpi_{column}" for column in FPI_COLUMNS]
    columns += [f"rating_{column}" for column in RATING_COLUMNS]
    columns += [f"summary_{column}" for column in SUMMARY_COLUMNS]
    columns += [f"roster_{column}" for column in ROSTER_COLUMNS]
    columns += [f"prior_{column}" for column in PRIOR_COLUMNS]
    return columns


def family_feature_columns() -> dict[str, list[str]]:
    return {
        "fpi": [f"{side}_fpi_{column}" for side in ("home", "away") for column in FPI_COLUMNS],
        "fei_epa": [
            f"{side}_rating_{column}" for side in ("home", "away") for column in RATING_COLUMNS
        ],
        "summary": [
            f"{side}_summary_{column}" for side in ("home", "away") for column in SUMMARY_COLUMNS
        ],
        "roster_prior": [
            f"{side}_{column}"
            for side in ("home", "away")
            for column in ([f"roster_{value}" for value in ROSTER_COLUMNS] + [f"prior_{value}" for value in PRIOR_COLUMNS])
        ],
    }


def attach_public_features(
    games: pd.DataFrame,
    settings: Settings,
    seasons: Iterable[int],
    live_latest_week: int | None = None,
    as_of: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Attach the last completed-week snapshot; current-game data is never eligible."""
    pieces: list[pd.DataFrame] = []
    for season in seasons:
        sample = games.loc[pd.to_numeric(games["season"]).eq(season)].copy()
        if sample.empty:
            continue
        sample["snapshot_week"] = pd.to_numeric(sample["week"], errors="coerce").fillna(1).astype(int) - 1
        if live_latest_week is not None:
            sample["snapshot_week"] = sample["snapshot_week"].clip(upper=live_latest_week)
        public = load_public_season(settings, season)
        values = public_feature_columns()
        metadata = ["fpi_available_at"] if "fpi_available_at" in public else []
        home = public[["team_id", "snapshot_week"] + metadata + [column for column in values if column in public]].rename(
            columns={"team_id": "home_id", **{column: f"home_{column}" for column in values + metadata}}
        )
        away = public[["team_id", "snapshot_week"] + metadata + [column for column in values if column in public]].rename(
            columns={"team_id": "away_id", **{column: f"away_{column}" for column in values + metadata}}
        )
        sample = sample.merge(home, on=["home_id", "snapshot_week"], how="left").merge(
            away, on=["away_id", "snapshot_week"], how="left"
        )
        kickoff = sample.get("game_date", sample.get("commence_time", pd.Series(pd.NaT, index=sample.index)))
        cutoff = pd.to_datetime(kickoff, utc=True, errors="coerce")
        if as_of is not None or live_latest_week is not None:
            observed_at = pd.Timestamp(as_of) if as_of is not None else pd.Timestamp.now(tz="UTC")
            observed_at = observed_at.tz_localize("UTC") if observed_at.tzinfo is None else observed_at.tz_convert("UTC")
            cutoff = cutoff.fillna(observed_at).clip(upper=observed_at)
        for side in ("home", "away"):
            available = pd.to_datetime(sample.get(f"{side}_fpi_available_at", pd.Series(pd.NaT, index=sample.index)), utc=True, errors="coerce")
            invalid = available.isna() | cutoff.isna() | available.ge(cutoff)
            fpi_columns = [f"{side}_fpi_{column}" for column in FPI_COLUMNS if f"{side}_fpi_{column}" in sample]
            sample.loc[invalid, fpi_columns] = np.nan
            sample[f"{side}_fpi_timestamp_rejected"] = invalid
        # Static roster and prior-season fields can exist on outer-merge rows
        # without a weekly snapshot. Fill them by team from any season row.
        for side, team_id in (("home", "home_id"), ("away", "away_id")):
            static_columns = [
                column
                for column in values
                if column.startswith("roster_") or column.startswith("prior_")
            ]
            static = public[["team_id"] + [column for column in static_columns if column in public]].drop_duplicates(
                "team_id"
            )
            static = static.rename(
                columns={"team_id": team_id, **{column: f"{side}_{column}_static" for column in static_columns}}
            )
            sample = sample.merge(static, on=team_id, how="left")
            for column in static_columns:
                target = f"{side}_{column}"
                backup = f"{target}_static"
                if backup in sample:
                    sample[target] = sample.get(target, np.nan)
                    sample[target] = sample[target].fillna(sample[backup])
                    sample = sample.drop(columns=backup)
        pieces.append(sample)
    output = pd.concat(pieces, ignore_index=True) if pieces else games.copy()
    families = family_feature_columns()
    for family, columns in families.items():
        existing = [column for column in columns if column in output]
        output[f"coverage_{family}"] = output[existing].notna().sum(axis=1) / len(columns) if existing else 0.0
    return output
