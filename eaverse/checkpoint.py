"""Keep durable SQLite state outside the Git history using release assets."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from .delivery import gh


TAG = "research-state-v1"
NAME = re.compile(r"state-([0-9]{20})-([0-9a-f]{64})\.sqlite\.gz")
LIMIT = 768 * 1024 * 1024


def file_digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def repository():
    return os.environ["GITHUB_REPOSITORY"]


def release():
    return json.loads(gh("api", f"repos/{repository()}/releases/tags/{TAG}"))


def assets():
    release_id = release()["id"]
    items = json.loads(gh("api", f"repos/{repository()}/releases/{release_id}/assets?per_page=100"))
    return sorted((item for item in items if NAME.fullmatch(item["name"])),
                  key=lambda item: item["name"], reverse=True)


def init_release():
    gh("release", "create", TAG, "-R", repository(), "--target", os.environ["GITHUB_SHA"],
       "--title", "Research state", "--notes",
       "Rotating SQLite checkpoints. Contains simulated results and public market identifiers only.", "--latest=false")


def restore(path):
    # The bootstrap variable is enabled only for the explicit first run. A missing
    # established checkpoint stops the job instead of discarding the audit ledger.
    bootstrap = os.getenv("EAVERSE_BOOTSTRAP") == "true"
    try:
        items = assets()
    except subprocess.CalledProcessError as exc:
        if not bootstrap or "(HTTP 404)" not in getattr(exc, "stderr", ""):
            raise
        init_release()
        items = assets()
    if not items:
        if not bootstrap:
            raise RuntimeError("research checkpoint is missing; restore it before running discovery")
        return
    if path.exists():
        raise FileExistsError(path)
    newest = items[0]["name"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=path.parent) as temp:
        archive = Path(temp) / newest
        gh("release", "download", TAG, "-R", repository(), "--pattern", newest, "--dir", temp)
        expected = NAME.fullmatch(newest).group(2)
        if archive.stat().st_size > LIMIT or file_digest(archive) != expected:
            raise ValueError("checkpoint size or hash mismatch")
        db_path = Path(temp) / "restored.sqlite"
        with gzip.open(archive, "rb") as compressed, db_path.open("wb") as out:
            while block := compressed.read(1024 * 1024):
                if out.tell() + len(block) > LIMIT:
                    raise ValueError("checkpoint exceeds the uncompressed size limit")
                out.write(block)
        with sqlite3.connect(db_path) as db:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("SQLite checkpoint is corrupt")
        db_path.replace(path)


def save(path):
    if not path.is_file() or path.stat().st_size > LIMIT:
        raise ValueError("checkpoint missing or 768 MiB ledger limit reached")
    items = assets()
    generation = int(NAME.fullmatch(items[0]["name"]).group(1)) + 1 if items else 1
    with tempfile.TemporaryDirectory() as temp:
        archive = Path(temp) / "state"
        with path.open("rb") as source, archive.open("wb") as target:
            with gzip.GzipFile(fileobj=target, mode="wb", mtime=0, filename="") as compressed:
                shutil.copyfileobj(source, compressed)
        sha = file_digest(archive)
        if items and NAME.fullmatch(items[0]["name"]).group(2) == sha:
            return
        asset_path = archive.rename(archive.with_name(f"state-{generation:020}-{sha}.sqlite.gz"))
        gh("release", "upload", TAG, str(asset_path), "-R", repository())
    newest = assets()
    if not newest or newest[0]["name"] != asset_path.name:
        raise RuntimeError("checkpoint upload was not confirmed")
    for old in newest[3:]:
        gh("api", "--method", "DELETE", f"repos/{repository()}/releases/assets/{old['id']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["restore", "save"])
    parser.add_argument("--db", type=Path, default=Path("runtime/research.sqlite"))
    args = parser.parse_args()
    {"restore": restore, "save": save}[args.action](args.db)
