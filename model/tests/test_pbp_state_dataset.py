"""Synthetic-only receipt verification and selective nullable PBP loading."""
import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pandas as pd
from pyarrow import ArrowInvalid
import pytest

from ncaaf_model import pbp_state_dataset as dataset


RAW_COLUMNS = ("season", "game_id", "id", "clock.displayValue", "homeScore", "awayScore")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    path.write_bytes(body)
    return hashlib.sha256(body).hexdigest()


def file_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def archive(tmp_path, monkeypatch, request):
    """Build a seven-season, original-receipt-shaped synthetic archive."""
    repo = tmp_path.resolve()
    model = repo / "model"
    model.mkdir()
    sources, original_sources, links, assets = [], [], [], []
    code = {"synthetic_source.py": "b" * 64}
    committed = "a" * 40
    timestamp = "2026-09-09T00:00:00+00:00"
    real_parser = None
    if getattr(request, "param", None) == "real_parser":
        from ncaaf_model import pbp_state_rows
        real_parser = pbp_state_rows
    for season in dataset.SEASONS:
        body_rel = dataset.ARCHIVE + ("/partials/2025/0001.parquet" if season == 2025 else f"/play_by_play_{season}.parquet")
        path = repo / body_rel
        path.parent.mkdir(parents=True, exist_ok=True)
        # This ID would round if ever routed through float64; null in the same
        # column exercises the nullable Parquet loading path explicitly.
        raw = pd.DataFrame({"season": pd.array([season, season], dtype="Int64"),
            "game_id": pd.array([2**53 + season, 2**53 + season], dtype="Int64"),
            "id": pd.array([2**53 + 10000 + season, None], dtype="Int64"),
            "clock.displayValue": ["12:00", "11:30"], "homeScore": [0, 0], "awayScore": [0, 0],
            "EPA": [999, 999], "actual_total": [999, 999], "market_total": [999, 999]})
        if real_parser is not None:
            records = []
            for number in (1, 2, 3):
                record = {k: 0 for k in real_parser.INTEGER_COLUMNS}
                record.update({k: False for k in real_parser.FLAG_COLUMNS})
                record.update({"season": season, "game_id": 2**53 + season,
                    "id": 2**53 + 10000 + season * 10 + number,
                    "sequenceNumber": 2**53 + 20000 + season * 10 + number,
                    "game_play_number": number, "homeTeamId": 1, "awayTeamId": 2,
                    "drive.id": "synthetic-drive", "type.text": "Rush", "orig_play_type": "Rush",
                    "text": "Runner rushes for four yards", "period.number": 1,
                    "clock.displayValue": f"14:{60 - number * 10:02d}", "start.down": 1,
                    "start.distance": 10, "start.yardsToEndzone": 70, "start.team.id": 1,
                    "end.team.id": 1, "statYardage": 4})
                records.append(record)
            raw = pd.DataFrame(records, columns=real_parser.RAW_COLUMNS)
            for name in real_parser.INTEGER_COLUMNS:
                raw[name] = pd.array([row[name] for row in records], dtype="Int64")
            for name in real_parser.FLAG_COLUMNS:
                raw[name] = pd.array([row[name] for row in records], dtype="boolean")
            raw["EPA"] = 999
            raw["actual_total"] = 999
        raw.to_parquet(path, index=False)
        size, sha = path.stat().st_size, file_sha(path)
        original = {"season": season, "filename": f"play_by_play_{season}.parquet",
            "url": dataset.ASSET_BASE + f"play_by_play_{season}.parquet",
            "expected_sha256": "f" * 64 if season == 2025 else None}
        original_sources.append(original)
        req_rel = dataset.ARCHIVE + f"/attempts/{season}/0001.request.json"
        rec_rel = dataset.ARCHIVE + f"/attempts/{season}/0001.receipt.json"
        request = {"source": original, "requested_at": timestamp, "git_commit": committed, "source_files_sha256": code}
        req_sha = write_json(repo / req_rel, request)
        status, error = ("failed", "expected_sha256_mismatch") if season == 2025 else ("complete", None)
        receipt = {"source": original, "request_path": req_rel, "request_sha256": req_sha,
            "receipt_path": rec_rel, "body_path": body_rel, "sha256": sha,
            "bytes_observed": size, "bytes_stored": size, "requested_at": timestamp, "received_at": timestamp,
            "status": status, "error": error, "status_code": 200}
        rec_sha = write_json(repo / rec_rel, receipt)
        asset = {"id": season, "name": original["filename"], "size": size, "digest": "sha256:" + sha,
            "state": "uploaded", "browser_download_url": original["url"]}
        assets.append(asset)
        source = {"season": season, "body_path": body_rel, "body_sha256": sha, "bytes": size,
            "source_url": original["url"], "request_path": req_rel, "request_sha256": req_sha,
            "receipt_path": rec_rel, "receipt_sha256": rec_sha, "acquisition_status": status,
            "expected_sha256": original["expected_sha256"], "error": error, "public_asset": asset,
            "requested_at": timestamp, "received_at": timestamp, "footer_summary": {"num_rows": len(raw)}}
        sources.append(source)
        links.append({"season": season, "path": rec_rel, "sha256": rec_sha})
        sp = model / f"data/raw/sportsdataverse/cfb_schedule_{season}.parquet"
        sp.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"game_id": pd.array([2**53 + season], dtype="Int64"), "season": [season], "week": [1],
            "game_date": [f"{season}-09-01T16:00:00Z"], "neutral_site": [False], "home_id": [1], "away_id": [2],
            "status": ["STATUS_FINAL"], "home_score": [999], "away_score": [999], "actual_total": [999]}).to_parquet(sp, index=False)
    plan_rel, run_rel = dataset.ARCHIVE + "/acquisition_plan.json", dataset.ARCHIVE + "/runs/0001.json"
    plan = {"git_commit": committed, "source_files_sha256": code, "definition": {"sources": original_sources}}
    plan_sha = write_json(repo / plan_rel, plan)
    run = {"plan_path": plan_rel, "plan_sha256": plan_sha, "status": "partial", "completed_seasons": list(dataset.SEASONS[:-1]),
        "missing_seasons": [2025], "error": "expected_sha256_mismatch", "git_commit": committed,
        "source_files_sha256": code, "new_receipts": links, "total_bytes_observed": sum(s["bytes"] for s in sources)}
    run_sha = write_json(repo / run_rel, run)
    metadata = {"id": 101941082, "tag_name": "espn_cfb_pbp", "assets": assets}
    metadata_sha = write_json(repo / dataset.METADATA_PATH, metadata)
    metadata_size = (repo / dataset.METADATA_PATH).stat().st_size
    metadata_receipt = {"status": 200, "url": dataset.RELEASE_URL, "body_path": dataset.METADATA_PATH,
        "sha256": metadata_sha, "bytes": metadata_size, "requested_at": timestamp, "received_at": timestamp}
    metadata_receipt_sha = write_json(repo / dataset.METADATA_RECEIPT_PATH, metadata_receipt)
    release = {"body_path": dataset.METADATA_PATH, "body_sha256": metadata_sha,
        "receipt_path": dataset.METADATA_RECEIPT_PATH, "receipt_sha256": metadata_receipt_sha,
        "bytes": metadata_size, "release_id": metadata["id"], "tag_name": metadata["tag_name"], "original_receipt": metadata_receipt}
    audit = {"records": sources, "plan_sha256": plan_sha, "acquisition_run_sha256": run_sha}
    audit_sha = write_json(repo / dataset.AUDIT_PATH, audit)
    inventory = {"schema_version": "pbp-research-source-inventory-v1", "audit_path": dataset.AUDIT_PATH,
        "audit_sha256": audit_sha, "selected_seasons": list(dataset.SEASONS), "sources": sources,
        "original_acquisition_status": "partial", "original_acquisition_plan_path": plan_rel,
        "original_acquisition_plan_sha256": plan_sha, "original_acquisition_run_path": run_rel,
        "original_acquisition_run_sha256": run_sha, "source_acquisition_commit": committed,
        "release_metadata": release, "total_bytes": sum(s["bytes"] for s in sources),
        "source_reconciliation": {"original_expected_sha256": "f" * 64, "original_observed_sha256": sources[-1]["body_sha256"],
            "current_api_asset_id": 2025, "current_api_digest_matches_observed": True}}
    inventory_sha = write_json(repo / dataset.INVENTORY_PATH, inventory)
    monkeypatch.setattr(dataset, "INVENTORY_SHA256", inventory_sha)
    monkeypatch.setattr(dataset, "AUDIT_SHA256", audit_sha)
    monkeypatch.setattr(dataset, "_committed", lambda *args: None)
    calls = []
    def prepare_rows(raw, schedules):
        calls.append((raw.copy(), schedules.copy()))
        output = raw.dropna(subset=["id"])[["season", "game_id", "id"]].rename(columns={"id": "play_id"})
        return output, {"input_rows": len(raw), "retained_rows": len(output), "synthetic_invalid_id_rows": int(raw.id.isna().sum())}
    parser = SimpleNamespace(RAW_COLUMNS=RAW_COLUMNS, VERSION="synthetic-parser-v1", prepare_rows=prepare_rows)
    monkeypatch.setattr(dataset, "_parser", lambda: real_parser or parser)
    return SimpleNamespace(repo=repo, model=model, inventory=inventory, sources=sources, calls=calls,
                           parser=parser, audit=audit, metadata=metadata, metadata_receipt=metadata_receipt)


