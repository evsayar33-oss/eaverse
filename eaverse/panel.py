"""Publish and read a small public projection of the committed PAPER ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen

from . import checkpoint
from .delivery import gh


REPO = "evsayar33-oss/eaverse"
RELEASE_URL = f"https://github.com/{REPO}/releases/tag/{checkpoint.TAG}"
API = f"https://api.github.com/repos/{REPO}/releases/tags/{checkpoint.TAG}"
ASSET = re.compile(r"paper-panel-([0-9]{12})-([0-9]{4})-([0-9a-f]{64})\.json")
MAX_BYTES = 16 * 1024 * 1024


def panel_assets(release):
    return sorted(
        (item for item in release["assets"]
         if item.get("state", "uploaded") == "uploaded" and ASSET.fullmatch(item["name"])),
        key=lambda item: item["name"], reverse=True,
    )


def projection(db_path, report, run_url):
    assets = report.get("assets", {})
    entries = []
    with sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        for row in db.execute("""
            SELECT paper.*, paper_progress.metrics
            FROM paper LEFT JOIN paper_progress USING(sequence)
            ORDER BY paper.sequence DESC
        """):
            entry = {key: row[key] for key in ("sequence", "asset", "status", "start", "cursor", "cutoff", "cost")}
            entry.update({
                "spec": json.loads(row["spec"]), "policy": json.loads(row["policy"]),
                "origin": json.loads(row["origin"]), "progress": json.loads(row["metrics"]) if row["metrics"] else None,
                "result": json.loads(row["result"]) if row["result"] else None,
                "data_paused": row["status"] == "active" and bool(assets.get(row["asset"], {}).get("error")),
            })
            entries.append(entry)
    return {
        "schema": 1, "published_at": report["time"], "run_url": run_url,
        "entries": entries,
    }


def publish(db, report_path):
    report = json.loads(Path(report_path).read_text())
    payload = projection(db, report, os.environ["EAVERSE_EVIDENCE_URL"])
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    name = f"paper-panel-{int(os.environ['GITHUB_RUN_NUMBER']):012}-{int(os.environ['GITHUB_RUN_ATTEMPT']):04}-{digest}.json"
    if len(encoded) > MAX_BYTES:
        raise ValueError("PAPER panel data exceeds its size limit")
    repo = checkpoint.repository()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / name
        path.write_bytes(encoded)
        items = panel_assets(checkpoint.release())
        for attempt in range(4):
            current = next((item for item in items if item["name"] == name), None)
            if current:
                if current.get("digest") and current["digest"] != f"sha256:{digest}":
                    raise ValueError("PAPER panel checksum mismatch")
                break
            if attempt == 3:
                raise RuntimeError("PAPER panel upload could not be confirmed")
            try:
                gh("release", "upload", checkpoint.TAG, str(path), "-R", repo)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                time.sleep(2 ** attempt)
            items = panel_assets(checkpoint.release())
    for old in items[3:]:
        gh("api", "--method", "DELETE", f"repos/{repo}/releases/assets/{old['id']}")


def read_url(url):
    request = Request(url, headers={"User-Agent": "eaverse-paper-panel/1.0", "Accept": "application/json"})
    with urlopen(request, timeout=12) as response:
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError("PAPER panel data exceeds its size limit")
    return body


def read_public():
    available = panel_assets(json.loads(read_url(API)))
    if not available:
        raise ValueError("no PAPER panel has been published")
    for item in available[:3]:
        name = item["name"]
        url = f"https://github.com/{REPO}/releases/download/{checkpoint.TAG}/{name}"
        try:
            content = read_url(url)
        except OSError:
            continue
        expected = ASSET.fullmatch(name).group(3)
        if hashlib.sha256(content).hexdigest() == expected:
            payload = json.loads(content)
            if payload.get("schema") == 1 and isinstance(payload.get("entries"), list):
                return payload
    raise ValueError("PAPER snapshot could not be downloaded or validated")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="runtime/research.sqlite")
    parser.add_argument("--report", default="runtime/report.json")
    args = parser.parse_args()
    publish(args.db, args.report)
