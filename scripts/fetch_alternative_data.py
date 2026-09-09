"""Download pinned public market sources and normalize against public schedules.

No API key is required. Run from any directory with --root <repository root>.
Raw/derived research data remain ignored by Git. Source rights are documented in
ALTERNATIVE_DATA.md; this program downloads data and never executes source code.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import requests

CFBD_PIN = "59f7f0ef813b229894757619901c49855a08ac1a"
CFBD_URL = f"https://raw.githubusercontent.com/jasperfriis-cuni/cfb-market-efficiency/{CFBD_PIN}/raw_data"
PINNED_FILES = [
    ("cfbd_public/lines_2020.json", f"{CFBD_URL}/lines_2020.json", "c83d3c7fde9dbf8191663c9a756fd34df5863351068b1a24152380087703b3e4"),
    ("cfbd_public/lines_2021.json", f"{CFBD_URL}/lines_2021.json", "93ba11d5aaf8d8d97feba252d200d09c9b2fdc00932513ba4dd88205d62e76dc"),
    ("cfbd_public/lines_2022.json", f"{CFBD_URL}/lines_2022.json", "405813c2729bc8ad2ba800bd94775189184554d56f5b04f929971dba2faa5be5"),
    ("cfbd_public/lines_2023.json", f"{CFBD_URL}/lines_2023.json", "5e6e8424d9fe87e387916ab9aec4a9cb57f96db0134d4ec7560b5c688d672d71"),
    ("andrew_training_24_25.csv", "https://raw.githubusercontent.com/andrewrpokorny-source/cfb-analytics/1371e18135e778b41372b860f7045c76027719aa/cfb_training_data_24_25.csv", "31d3315176b1c6bece457a32b7505252d81d44fdcdaff1722d213f105e659c20"),
    ("sdql/ncaafb_2019.csv", "https://raw.githubusercontent.com/jampdx/sdql2/e8783b7f02da6a883904ed2e0bb03d75532e7214/Data/ncaafb_2019.csv", "7fad4df6102aa13bd95588dfc756b0f5a449624c0ef5b38a870c0a20706f2097"),
]
SDV_BASE = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download"


def verify_digest(content: bytes, expected: str | None) -> str:
    actual = hashlib.sha256(content).hexdigest()
    if expected is not None and actual != expected:
        raise ValueError("Downloaded public file does not match its pinned SHA-256")
    return actual


def download(session, url: str, destination: Path, expected: str | None = None) -> dict:
    """Validate before writing; a failed download cannot replace an existing file."""
    if destination.exists():
        digest = verify_digest(destination.read_bytes(), expected)
        return {"path": str(destination), "source_url": url, "sha256": digest, "cached": True}
    response = session.get(url, timeout=60)
    response.raise_for_status()
    digest = verify_digest(response.content, expected)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".download")
    temporary.write_bytes(response.content)
    temporary.replace(destination)
    metadata = {
        "source_url": url,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "sha256": digest,
        "content_type": response.headers.get("content-type"),
        "last_modified": response.headers.get("last-modified"),
        "quote_timestamp": None,
        "quote_time_note": "Retrieval time is not a bookmaker quote timestamp.",
    }
    destination.with_suffix(destination.suffix + ".meta.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return {"path": str(destination), **metadata, "cached": False}


def main(root: Path, with_features: bool = False, download_only: bool = False) -> None:
    root = Path(root).resolve()
    alternative = root / "model/data/raw/alternative"
    sportsdataverse = root / "model/data/raw/sportsdataverse"
    with requests.Session() as session:
        for relative, url, digest in PINNED_FILES:
            print(json.dumps(download(session, url, alternative / relative, digest)), flush=True)
        for year in range(2019, 2026):
            filename = f"cfb_schedule_{year}.parquet"
            print(json.dumps(download(session, f"{SDV_BASE}/espn_cfb_schedules/{filename}", sportsdataverse / filename)), flush=True)
            if with_features:
                if year >= 2023:
                    # Only supplies the audited repair universe. Scalar totals
                    # are never accepted as pregame quotes by these scripts.
                    filename = f"betting_{year}.parquet"
                    print(json.dumps(download(session, f"{SDV_BASE}/espn_cfb_betting/{filename}", sportsdataverse / filename)), flush=True)
                for name, tag in [("drives", "espn_cfb_drives"), ("adv_team_gamelog", "espn_cfb_adv_team_gamelog")]:
                    filename = f"{name}_{year}.parquet"
                    print(json.dumps(download(session, f"{SDV_BASE}/{tag}/{filename}", sportsdataverse / filename)), flush=True)
    if not download_only:
        from normalize_cfbd_public import main as normalize_cfbd
        from normalize_alternative_supplements import main as normalize_supplements
        normalize_cfbd(root)
        normalize_supplements(root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="Repository root, containing model/ and scripts/")
    parser.add_argument("--with-features", action="store_true", help="Also obtain prior outcome drive and team-game inputs for research; no model fitting")
    parser.add_argument("--download-only", action="store_true", help="Verify/download raw files without normalizing")
    args = parser.parse_args()
    main(args.root, args.with_features, args.download_only)
