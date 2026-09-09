"""Verified, selective PBP loading for a separately frozen research experiment.

``root`` is the model directory. This module performs no network calls, writes,
model fitting or outcome aggregation. Every source is verified before any table
read. Current public metadata reconciles the original 2025 partial body without
rewriting its failed original acquisition status. Parser coverage is descriptive.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import importlib
import json
from pathlib import Path, PurePosixPath
import subprocess

import pandas as pd

VERSION = "pbp-state-dataset-v1"
SEASONS = tuple(range(2019, 2026))
INVENTORY_PATH = "model/reports/PBP_RESEARCH_SOURCE_INVENTORY.json"
INVENTORY_SHA256 = "2898f79ea2509805a9da51bbfb0f45d4ebff6303e6428b9ac9c9bf4a9a88ccea"
AUDIT_PATH = "model/reports/PBP_RAW_ACQUISITION_AUDIT.json"
AUDIT_SHA256 = "052a1f8b1f2d1b3465b277fbbf5c7a7e86ce997db1f249789d1345630eedb3f9"
ARCHIVE = "model/data/raw/pbp_research/espn"
METADATA_PATH = "model/data/raw/pbp_research/metadata/github_espn_pbp_release_20260909.json"
METADATA_RECEIPT_PATH = "model/data/raw/pbp_research/metadata/github_espn_pbp_release_20260909.receipt.json"
RELEASE_URL = "https://api.github.com/repos/sportsdataverse/sportsdataverse-data/releases/tags/espn_cfb_pbp"
ASSET_BASE = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_cfb_pbp/"
SCHEDULE_COLUMNS = ("game_id", "season", "week", "game_date", "neutral_site", "home_id", "away_id", "status")


class DatasetIntegrityError(ValueError):
    pass


def _require(condition, reason):
    if not condition:
        raise DatasetIntegrityError(reason)


def _sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _path(repo, relative):
    _require(isinstance(relative, str) and "\\" not in relative, "Invalid source path")
    p = PurePosixPath(relative)
    _require(not p.is_absolute() and relative == p.as_posix() and bool(p.parts)
        and all(part not in (".", "..") for part in p.parts), "Source path traversal")
    candidate = repo.joinpath(*p.parts)
    _require(not any(part.is_symlink() for part in (candidate, *candidate.parents)), "Source symlink")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise DatasetIntegrityError("Missing source file: " + relative) from exc
    _require(resolved.is_relative_to(repo) and resolved.is_file(), "Source outside repository")
    return resolved


def _verified(repo, relative, expected, hashes):
    _require(isinstance(expected, str) and len(expected) == 64
        and all(c in "0123456789abcdef" for c in expected), "Invalid SHA-256")
    path = _path(repo, relative)
    actual = _sha(path)
    _require(actual == expected, "SHA-256 mismatch: " + relative)
    hashes[relative] = actual
    return path


def _json(repo, relative, expected, hashes):
    path = _verified(repo, relative, expected, hashes)
    with path.open("rb") as stream:
        value = json.load(stream)
    _require(isinstance(value, dict), "Expected JSON object: " + relative)
    return value


def _committed(repo, relative, expected):
    path = _path(repo, relative)
    try:
        frozen = subprocess.check_output(["git", "show", "HEAD:" + relative], cwd=repo, stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, OSError) as exc:
        raise DatasetIntegrityError("Inventory and audit must be committed") from exc
    _require(hashlib.sha256(frozen).hexdigest() == expected and path.read_bytes() == frozen,
             "Inventory/audit differ from pinned committed bytes")


def _time(value):
    _require(isinstance(value, str), "Missing original receipt timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DatasetIntegrityError("Invalid original receipt timestamp") from exc
    _require(result.tzinfo is not None and result.utcoffset() is not None, "Naive original receipt timestamp")
    return result


def _parser():
    return importlib.import_module("ncaaf_model.pbp_state_rows")


def _verify_sources(repo):
    """Verify every frozen metadata/receipt/body chain without reading rows."""
    hashes = {}
    _committed(repo, INVENTORY_PATH, INVENTORY_SHA256)
    _committed(repo, AUDIT_PATH, AUDIT_SHA256)
    inv = _json(repo, INVENTORY_PATH, INVENTORY_SHA256, hashes)
    audit = _json(repo, AUDIT_PATH, AUDIT_SHA256, hashes)
    _require(inv.get("schema_version") == "pbp-research-source-inventory-v1", "Wrong inventory schema")
    _require(inv.get("audit_path") == AUDIT_PATH and inv.get("audit_sha256") == AUDIT_SHA256, "Audit binding mismatch")
    _require(inv.get("selected_seasons") == list(SEASONS), "Inventory must select exactly 2019-2025")
    sources = inv.get("sources", [])
    _require(isinstance(sources, list) and [s.get("season") for s in sources] == list(SEASONS),
             "Duplicate, missing or out-of-order source season")
    _require([r.get("season") for r in audit.get("records", [])] == list(SEASONS), "Audit source seasons differ")
    _require(inv.get("original_acquisition_status") == "partial", "Original acquisition status changed")
    plan_path, run_path = ARCHIVE + "/acquisition_plan.json", ARCHIVE + "/runs/0001.json"
    _require(inv.get("original_acquisition_plan_path") == plan_path
        and inv.get("original_acquisition_run_path") == run_path, "Original acquisition paths changed")
    plan = _json(repo, plan_path, inv["original_acquisition_plan_sha256"], hashes)
    run = _json(repo, run_path, inv["original_acquisition_run_sha256"], hashes)
    _require(audit.get("plan_sha256") == inv["original_acquisition_plan_sha256"]
        and audit.get("acquisition_run_sha256") == inv["original_acquisition_run_sha256"], "Audit acquisition binding mismatch")
    _require(run.get("plan_path") == plan_path and run.get("plan_sha256") == hashes[plan_path]
        and run.get("status") == "partial" and run.get("completed_seasons") == list(SEASONS[:-1])
        and run.get("missing_seasons") == [2025] and run.get("error") == "expected_sha256_mismatch",
        "Original acquisition outcome changed")
    _require(plan.get("git_commit") == inv.get("source_acquisition_commit") == run.get("git_commit"),
        "Original acquisition provenance differs")
    _require(run.get("source_files_sha256") == plan.get("source_files_sha256"), "Original source-code provenance differs")
    planned_sources = plan.get("definition", {}).get("sources", [])
    run_receipts = run.get("new_receipts", [])
    _require([s.get("season") for s in planned_sources] == list(SEASONS)
        and [r.get("season") for r in run_receipts] == list(SEASONS), "Original acquisition source enumeration differs")
    release = inv.get("release_metadata", {})
    _require(release.get("body_path") == METADATA_PATH and release.get("receipt_path") == METADATA_RECEIPT_PATH,
        "Unexpected release metadata paths")
    metadata = _json(repo, METADATA_PATH, release["body_sha256"], hashes)
    metadata_receipt = _json(repo, METADATA_RECEIPT_PATH, release["receipt_sha256"], hashes)
    _require(metadata_receipt == release.get("original_receipt") and metadata_receipt.get("status") == 200
        and metadata_receipt.get("url") == RELEASE_URL and metadata_receipt.get("body_path") == METADATA_PATH
        and metadata_receipt.get("sha256") == hashes[METADATA_PATH]
        and metadata_receipt.get("bytes") == release.get("bytes") == _path(repo, METADATA_PATH).stat().st_size,
        "Current public metadata receipt mismatch")
    _require(_time(metadata_receipt["requested_at"]) <= _time(metadata_receipt["received_at"]), "Metadata receipt time order")
    _require(metadata.get("id") == release.get("release_id") and metadata.get("tag_name") == release.get("tag_name") == "espn_cfb_pbp",
        "Current public release identity mismatch")
    total_bytes = 0
    source_paths = []
    for source, audited, original, link in zip(sources, audit["records"], planned_sources, run_receipts):
        season = source["season"]
        expected_body = ARCHIVE + (f"/partials/2025/0001.parquet" if season == 2025 else f"/play_by_play_{season}.parquet")
        expected_receipt = ARCHIVE + f"/attempts/{season}/0001.receipt.json"
        expected_request = ARCHIVE + f"/attempts/{season}/0001.request.json"
        _require(source.get("body_path") == expected_body and source.get("receipt_path") == expected_receipt
            and source.get("request_path") == expected_request, "Source paths differ from original selected paths")
        for key in ("body_path", "body_sha256", "bytes", "source_url", "request_path", "request_sha256",
                    "receipt_path", "receipt_sha256", "acquisition_status", "expected_sha256", "error", "public_asset"):
            _require(source.get(key) == audited.get(key), "Inventory/audit source mismatch: " + key)
        _require(link.get("path") == expected_receipt and link.get("sha256") == source["receipt_sha256"], "Run receipt binding mismatch")
        request = _json(repo, expected_request, source["request_sha256"], hashes)
        receipt = _json(repo, expected_receipt, source["receipt_sha256"], hashes)
        _require(request.get("source") == receipt.get("source") == original, "Original source identity mismatch")
        _require(original.get("season") == season and original.get("filename") == f"play_by_play_{season}.parquet"
            and original.get("url") == source.get("source_url") == ASSET_BASE + f"play_by_play_{season}.parquet", "Unexpected source URL")
        _require(request.get("git_commit") == plan.get("git_commit")
            and request.get("source_files_sha256") == plan.get("source_files_sha256"), "Request provenance mismatch")
        _require(receipt.get("request_path") == expected_request and receipt.get("request_sha256") == source["request_sha256"]
            and receipt.get("receipt_path") == expected_receipt and receipt.get("body_path") == expected_body
            and receipt.get("sha256") == source["body_sha256"] and receipt.get("status") == source["acquisition_status"]
            and receipt.get("error") == source["error"] and receipt.get("status_code") == 200, "Original receipt binding mismatch")
        _require(request.get("requested_at") == receipt.get("requested_at") == source.get("requested_at")
            and receipt.get("received_at") == source.get("received_at")
            and _time(receipt["requested_at"]) <= _time(receipt["received_at"]), "Source receipt chronology mismatch")
        path = _verified(repo, expected_body, source["body_sha256"], hashes)
        _require(type(source["bytes"]) is int and 0 < source["bytes"] == path.stat().st_size
            == receipt.get("bytes_observed") == receipt.get("bytes_stored"), "Original body size mismatch")
        matching = [a for a in metadata.get("assets", []) if a.get("name") == original["filename"]]
        _require(len(matching) == 1, "Current public asset identity ambiguous")
        asset = matching[0]
        _require(isinstance(source.get("public_asset"), dict)
            and all(asset.get(k) == v for k, v in source["public_asset"].items())
            and asset.get("browser_download_url") == source["source_url"]
            and asset.get("digest") == "sha256:" + source["body_sha256"]
            and asset.get("size") == source["bytes"] and asset.get("state") == "uploaded", "Current public asset does not match original bytes")
        if season == 2025:
            reconcile = inv.get("source_reconciliation", {})
            _require(source["acquisition_status"] == "failed" and source["error"] == "expected_sha256_mismatch"
                and original.get("expected_sha256") == source["expected_sha256"] != source["body_sha256"]
                and reconcile.get("original_expected_sha256") == source["expected_sha256"]
                and reconcile.get("original_observed_sha256") == source["body_sha256"]
                and reconcile.get("current_api_asset_id") == asset.get("id")
                and reconcile.get("current_api_digest_matches_observed") is True, "Unreconciled original 2025 hash failure")
        else:
            _require(source["acquisition_status"] == "complete" and source["error"] is None, "Unexpected failed original source")
        source_paths.append(path)
        total_bytes += source["bytes"]
    _require(total_bytes == inv.get("total_bytes") == run.get("total_bytes_observed"), "Total source bytes differ")
    return inv, source_paths, hashes


def load_rows(root: Path):
    """Verify all seven sources, then prepare each season independently.

    Returns ``(rows, report)`` without writing a raw or normalized artifact.
    Caller must freeze parser/dataset/experiment before using real rows.
    """
    root = Path(root).resolve()
    _require(root.name == "model" and root.is_dir(), "root must be the model directory")
    repo = root.parent
    inventory, paths, hashes = _verify_sources(repo)
    parser = _parser()
    raw_columns = tuple(parser.RAW_COLUMNS)
    _require(raw_columns and len(set(raw_columns)) == len(raw_columns), "Parser allowlist is empty or duplicated")
    schedule_paths = []
    # Hash and anchor every schedule before the first row read, too.
    for season in SEASONS:
        relative = f"model/data/raw/sportsdataverse/cfb_schedule_{season}.parquet"
        path = _path(repo, relative)
        hashes[relative] = _sha(path)
        schedule_paths.append(path)
    pieces, reports = [], {}
    for season, path, schedule_path, source in zip(SEASONS, paths, schedule_paths, inventory["sources"]):
        raw = pd.read_parquet(path, columns=list(raw_columns), dtype_backend="numpy_nullable")
        schedules = pd.read_parquet(schedule_path, columns=list(SCHEDULE_COLUMNS), dtype_backend="numpy_nullable")
        _require(list(raw.columns) == list(raw_columns) and list(schedules.columns) == list(SCHEDULE_COLUMNS), "Selective read columns differ")
        _require(raw["season"].notna().all() and raw["season"].eq(season).all(), "PBP contains rows from another season")
        _require(schedules["season"].notna().all() and schedules["season"].eq(season).all(), "Schedule contains rows from another season")
        _require(len(raw) == source["footer_summary"]["num_rows"], "PBP row count differs from pinned footer")
        rows, coverage = parser.prepare_rows(raw, schedules)
        _require(isinstance(rows, pd.DataFrame) and isinstance(coverage, dict), "Unexpected parser result")
        _require(len(rows) <= len(raw), "Parser expanded raw rows")
        pieces.append(rows)
        reports[str(season)] = {"raw_rows": len(raw), "schedule_rows": len(schedules),
            "retained_rows": len(rows), "source_path": source["body_path"],
            "source_sha256": source["body_sha256"], "original_acquisition_status": source["acquisition_status"],
            "schedule_path": schedule_path.relative_to(repo).as_posix(),
            "schedule_sha256": hashes[schedule_path.relative_to(repo).as_posix()], "parser_coverage": coverage}
    combined = pd.concat(pieces, ignore_index=True)
    if len(combined) and {"game_id", "season"}.issubset(combined.columns):
        _require(combined.groupby("game_id")["season"].nunique(dropna=False).le(1).all(),
                 "Game identity appears in multiple seasons")
    if len(combined) and {"game_id", "play_id"}.issubset(combined.columns):
        _require(not combined.duplicated(["game_id", "play_id"]).any(), "Duplicate game/play identity across prepared seasons")
    report = {"schema_version": VERSION, "parser_version": getattr(parser, "VERSION", None),
        "inventory_path": INVENTORY_PATH, "inventory_sha256": INVENTORY_SHA256,
        "audit_path": AUDIT_PATH, "audit_sha256": AUDIT_SHA256,
        "seasons": list(SEASONS), "raw_columns": list(raw_columns), "schedule_columns": list(SCHEDULE_COLUMNS),
        "source_files_sha256": hashes, "per_season": reports,
        "total_raw_rows": sum(r["raw_rows"] for r in reports.values()), "retained_rows": len(combined),
        "scope": "Source/identity/clock/state coverage only; no model metrics or target outcome aggregation",
        "historical_source_receipts_available": False,
        "source_reconciliation": inventory["source_reconciliation"]}
    return combined, report
