"""Publish interim evaluation and freeze the fixed-cutoff report exactly once."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "model"))
from ncaaf_model.prospective_evaluation import evaluate
from ncaaf_model.storage import atomic_write_bytes

CUTOFF = datetime(2027, 2, 8, 12, tzinfo=timezone.utc)
FINAL_NAME = "prospective_evaluation_final.json"


def encoded(value) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def digest(value) -> str:
    return hashlib.sha256(encoded(value)).hexdigest()


def read_positions(path: Path) -> list:
    value = json.loads(path.read_text())
    if not isinstance(value, list):
        raise ValueError("Position ledger must be an array")
    return value


def create_once(path: Path, content: bytes) -> None:
    """Atomic no-replace publication; safe if two recovery runs race."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".prospective-final-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            pass  # The first completed publication remains authoritative.
    finally:
        os.unlink(temporary)


def frozen_report(path: Path) -> dict:
    bundle = json.loads(path.read_text())
    report = bundle.get("report") or {}
    if (bundle.get("schema_version") != 1 or bundle.get("cutoff") != CUTOFF.isoformat()
            or bundle.get("input_sha256") != digest(bundle.get("inputs"))
            or bundle.get("report_sha256") != digest(report)
            or report.get("stage") != "formal_cutoff_report"
            or report.get("evaluation_cutoff") != "2027-02-08T12:00:00Z"
            or report.get("publication", {}).get("frozen") is not True):
        raise ValueError("Frozen prospective report failed integrity verification")
    return report


def publish(root: Path, now: datetime) -> dict:
    if now.tzinfo is None:
        raise ValueError("Publication time must have a timezone")
    ledger = root / "model/ledger"
    final_path = ledger / FINAL_NAME
    if final_path.exists():
        report = frozen_report(final_path)
    else:
        inputs = {name: read_positions(ledger / name) for name in
                  ("positions.json", "weather_positions.json")}
        report = evaluate(inputs["positions.json"] + inputs["weather_positions.json"], as_of=now)
        report["publication"] = {
            "published_at": now.astimezone(timezone.utc).isoformat(),
            "frozen": now >= CUTOFF, "input_sha256": digest(inputs),
            "final_archive_url": (
                "https://github.com/drhyphy/ncaa-football-totals/blob/main/model/ledger/" + FINAL_NAME
            ) if now >= CUTOFF else None,
        }
        if now >= CUTOFF:
            bundle = {"schema_version": 1, "cutoff": CUTOFF.isoformat(),
                      "inputs": inputs, "input_sha256": digest(inputs),
                      "report": report, "report_sha256": digest(report)}
            create_once(final_path, encoded(bundle))
            report = frozen_report(final_path)
    atomic_write_bytes(root / "site/data/prospective-evaluation.json", encoded(report))
    if report["publication"]["frozen"]:
        atomic_write_bytes(root / "site/data/prospective-evaluation-final.json", encoded(report))
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    report = publish(args.root, datetime.now(timezone.utc))
    print(json.dumps({"report": "site/data/prospective-evaluation.json",
                      "frozen": report["publication"]["frozen"]}))


if __name__ == "__main__":
    main()
