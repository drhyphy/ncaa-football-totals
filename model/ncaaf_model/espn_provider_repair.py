"""Repair archived market provenance using a fixed non-live bookmaker policy.

No opening values or outcome-based plausibility rules are used as fallbacks.
Non-live provider role is verified; exact closing/publication time is not.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import threading
import time

import pandas as pd
import requests

PROVIDER_POLICY = (
    ("58", "espn bet"), ("100", "draftkings"), ("40", "draftkings"),
    ("47", "mgm"), ("45", "caesars sportsbook (new jersey)"),
    ("52", "caesars sportsbook (colorado)"), ("57", "caesars sportsbook (tennessee)"),
    ("48", "pointsbet"), ("41", "sugarhouse"), ("53", "titanbets"), ("36", "unibet"),
)
ROLE_LABEL = "pregame-provider archived line, close-time unverified"
_LOCAL = threading.local()


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def select_provider(payload: dict) -> tuple[dict | None, str]:
    items = payload.get("items", [])
    if not isinstance(items, list):
        return None, "malformed_items"
    for provider_id, expected_name in PROVIDER_POLICY:
        for item in items:
            if not isinstance(item, dict):
                continue
            provider = item.get("provider") or {}
            name = str(provider.get("name", "")).strip().lower()
            if str(provider.get("id")) != provider_id or name != expected_name or "live" in name:
                continue
            total = number(item.get("overUnder"))
            if total is not None and total > 0:
                return item, "verified_nonlive_provider"
    if items and all(isinstance(item, dict) and (str((item.get("provider") or {}).get("id")) == "59" or "live" in str((item.get("provider") or {}).get("name", "")).lower()) for item in items):
        return None, "live_provider_only"
    return None, "no_approved_nonlive_total"


def load_games(root: Path) -> pd.DataFrame:
    pieces = []
    for season in [2023, 2024, 2025]:
        raw = root / "data/raw/sportsdataverse"
        schedule = pd.read_parquet(raw / f"cfb_schedule_{season}.parquet")
        archive = pd.read_parquet(raw / f"betting_{season}.parquet")
        archive = archive.loc[archive.odds_source.isin(["core_odds_api", "summary_pickcenter"])].drop_duplicates(["game_id", "season", "week"], keep="last")
        games = schedule.merge(archive, on=["game_id", "season", "week"], validate="one_to_one")
        games = games.loc[games.status.eq("STATUS_FINAL") & games.home_score.notna() & games.away_score.notna() & games.over_under.notna()].copy()
        pieces.append(games)
    return pd.concat(pieces, ignore_index=True).sort_values(["season", "week", "game_id"]).reset_index(drop=True)


def fetch_game(game_id: int, cache: Path) -> dict:
    cached = cache / f"{game_id}.json.gz"
    if cached.exists():
        try:
            with gzip.open(cached, "rt") as stream:
                return json.load(stream)
        except (OSError, json.JSONDecodeError):
            pass
    url = f"https://sports.core.api.espn.com/v2/sports/football/leagues/college-football/events/{game_id}/competitions/{game_id}/odds?limit=100"
    if not hasattr(_LOCAL, "session"):
        _LOCAL.session = requests.Session()
        _LOCAL.session.headers["User-Agent"] = "NCAAF-public-market-provenance-audit/1.0"
    failure = None
    for attempt in range(2):
        try:
            response = _LOCAL.session.get(url, timeout=10)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Top-level payload is not an object")
            result = {"game_id": game_id, "source_url": url, "observed_at": datetime.now(timezone.utc).isoformat(),
                      "response_sha256": hashlib.sha256(response.content).hexdigest(), "payload": payload}
            temporary = cached.with_suffix(".json.gz.tmp")
            with gzip.open(temporary, "wt") as stream:
                json.dump(result, stream, separators=(",", ":"))
            temporary.replace(cached)
            return result
        except (requests.RequestException, ValueError) as error:
            failure = type(error).__name__
            if attempt == 0:
                time.sleep(.5)
    return {"game_id": game_id, "source_url": url, "observed_at": datetime.now(timezone.utc).isoformat(), "error": failure}


def normalized_row(game: dict, response: dict, selected: dict) -> dict:
    provider = selected["provider"]
    scalar_spread = number(selected.get("spread"))
    home = selected.get("homeTeamOdds") or {}
    explicit = number(((home.get("current") or {}).get("pointSpread") or {}).get("american"))
    if explicit is not None:
        spread = explicit
    elif scalar_spread is not None and isinstance(home.get("favorite"), bool):
        spread = -abs(scalar_spread) if home["favorite"] else abs(scalar_spread)
    else:
        spread = scalar_spread
    row = {key: game.get(key) for key in ["game_id", "season", "week", "season_type", "game_date", "neutral_site", "conference_competition", "home_id", "away_id", "home_team", "away_team", "home_score", "away_score", "status"]}
    row.update({"date": game["game_date"], "start_date": game["game_date"], "actual_total": float(game["home_score"] + game["away_score"]),
                "market_total": float(selected["overUnder"]), "spread": spread, "market_home_spread": spread,
                "provider_id": str(provider["id"]), "provider_name": provider["name"],
                "market_source": f"espn_nonlive_provider_{provider['id']}", "market_label": ROLE_LABEL,
                "source_url": response["source_url"], "observed_at": response["observed_at"], "quote_timestamp": None,
                "role_verified": True, "closing_time_verified": False, "original_archive_total": float(game["over_under"]),
                "original_archive_spread": number(game.get("home_team_spread")), "response_sha256": response["response_sha256"]})
    return row


def run(root: Path, workers: int = 6, limit: int | None = None) -> dict:
    games = load_games(root)
    if limit:
        games = games.iloc[:limit]
    cache = root / "data/raw/alternative/espn_provider_cache"
    cache.mkdir(parents=True, exist_ok=True)
    records, rejected, samples, completed = [], [], [], 0
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=min(max(workers, 1), 6)) as pool:
        futures = {pool.submit(fetch_game, int(game["game_id"]), cache): game for game in games.to_dict("records")}
        for future in as_completed(futures):
            game = futures[future]
            response = future.result()
            if response.get("error"):
                selected, reason = None, "request_failed_" + response["error"]
            else:
                selected, reason = select_provider(response["payload"])
            if selected:
                records.append(normalized_row(game, response, selected))
            else:
                rejected.append({"game_id": int(game["game_id"]), "season": int(game["season"]), "matchup": f"{game['away_team']} at {game['home_team']}",
                                 "original_total":float(game["over_under"]),"reason": reason, "source_url": response["source_url"],
                                 "providers": [{"id": (item.get("provider") or {}).get("id"), "name": (item.get("provider") or {}).get("name"), "total":item.get("overUnder")} for item in response.get("payload",{}).get("items",[]) if isinstance(item,dict)]})
            completed += 1
            if completed % 100 == 0 or completed == len(games):
                print(f"{completed}/{len(games)} inspected; {len(records)} non-live, {len(rejected)} excluded; {time.monotonic()-started:.1f}s", flush=True)
    frame = pd.DataFrame(records).sort_values(["season", "week", "game_id"])
    output = root / "data/raw/alternative/espn_verified_pregame_games.parquet"
    frame.to_parquet(output, index=False)
    source_counts = Counter(str(row["provider_name"]) for row in records)
    coverage = []
    for season, original in games.groupby("season"):
        selected = frame.loc[frame.season.eq(season)]
        misses = [row for row in rejected if row["season"] == season]
        coverage.append({"season": int(season), "original_games":len(original), "verified_provider_games":len(selected), "excluded_games":len(misses),
                         "exclusion_reasons": dict(Counter(row["reason"] for row in misses)),
                         "changed_total_count":int(selected.market_total.ne(selected.original_archive_total).sum()),
                         "mean_absolute_total_difference":float((selected.market_total-selected.original_archive_total).abs().mean()),
                         "integer_total_count":int(selected.market_total.mod(1).eq(0).sum())})
    changed = frame.loc[frame.market_total.ne(frame.original_archive_total)]
    report = {"version":"espn-nonlive-provider-repair-v1","label":ROLE_LABEL,"generated_at":datetime.now(timezone.utc).isoformat(),
              "policy":list(PROVIDER_POLICY), "input_games":len(games), "verified_provider_games":len(frame), "excluded_games":len(rejected),
              "coverage":coverage,"provider_counts":dict(source_counts),"changed_totals":changed[["game_id","season","home_team","away_team","original_archive_total","market_total","provider_id"]].to_dict("records"),
              "excluded":rejected,"artifact":str(output.relative_to(root)),"artifact_sha256":hashlib.sha256(output.read_bytes()).hexdigest(),
              "limitations":["Role verified by fixed known non-live bookmaker IDs and exact names; provider59 and any live-named provider excluded.",
                             "No historical quote timestamps or evidence of an exact closing or06:30AM price. Observation time is the audit retrieval time.",
                             "Opening totals are never substituted. Selection uses provider role and availability, never outcomes or model performance.",
                             "Retained and excluded populations can differ. Compare clean-source evaluation to its own population; old contaminated results are not repaired retroactively."]}
    out = root / "reports"
    (out / "espn_verified_provider_repair.json").write_text(json.dumps(report,indent=2,allow_nan=False)+"\n")
    lines=["# Historical ESPN provider repair", "", f"**{len(frame):,} of {len(games):,} games retained.** Label: {ROLE_LABEL}.", "", "A fixed provider-ID/name priority excludes live markets without substituting opening prices. No outcome or model metric influences selection.", "", "| Season | Original | Retained | Excluded | Changed totals |", "|---|---:|---:|---:|---:|"]
    for row in coverage:
        lines.append(f"| {row['season']} | {row['original_games']} | {row['verified_provider_games']} | {row['excluded_games']} | {row['changed_total_count']} |")
    lines += ["", "Retained provider counts: "+json.dumps(dict(source_counts))+".", "", "The exported parquet contains game metadata, signed home spread, provider identity, response hash, source URL, and audit observation time. `role_verified=true`; `quote_timestamp=null`; `closing_time_verified=false`. Cached raw gzip responses are ignored by Git.", "", "## Limits", ""]
    lines.extend("- "+item for item in report["limitations"])
    (out / "espn_verified_provider_repair.md").write_text("\n".join(lines)+"\n")
    print(json.dumps({key:report[key] for key in ["input_games","verified_provider_games","excluded_games","coverage","provider_counts"]},indent=2),flush=True)
    return report


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument("--workers",type=int,default=6)
    parser.add_argument("--limit",type=int)
    args=parser.parse_args()
    run(args.root,args.workers,args.limit)
