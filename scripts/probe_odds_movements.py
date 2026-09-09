"""Bounded schema/retention probe; never fit, grade, or infer historical availability."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))
from ncaaf_model.revision_archive import ArchiveClient, immutable_json
from ncaaf_model.sources import load_dotenv


class Probe:
    def __init__(self, model_root, run_name, key, max_requests=7):
        self.root = model_root.resolve()
        self.path = self.root / "data/raw/odds_movement_probe" / run_name
        self.client = ArchiveClient(self.root, self.path, timeout=30.)
        self.key = key
        self.max_requests = max_requests
        self.remaining = None
        self.stopped = False
        receipts = [json.loads(p.read_text()) for p in (self.path / "receipts").glob("*.json")]
        receipts.sort(key=lambda r: datetime.fromisoformat(r["received_at"].replace("Z", "+00:00")))
        self.request_count = len(receipts)
        for index, receipt in enumerate(receipts):
            reported = receipt["response_headers"].get("x_ratelimit_remaining")
            if reported is not None:
                self.remaining = int(reported)
            elif self.remaining is not None:
                self.remaining -= 1
            if receipt["status_code"] in (401, 403, 429):
                self.stopped = True
            if index == 0 and (reported is None or int(reported) < 27):
                self.stopped = True

    def fetch(self, label, path, params):
        if path not in {"/odds/multi", "/odds/movements", "/historical/events", "/historical/odds"}:
            raise ValueError("Endpoint outside the bounded read-only probe")
        if not label or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in label):
            raise ValueError("Safe request label required")
        if (self.path / (label + ".json")).exists():
            raise RuntimeError("Request label already archived")
        if self.stopped or self.request_count >= self.max_requests:
            raise RuntimeError("Bounded probe stopped")
        if self.remaining is not None and self.remaining <= 20:
            raise RuntimeError("Quota reserve reached")
        envelope = self.client.fetch("https://api.odds-api.io/v3" + path, params,
                                     {"apiKey": self.key}, purpose="movement_probe_" + label)
        receipt = envelope["receipt"]
        self.request_count += 1
        reported = receipt["response_headers"].get("x_ratelimit_remaining")
        if reported is not None:
            self.remaining = int(reported)
        elif self.remaining is not None:
            self.remaining -= 1 # Conservative own-call accounting, not a fresh provider report.
        if receipt["status_code"] in (401, 403, 429) or (self.remaining is not None and self.remaining <= 20):
            self.stopped = True
        if self.request_count == 1 and (reported is None or int(reported) < 27):
            self.stopped = True
        immutable_json(self.path / (label + ".json"), {"receipt_path": receipt["receipt_path"],
            "request_count": self.request_count, "quota_remaining_reported": reported,
            "remaining_after_own_call_accounting": self.remaining, "retrieved_at": receipt["received_at"]})
        return envelope


def shape(value, depth=0):
    """Only schema names/types; historical event scores are never printed."""
    if isinstance(value, dict):
        return {k: shape(v, depth + 1) if depth < 2 else type(v).__name__ for k, v in value.items()}
    if isinstance(value, list):
        return {"type": "list", "length": len(value), "item_shapes": [shape(v, depth + 1) for v in value[:1]] if depth < 2 else []}
    return type(value).__name__


def summary(envelope):
    receipt = envelope["receipt"]
    return {"status_code": receipt["status_code"], "transport_error": receipt["transport_error"],
            "received_at": receipt["received_at"], "body_sha256": receipt["body_sha256"],
            "response_headers": receipt["response_headers"], "schema": shape(envelope["payload"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request-file", type=Path, required=True)
    parser.add_argument("--run-name", required=True)
    args = parser.parse_args()
    if not args.run_name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in args.run_name):
        parser.error("run-name must be a safe lowercase archive name")
    requests = json.loads(args.request_file.read_text())
    if not isinstance(requests, list) or not 1 <= len(requests) <= 7:
        parser.error("one to seven explicitly specified read requests required")
    load_dotenv(ROOT.parent / ".env")
    key = os.environ.get("ODDS_API_IO_KEY")
    if not key:
        parser.error("existing odds key unavailable")
    probe = Probe(ROOT / "model", args.run_name, key)
    for request in requests:
        result = probe.fetch(request["label"], request["path"], request["params"])
        print(json.dumps({"label": request["label"], **summary(result)}, sort_keys=True), flush=True)
        if probe.stopped:
            break


if __name__ == "__main__":
    main()