def repin_inventory(archive, monkeypatch):
    monkeypatch.setattr(dataset, "INVENTORY_SHA256", write_json(archive.repo / dataset.INVENTORY_PATH, archive.inventory))


def test_selective_columns_nullable_large_ids_and_season_independence(archive, monkeypatch):
    original_read = pd.read_parquet
    reads = []
    def reader(path, **kwargs):
        reads.append((str(path), kwargs))
        return original_read(path, **kwargs)
    monkeypatch.setattr(pd, "read_parquet", reader)
    rows, report = dataset.load_rows(archive.model)
    assert len(rows) == 7 and report["retained_rows"] == 7 and report["total_raw_rows"] == 14
    assert len(archive.calls) == 7 and len(reads) == 14
    for season, (raw, schedule), raw_read, schedule_read in zip(dataset.SEASONS, archive.calls, reads[::2], reads[1::2]):
        assert raw_read[1] == {"columns": list(RAW_COLUMNS), "dtype_backend": "numpy_nullable"}
        assert schedule_read[1] == {"columns": list(dataset.SCHEDULE_COLUMNS), "dtype_backend": "numpy_nullable"}
        assert list(raw) == list(RAW_COLUMNS) and list(schedule) == list(dataset.SCHEDULE_COLUMNS)
        assert str(raw.id.dtype) == "Int64" and str(raw.game_id.dtype) == "Int64"
        assert raw.id.iloc[0] == 2**53 + 10000 + season and pd.isna(raw.id.iloc[1])
        assert schedule.game_id.iloc[0] == 2**53 + season
        assert "EPA" not in raw and "actual_total" not in raw and "home_score" not in schedule
        assert report["per_season"][str(season)]["retained_rows"] == 1
    assert report["per_season"]["2025"]["original_acquisition_status"] == "failed"
    assert report["per_season"]["2025"]["source_path"].endswith("partials/2025/0001.parquet")
    assert set(report["source_files_sha256"]) >= {dataset.INVENTORY_PATH, dataset.AUDIT_PATH, dataset.METADATA_PATH}
    assert not any("profit" in key or "roi" in key for key in report)
    assert not str(archive.repo) in json.dumps(report)


