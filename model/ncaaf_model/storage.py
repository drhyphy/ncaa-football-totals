from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_bytes(path, (json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n").encode())


def atomic_write_csv(path: Path, frame: pd.DataFrame) -> None:
    atomic_write_bytes(path, frame.to_csv(index=False).encode())


def append_csv(path: Path, frame: pd.DataFrame, dedupe_key: str | None = None) -> pd.DataFrame:
    combined = frame.copy()
    if path.exists():
        prior = pd.read_csv(path)
        combined = pd.concat([prior, combined], ignore_index=True)
    if dedupe_key and dedupe_key in combined:
        combined = combined.drop_duplicates(dedupe_key, keep="last")
    atomic_write_csv(path, combined)
    return combined
