"""Replay frozen totals models against pre-existing 2026 priced snapshots.

Forecasts are reconstructed now, not claimed as published before these games.
No live ledger or fitted artifact is changed. Source receipt metadata and past
feature availability remain explicitly weaker than independently attested logs.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .config import load_settings
from .opponent_model import adjusted_features, load_history, projections
from .runtime import (VERSION, clean_json, forecast_performance, grade_positions, performance,
                      quotes_from_events, record_forecasts, record_positions, score_games)

REPLAY_VERSION = "2026-priced-replay-v1"
BOOK_COHORTS = {"connected_two_books": ("draftkings", "fanduel")}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(clean_json(value), indent=2, allow_nan=False) + "\n")


def make_plan(root):
    root = Path(root)
    if (root / "reports/archived_2026_replay_plan.json").exists():
        raise ValueError("A frozen replay plan already exists; never overwrite it after evaluation")
    quotes_path = root / "data/raw/alternative/archived_2026_paired_quotes.parquet"
    quotes = pd.read_parquet(quotes_path)
    artifact_path = root / "data/models/opponent_adjusted_v1.json"
    distribution_path = root / "data/models/score_distribution_v2.json"
    artifact, distribution = read(artifact_path), read(distribution_path)
    if max(artifact["training_seasons"]) >= 2026:
        raise ValueError("2026 replay requires model trained strictly before 2026")
    if artifact["data_fingerprint"] != distribution["data_fingerprint"]:
        raise ValueError("Model and distribution do not match")
    q = quotes.loc[quotes.source.eq("the_odds_api") & quotes.within14days & quotes.receipt_hash_matches].copy()
    if q.outcomes_loaded.any():
        raise ValueError("Quote manifest must precede outcome evaluation")
    plan = {"version": REPLAY_VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
            "candidate_version": VERSION, "quote_path": str(quotes_path.relative_to(root)),
            "quote_sha256": sha(quotes_path), "model_sha256": sha(artifact_path),
            "distribution_sha256": sha(distribution_path), "data_fingerprint": artifact["data_fingerprint"],
            "source_manifest_sha256": sha(root / "data/raw/alternative/archived_2026_quote_manifest.json"),
            "book_cohorts": BOOK_COHORTS,
            "snapshots": [{"source_sha256": str(key[0]), "observed_at": pd.Timestamp(key[1]).isoformat(),
                           "games": int(frame.game_id.nunique())}
                          for key, frame in q.groupby(["source_sha256", "observed_at"], sort=True)],
            "quote_rows": len(q), "games": int(q.game_id.nunique()),
            "first_entry_rule": "First qualifying archived snapshot per candidate/game; first forecast including abstentions scored separately.",
            "snapshot_horizon": "Actual archive receipt, pre-kickoff within 14 days; no exact 06:30 claim.",
            "quote_freshness": "Unchanged live v4 non-IO rule: actual market/book timestamp within 60 minutes, no future updates.",
            "schedule_status_assumption": "Pregame status inferred from archived future event plus retrospectively matched official kickoff; contemporaneous official state not independently attested.",
            "outcomes_used_to_select_cohort": False, "thresholds_changed": False}
    plan["plan_sha256"] = hashlib.sha256(json.dumps(clean_json(plan), sort_keys=True).encode()).hexdigest()
    write(root / "reports/archived_2026_replay_plan.json", plan)
    print(json.dumps({k: plan[k] for k in ("plan_sha256", "quote_rows", "games", "book_cohorts")}))
    return plan


def event_batch(frame):
    """Restore exact paired quotes; market-update clocks are never rewritten."""
    events = []
    for game_id, rows in frame.groupby("game_id", sort=True):
        identity = rows.iloc[0]
        books = []
        for book, quotes in rows.groupby("book", sort=True):
            if len(quotes) != 1:
                raise ValueError("Conflicting duplicate book snapshot")
            q = quotes.iloc[0]
            update = pd.Timestamp(q.market_updated_at).isoformat() if pd.notna(q.market_updated_at) else None
            markets = [{"key": "totals", "last_update": update, "outcomes": [
                {"name": "Over", "point": float(q.line), "price": float(q.over_price)},
                {"name": "Under", "point": float(q.line), "price": float(q.under_price)}]}]
            if pd.notna(q.market_home_spread):
                markets.append({"key": "spreads", "outcomes": [{"name": identity.home_team, "point": float(q.market_home_spread)}]})
            books.append({"key": book, "source": "the_odds_api", "last_update": update, "markets": markets})
        events.append({"id": str(game_id), "home_team": identity.home_team, "away_team": identity.away_team,
                       "commence_time": pd.Timestamp(identity.kickoff).isoformat(),
                       "bookmakers": books, "source": "the_odds_api"})
    return events


def replay_cohort(frame, history, artifact, distribution, settings):
    positions, entries, snapshot_summary = [], [], []
    grouping = frame.groupby(["observed_at", "source_sha256"], sort=True)
    for (observed, source_hash), batch in grouping:
        now = pd.Timestamp(observed).to_pydatetime()
        odds = quotes_from_events(event_batch(batch), settings, now)
        if odds.empty:
            continue
        identities = batch.drop_duplicates("game_id")[["game_id", "season", "week", "home_id", "away_id", "neutral_site", "kickoff"]].copy()
        identities["event_id"] = identities.game_id.astype(str)
        games = odds.merge(identities, on="event_id", validate="one_to_one")
        games["espn_game_id"] = games.game_id
        games["canonical_kickoff"] = pd.to_datetime(games.kickoff, utc=True).map(lambda t: t.isoformat())
        games["game_date"] = games.canonical_kickoff
        games["schedule_match"] = True
        games["schedule_status"] = "STATUS_SCHEDULED"
        features = adjusted_features(games, history, as_of=now)
        games["home_prior_games"] = features.adjusted_history_games
        games["away_prior_games"] = features.adjusted_history_games
        predicted = projections(features, artifact)
        rows = score_games(games, predicted, distribution, now, {"schedule_fresh": True, "input_failures": {}})
        for row in rows:
            row.update(reconstructed_after_games=True, source_snapshot_sha256=source_hash,
                       source_observed_at=now.isoformat(), execution_confirmed=False)
        positions = record_positions(positions, rows, now)
        entries = record_forecasts(entries, rows, now)
        snapshot_summary.append({"observed_at": now.isoformat(), "games": len(games),
                                 "currently_eligible_forecasts": sum(row["eligible"] for row in rows),
                                 "source_sha256": source_hash,
                                 "ratings_cutoffs": sorted(features.ratings_cutoff.unique())})
    return positions, entries, snapshot_summary


def read_frozen_plan(root):
    root = Path(root)
    plan = read(root / "reports/archived_2026_replay_plan.json")
    digest = plan.pop("plan_sha256")
    if hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest() != digest:
        raise ValueError("Replay plan changed")
    plan["plan_sha256"] = digest
    if plan.get("version") != REPLAY_VERSION or plan.get("candidate_version") != VERSION:
        raise ValueError("Replay or candidate version changed after plan")
    if plan.get("book_cohorts") != {name: list(books) for name, books in BOOK_COHORTS.items()}:
        raise ValueError("Book cohorts changed after plan")
    paths = {"quote_sha256": root / plan["quote_path"],
             "model_sha256": root / "data/models/opponent_adjusted_v1.json",
             "distribution_sha256": root / "data/models/score_distribution_v2.json",
             "source_manifest_sha256": root / "data/raw/alternative/archived_2026_quote_manifest.json"}
    if any(sha(path) != plan[key] for key, path in paths.items()):
        raise ValueError("Quotes, provenance manifest or frozen model changed after plan")
    return plan, paths


def evaluate(root):
    root = Path(root)
    plan, paths = read_frozen_plan(root)
    digest = plan["plan_sha256"]
    artifact, distribution = read(paths["model_sha256"]), read(paths["distribution_sha256"])
    history = pd.concat([pd.read_parquet(root / "data/models/opponent_history.parquet"), load_history(root, [2026])], ignore_index=True)
    quotes = pd.read_parquet(paths["quote_sha256"])
    quotes = quotes.loc[quotes.source.eq("the_odds_api") & quotes.within14days & quotes.receipt_hash_matches].copy()
    settings = replace(load_settings(), root=root)
    report = {"version": REPLAY_VERSION, "plan_sha256": digest, "evaluated_at": datetime.now(timezone.utc).isoformat(),
              "status": "retrospective_2026_replay_with_observed_prices_not_prospective_forecasts", "cohorts": {},
              "limitations": ["Models fitted only through 2025, but this replay was created after 2026 game outcomes were available.",
                  "Raw quote receipts have matching local metadata hashes; no independent pre-kickoff timestamp attestation of every raw price.",
                  "Exact recorded prices replace the -110 assumption; sportsbook acceptance, stake limits and account availability remain unverified.",
                  "Prior game inputs use kickoff+6h availability proxies and retrospective corrections. Target and future outcomes are excluded from feature cutoffs.",
                  "The original pre-evaluation plan pinned quote, provenance and fitted-model files, but did not pin code, configuration or history hashes; full byte-for-byte pre-evaluation reproducibility is not attested.",
                  "Archive capture times and sparse capture dates differ from the current 06:30 publication schedule.",
                  "Unchanged candidate models and qualification thresholds; no post-result selection of games, books or forecasts."]}
    for name, books in plan["book_cohorts"].items():
        q = quotes.loc[quotes.book.isin(books)].copy()
        positions, entries, snapshots = replay_cohort(q, history, artifact, distribution, replace(settings, allowed_books=tuple(books)))
        # Outcomes first enter forecast scoring only after predictions are fixed.
        schedule = pd.read_parquet(root / "data/raw/sportsdataverse/cfb_schedule_2026.parquet")
        positions, entries = grade_positions(positions, schedule), grade_positions(entries, schedule)
        perf = {candidate: performance(positions, candidate) for candidate in sorted({r["candidate"] for r in entries})}
        report["cohorts"][name] = {"books": books, "snapshot_count": len(snapshots),
            "forecast_games": len({r["game_id"] for r in entries}), "positions": perf,
            "forecast_metrics": forecast_performance(entries), "snapshots": snapshots,
            "positions_source_path": f"data/raw/alternative/2026_replay_{name}_positions.json"}
        write(root / f"data/raw/alternative/2026_replay_{name}_positions.json", positions)
        write(root / f"data/raw/alternative/2026_replay_{name}_forecasts.json", entries)
    write(root / "reports/archived_2026_replay_results.json", report)
    lines = ["# Frozen-model 2026 replay with recorded prices", "",
             "These predictions were reconstructed after the games. They are not a pre-existing prospective forecast record. Actual archived prices, rather than assumed −110, determine hypothetical returns.", "",
             "| Books | Candidate | Settled signals | W–L–P | ROI | Pending |",
             "|---|---|---:|---|---:|---:|"]
    for cohort, values in report["cohorts"].items():
        for candidate, m in values["positions"].items():
            roi = "—" if m["roi"] is None else f"{m['roi']:+.2%}"
            lines.append(f"| {cohort} | {candidate} | {m['bets']} | {m['wins']}–{m['losses']}–{m['pushes']} | {roi} | {m['pending']} |")
    lines += ["", "## Limits", "", *["- " + text for text in report["limitations"]], "",
              "Reproduce after normalizing the existing source archives: `python -m ncaaf_model.archived_replay --plan`, then `python -m ncaaf_model.archived_replay --evaluate`. No live ledger is written."]
    (root / "reports/archived_2026_replay_results.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({name: cohort["positions"] for name, cohort in report["cohorts"].items()}, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    stage = parser.add_mutually_exclusive_group(required=True)
    stage.add_argument("--plan", action="store_true")
    stage.add_argument("--evaluate", action="store_true")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    make_plan(args.root) if args.plan else evaluate(args.root)


if __name__ == "__main__":
    main()