def test_loading_writes_no_source_or_cache(archive):
    before = {p: file_sha(p) for p in archive.repo.rglob("*") if p.is_file()}
    dataset.load_rows(archive.model)
    after = {p: file_sha(p) for p in archive.repo.rglob("*") if p.is_file()}
    assert before == after


@pytest.mark.parametrize("archive", ["real_parser"], indirect=True)
def test_real_parser_integration_on_seven_tiny_original_byte_archives(archive):
    from ncaaf_model import pbp_state_rows as parser
    rows, report = dataset.load_rows(archive.model)
    assert len(rows) == 14 and report["total_raw_rows"] == 21
    assert report["raw_columns"] == list(parser.RAW_COLUMNS)
    assert report["parser_version"] == parser.VERSION
    for season in dataset.SEASONS:
        piece = rows.loc[rows.season.eq(season)]
        assert piece.game_play_number.tolist() == [2, 3]
        assert piece.game_id.tolist() == [2**53 + season] * 2
        assert piece.play_id.tolist() == [2**53 + 10000 + season * 10 + n for n in (2, 3)]
        assert piece.sequence_number.tolist() == [2**53 + 20000 + season * 10 + n for n in (2, 3)]
        assert piece.clock_seconds.iloc[0] == 10 and pd.isna(piece.clock_seconds.iloc[1])
        assert piece.available_at.eq(pd.Timestamp(f"{season}-09-01T22:00:00Z")).all()
        assert report["per_season"][str(season)]["parser_coverage"]["exclusions"]["unavailable_previous_post_score"] == 1


