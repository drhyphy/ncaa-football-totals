"""Small, standard-library publication guard for GitHub Actions.

The success guard uses the board date and generation time in America/New_York.
It never suppresses deployment, so a backup can repair a failed Pages deployment.
"""
from __future__ import annotations

import argparse
from datetime import datetime, time, timezone
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

ZONE = ZoneInfo("America/New_York")


def read_board(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo is not None else None
    except ValueError:
        return None


def already_succeeded(board: dict, now: datetime) -> bool:
    local = now.astimezone(ZONE)
    generated = timestamp(board.get("generated_at"))
    return bool(
        board.get("schema_version") == 1
        and board.get("status") == "ok"
        and board.get("date") == local.date().isoformat()
        and generated is not None
        and generated.astimezone(ZONE).date() == local.date()
        and generated.astimezone(ZONE).time() >= time(6, 30)
        and 0 <= (now - generated).total_seconds() <= 26 * 3600
    )


def publication_board(board: dict, outcome: str, now: datetime) -> tuple[dict, bool]:
    generated = timestamp(board.get("generated_at"))
    current = bool(
        generated is not None
        and 0 <= (now - generated).total_seconds() <= 26 * 3600
        and board.get("schema_version") == 1
        and board.get("date") == now.astimezone(ZONE).date().isoformat()
        and board.get("status") == "ok"
    )
    if outcome != "failure" and current:
        return board, False
    # Preserve published evidence and ledger, while explicitly removing picks.
    result = dict(board)
    message = board.get("message") if board.get("status") == "unavailable" else None
    result.update({
        "schema_version": 1,
        "generated_at": now.astimezone(timezone.utc).isoformat(),
        "date": now.astimezone(ZONE).date().isoformat(),
        "timezone": "America/New_York",
        "status": "unavailable",
        "message": message or "The latest daily refresh did not complete successfully. Selections are paused until a successful run.",
        "evidence_status": "research_only",
        "today_picks": [],
        "upcoming_picks": [],
        "forecasts": [],
    })
    result.setdefault("diagnostics", {})["publication_guard"] = "refresh_failed_or_board_invalid"
    return result, True


def emit(name: str, value: str) -> None:
    output = os.getenv("GITHUB_OUTPUT")
    if output:
        with open(output, "a") as stream:
            stream.write(f"{name}={value}\n")
    print(f"{name}={value}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["decision", "prepare"])
    parser.add_argument("--board", type=Path, default=Path("site/data/board.json"))
    parser.add_argument("--event", default=os.getenv("GITHUB_EVENT_NAME", "workflow_dispatch"))
    parser.add_argument("--outcome", default="success", choices=["success", "failure", "skipped"])
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    board = read_board(args.board)
    if args.command == "decision":
        skip = args.event == "schedule" and already_succeeded(board, now)
        emit("refresh", "false" if skip else "true")
        return
    board, failed = publication_board(board, args.outcome, now)
    args.board.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.board.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(board, indent=2, allow_nan=False) + "\n")
    temporary.replace(args.board)
    emit("refresh_failed", "true" if failed else "false")


if __name__ == "__main__":
    main()
