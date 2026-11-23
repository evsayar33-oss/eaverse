"""Download and verify a bounded historical Binance 1d archive for Eaverse research.

NO trading; spot BTCUSDT venue only; original ZIPs are never published to GitHub.
The resulting retrospective experiment cannot establish prospective alpha.
"""
from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import io
import json
import re
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import pit_adapters, pit_research

BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"
SOURCE_TERMS = "https://github.com/binance/binance-public-data/blob/master/TERMS_AND_CONDITIONS.md"
SYMBOLS = {"BTCUSDT"}  # deliberately frozen small scope, no hindsight universe expansion
MONTHS = tuple(f"{year}-{month:02d}" for year in (2024, 2025) for month in range(1, 13))
MAX_ZIP_BYTES = 4_000_000
MAX_CSV_BYTES = 8_000_000
MAX_CHECKSUM_BYTES = 1024


def fetch_bytes(url: str, limit: int) -> bytes:
    if not url.startswith(BASE_URL + "/"):
        raise ValueError("unauthorized historical download host/path")
    request = Request(url, headers={"User-Agent": "eaverse-noncommercial-research/1.0"})
    failure = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=18) as response:
                raw = response.read(limit + 1)
            if len(raw) > limit:
                raise ValueError("remote archive exceeds strict size cap")
            return raw
        except (HTTPError, URLError, TimeoutError) as exc:
            failure = exc
            if isinstance(exc, HTTPError) and exc.code not in {429, 500, 502, 503, 504}:
                break
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"historical archive retrieval failed: {type(failure).__name__}") from failure


def unpack_verified(zipped: bytes, checksum_bytes: bytes, zip_name: str) -> bytes:
    """Require official SHA-256 of compressed ZIP and the expected single CSV."""
    if len(zipped) > MAX_ZIP_BYTES or len(checksum_bytes) > MAX_CHECKSUM_BYTES:
        raise ValueError("historical archive/checksum too large")
    try:
        check = checksum_bytes.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise ValueError("checksum is not ASCII") from exc
    matched = re.fullmatch(r"([a-fA-F0-9]{64})\s+\*?" + re.escape(zip_name), check)
    if not matched or hashlib.sha256(zipped).hexdigest().lower() != matched[1].lower():
        raise ValueError("official Binance archive SHA256 verification failed")
    expected_csv = zip_name.removesuffix(".zip") + ".csv"
    try:
        with zipfile.ZipFile(io.BytesIO(zipped)) as zf:
            members = zf.infolist()
            if len(members) != 1 or members[0].filename != expected_csv:
                raise ValueError("archive contents differ from expected venue file")
            if members[0].file_size > MAX_CSV_BYTES or members[0].is_dir():
                raise ValueError("uncompressed archive exceeds safe limit")
            if zf.testzip() is not None:
                raise ValueError("archive failed CRC integrity test")
            output = zf.read(members[0])
            if len(output) > MAX_CSV_BYTES:
                raise ValueError("extracted archive exceeds safe limit")
            return output
    except zipfile.BadZipFile as exc:
        raise ValueError("invalid Binance ZIP") from exc


def collect(*, root: Path, symbol: str = "BTCUSDT", months=MONTHS,
            fetcher=fetch_bytes, retrieved_utc: str | None = None) -> dict:
    if symbol not in SYMBOLS:
        raise ValueError("pilot symbol must remain frozen")
    if not months or len(set(months)) != len(months) or tuple(months) != tuple(sorted(months)):
        raise ValueError("months must be unique and chronological")
    if any(month not in MONTHS for month in months):
        raise ValueError("pilot period cannot be extended without new experiment ID")
    packages, provenance = [], []
    for month in months:
        zip_name = f"{symbol}-1d-{month}.zip"
        url = f"{BASE_URL}/{symbol}/1d/{zip_name}"
        zipped = fetcher(url, MAX_ZIP_BYTES)
        checksum = fetcher(url + ".CHECKSUM", MAX_CHECKSUM_BYTES)
        extracted = unpack_verified(zipped, checksum, zip_name)
        packages.append(extracted)
        provenance.append({
            "month": month, "venue": "BINANCE_SPOT",
            "url": url,
            "zip_sha256": hashlib.sha256(zipped).hexdigest(),
            "csv_sha256": hashlib.sha256(extracted).hexdigest(),
            "checksum_verified": True,
        })
    retrieved = (pit_research.utc(retrieved_utc) if retrieved_utc
                 else datetime.now(timezone.utc)).isoformat()
    all_bars, all_events = [], []
    for package in packages:
        bars, events = pit_adapters.binance_daily_klines(
            package, symbol=symbol, retrieved_utc=retrieved,
        )
        all_bars.extend(bars)
        all_events.extend(events)
    if len(all_bars) < 600:
        raise ValueError("insufficient genuine historical daily bars; no replay")
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["date", "open", "high", "low", "close", "volume"],
                            lineterminator="\n")
    writer.writeheader()
    writer.writerows(all_bars)
    bars_bytes = output.getvalue().encode("utf-8")
    event_bytes = pit_adapters.event_jsonl(all_events)
    manifest = pit_adapters.make_manifest(
        market="CRYPTO", asset=f"CRYPTO:BINANCE_SPOT:{symbol}",
        price_source_id="BINANCE_SPOT_VISION_1D_KLINE",
        source_id="BINANCE_SPOT_VISION_TAKER_BUY_BASE_VOLUME",
        bars_csv=bars_bytes, events_jsonl=event_bytes,
        retrieved_utc=retrieved, rights_status="PERMITTED_PERSONAL_NONCOMMERCIAL",
        cost_bps=35,
    )
    manifest.update({
        "archive_provenance": provenance,
        "terms_url": SOURCE_TERMS, "research_use": "PERSONAL_NONCOMMERCIAL_ONLY",
        "historical_revisions_asof_original_publication_verified": False,
        "actual_trade_level_cvd_verified": False,
        "historical_l2_verified": False,
        "cross_venue_oos_verified": False,
    })
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "bars.csv").write_bytes(bars_bytes)
    (root / "events.jsonl").write_bytes(event_bytes)
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result = pit_research.replay(manifest_path)
    result.update({
        "archive_count": len(provenance),
        "verified_zip_checksums": len(provenance),
        "rights_basis": SOURCE_TERMS,
        "retrospective_archive_revisions_unverified": True,
        "research_only": True,
    })
    (root / "report.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n",
                                      encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", choices=sorted(SYMBOLS), default="BTCUSDT")
    parser.add_argument("--out-root", type=Path, default=Path("out/binance_pit"))
    args = parser.parse_args()
    report = collect(root=args.out_root, symbol=args.symbol)
    print(f"Verified real archives={report['verified_zip_checksums']} "
          f"bars={report['bars']} events={report['events']} "
          f"retrospective variants={report['variants']}; NO PAPER/LIVE APPROVAL")


if __name__ == "__main__":
    main()