@pytest.mark.parametrize("kind", ["inventory", "audit", "plan", "run", "metadata", "metadata_receipt", "request", "receipt", "body"])
def test_hash_tamper_any_chain_blocks_before_table_reads(archive, monkeypatch, kind):
    paths = {"inventory": dataset.INVENTORY_PATH, "audit": dataset.AUDIT_PATH,
        "plan": archive.inventory["original_acquisition_plan_path"], "run": archive.inventory["original_acquisition_run_path"],
        "metadata": dataset.METADATA_PATH, "metadata_receipt": dataset.METADATA_RECEIPT_PATH,
        "request": archive.sources[-1]["request_path"], "receipt": archive.sources[-1]["receipt_path"], "body": archive.sources[-1]["body_path"]}
    path = archive.repo / paths[kind]
    path.write_bytes(path.read_bytes() + b"tampered")
    monkeypatch.setattr(pd, "read_parquet", lambda *a, **k: pytest.fail("Verification must precede every table read"))
    with pytest.raises(dataset.DatasetIntegrityError, match="SHA-256"):
        dataset.load_rows(archive.model)


@pytest.mark.parametrize("bad_path", ["../outside.parquet", "/tmp/outside.parquet",
    "model/data/raw/../../.env", "model\\data\\outside.parquet", "model/data/raw/pbp_research/espn/other.parquet"])
def test_arbitrary_source_paths_never_opened(archive, monkeypatch, bad_path):
    archive.inventory["sources"][0]["body_path"] = bad_path
    repin_inventory(archive, monkeypatch)
    monkeypatch.setattr(pd, "read_parquet", lambda *a, **k: pytest.fail("No table read"))
    with pytest.raises(dataset.DatasetIntegrityError, match="paths differ"):
        dataset.load_rows(archive.model)


@pytest.mark.parametrize("change", ["missing", "duplicate", "reverse", "extra"])
def test_exact_seven_source_enumeration(archive, monkeypatch, change):
    entries = archive.inventory["sources"]
    if change == "missing":
        entries.pop()
    elif change == "duplicate":
        entries[-1] = entries[0]
    elif change == "reverse":
        entries.reverse()
    else:
        entries.append(entries[0])
    repin_inventory(archive, monkeypatch)
    with pytest.raises(dataset.DatasetIntegrityError, match="source season"):
        dataset.load_rows(archive.model)
    assert not archive.calls


def test_2025_reconciliation_cannot_be_silently_removed(archive, monkeypatch):
    archive.inventory["source_reconciliation"]["current_api_asset_id"] = -1
    repin_inventory(archive, monkeypatch)
    with pytest.raises(dataset.DatasetIntegrityError, match="Unreconciled"):
        dataset.load_rows(archive.model)
    assert not archive.calls


