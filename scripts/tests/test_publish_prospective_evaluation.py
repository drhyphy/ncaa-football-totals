from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "publish_prospective_evaluation", Path(__file__).parents[1] / "publish_prospective_evaluation.py")
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


def project(tmp_path):
    ledger = tmp_path / "model/ledger"
    ledger.mkdir(parents=True)
    for name in ("positions.json", "weather_positions.json"):
        (ledger / name).write_text("[]\n")
    return tmp_path


def test_interim_publishes_without_creating_final(tmp_path):
    root = project(tmp_path)
    report = publisher.publish(root, publisher.CUTOFF - timedelta(seconds=1))
    assert report["publication"]["frozen"] is False
    assert (root / "site/data/prospective-evaluation.json").is_file()
    assert not (root / "model/ledger" / publisher.FINAL_NAME).exists()


def test_cutoff_freezes_once_and_recovers_public_export_without_regrading(tmp_path):
    root = project(tmp_path)
    report = publisher.publish(root, publisher.CUTOFF)
    frozen = root / "model/ledger" / publisher.FINAL_NAME
    original = frozen.read_bytes()
    assert report["publication"]["frozen"] is True
    # Later corrupted/current inputs cannot silently change the frozen analysis.
    (root / "model/ledger/positions.json").write_text("not the frozen input")
    (root / "site/data/prospective-evaluation.json").unlink()
    later = publisher.publish(root, publisher.CUTOFF + timedelta(days=10))
    assert later == report
    assert frozen.read_bytes() == original
    assert json.loads((root / "site/data/prospective-evaluation.json").read_text()) == report
    assert json.loads((root / "site/data/prospective-evaluation-final.json").read_text()) == report


def test_corrupted_frozen_bundle_fails_without_overwriting(tmp_path):
    root = project(tmp_path)
    publisher.publish(root, publisher.CUTOFF)
    path = root / "model/ledger" / publisher.FINAL_NAME
    bundle = json.loads(path.read_text())
    bundle["inputs"]["positions.json"].append({"tampered": True})
    path.write_text(json.dumps(bundle))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="integrity"):
        publisher.publish(root, publisher.CUTOFF + timedelta(days=1))
    assert path.read_bytes() == before


def test_missing_live_ledger_cannot_freeze_an_empty_cohort(tmp_path):
    root = project(tmp_path)
    (root / "model/ledger/positions.json").unlink()
    with pytest.raises(FileNotFoundError):
        publisher.publish(root, publisher.CUTOFF)
    assert not (root / "model/ledger" / publisher.FINAL_NAME).exists()


def test_self_consistent_interim_bundle_cannot_be_recovered_as_final(tmp_path):
    root = project(tmp_path)
    publisher.publish(root, publisher.CUTOFF)
    path = root / "model/ledger" / publisher.FINAL_NAME
    bundle = json.loads(path.read_text())
    bundle["report"]["stage"] = "interim_descriptive"
    bundle["report_sha256"] = publisher.digest(bundle["report"])
    path.write_text(json.dumps(bundle))
    with pytest.raises(ValueError, match="integrity"):
        publisher.publish(root, publisher.CUTOFF + timedelta(days=1))


def test_atomic_create_never_replaces_existing_bytes(tmp_path):
    path = tmp_path / "final.json"
    publisher.create_once(path, b"first")
    publisher.create_once(path, b"second")
    assert path.read_bytes() == b"first"


def test_naive_publication_clock_rejected(tmp_path):
    with pytest.raises(ValueError, match="timezone"):
        publisher.publish(project(tmp_path), datetime(2027, 2, 8, 12))