def test_symlink_body_rejected_before_row_read(archive):
    path = archive.repo / archive.sources[-1]["body_path"]
    path.unlink()
    path.symlink_to(archive.repo / archive.sources[0]["body_path"])
    with pytest.raises(dataset.DatasetIntegrityError, match="symlink"):
        dataset.load_rows(archive.model)
    assert not archive.calls


def test_all_schedule_paths_checked_before_any_row_read(archive, monkeypatch):
    path = archive.model / "data/raw/sportsdataverse/cfb_schedule_2025.parquet"
    path.unlink()
    monkeypatch.setattr(pd, "read_parquet", lambda *a, **k: pytest.fail("All schedules verified first"))
    with pytest.raises(dataset.DatasetIntegrityError, match="Missing source"):
        dataset.load_rows(archive.model)


def test_missing_schedule_status_is_not_read_from_other_columns(archive):
    path = archive.model / "data/raw/sportsdataverse/cfb_schedule_2019.parquet"
    pd.read_parquet(path).drop(columns="status").to_parquet(path, index=False)
    with pytest.raises(ArrowInvalid):
        dataset.load_rows(archive.model)
    assert not archive.calls


def test_wrong_schedule_season_rejected(archive):
    path = archive.model / "data/raw/sportsdataverse/cfb_schedule_2019.parquet"
    frame = pd.read_parquet(path)
    frame["season"] = 2020
    frame.to_parquet(path, index=False)
    with pytest.raises(dataset.DatasetIntegrityError, match="Schedule contains rows"):
        dataset.load_rows(archive.model)
    assert not archive.calls


def test_nullable_pbp_season_missing_rejected(archive, monkeypatch):
    read = pd.read_parquet
    def wrong(path, **kwargs):
        frame = read(path, **kwargs)
        if "play_by_play_2019" in str(path):
            frame.loc[0, "season"] = pd.NA
        return frame
    monkeypatch.setattr(pd, "read_parquet", wrong)
    with pytest.raises(dataset.DatasetIntegrityError, match="PBP contains rows"):
        dataset.load_rows(archive.model)


def test_parser_cannot_expand_or_duplicate_game_play_identity(archive):
    original = archive.parser.prepare_rows
    def duplicate(raw, schedules):
        rows, coverage = original(raw, schedules)
        return pd.concat([rows, rows], ignore_index=True), coverage
    archive.parser.prepare_rows = duplicate
    with pytest.raises(dataset.DatasetIntegrityError, match="Duplicate game/play"):
        dataset.load_rows(archive.model)


def test_conflicting_game_identity_across_seasons_rejected(archive):
    original = archive.parser.prepare_rows
    def repeated_game(raw, schedules):
        rows, coverage = original(raw, schedules)
        rows["game_id"] = 9001
        return rows, coverage
    archive.parser.prepare_rows = repeated_game
    with pytest.raises(dataset.DatasetIntegrityError, match="multiple seasons"):
        dataset.load_rows(archive.model)


def test_root_convention_explicit(archive):
    with pytest.raises(dataset.DatasetIntegrityError, match="model directory"):
        dataset.load_rows(archive.repo)


def test_committed_report_guard_with_synthetic_git(tmp_path):
    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    git("init")
    git("config", "user.name", "Synthetic Test")
    git("config", "user.email", "test@example.invalid")
    path = tmp_path / dataset.INVENTORY_PATH
    expected = write_json(path, {"synthetic": True})
    with pytest.raises(dataset.DatasetIntegrityError, match="must be committed"):
        dataset._committed(tmp_path.resolve(), dataset.INVENTORY_PATH, expected)
    git("add", dataset.INVENTORY_PATH)
    git("commit", "-m", "Freeze synthetic inventory")
    dataset._committed(tmp_path.resolve(), dataset.INVENTORY_PATH, expected)
    path.write_text("changed")
    with pytest.raises(dataset.DatasetIntegrityError, match="pinned committed"):
        dataset._committed(tmp_path.resolve(), dataset.INVENTORY_PATH, expected)
