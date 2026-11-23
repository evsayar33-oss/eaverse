"""Mocked integrity tests for real-archive ingestion, never synthetic alpha claims."""
import calendar
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from eaverse import pit_binance_pilot


def monthly_archive(month):
    year, index = map(int, month.split("-"))
    rows = []
    for day in range(1, calendar.monthrange(year, index)[1] + 1):
        midnight = datetime(year, index, day, tzinfo=timezone.utc)
        stamp = int(midnight.timestamp() * 1000)
        # Published historical archives use both millisecond and microsecond epochs.
        scale = 1 if year == 2024 else 1000
        opening = stamp * scale
        closing = (stamp + 86_400_000 - 1) * scale
        rows.append(",".join(map(str, [
            opening, 100, 103, 99, 101, 100, closing, 10100, 10, 75, 7575, 0,
        ])))
    csv_content = ("\n".join(rows) + "\n").encode("utf-8")
    filename = f"BTCUSDT-1d-{month}"
    with io.BytesIO() as buffer:
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(filename + ".csv", csv_content)
        zipped = buffer.getvalue()
    checksum = f"{hashlib.sha256(zipped).hexdigest()}  {filename}.zip\n".encode("ascii")
    return zipped, checksum


class BinancePilotTests(unittest.TestCase):
    def test_full_checksum_verified_history_enters_real_eaverse_replay(self):
        remote = {}
        for month in pit_binance_pilot.MONTHS:
            zipped, checksum = monthly_archive(month)
            filename = f"BTCUSDT-1d-{month}.zip"
            url = f"{pit_binance_pilot.BASE_URL}/BTCUSDT/1d/{filename}"
            remote[url], remote[url + ".CHECKSUM"] = zipped, checksum
        def downloader(url, limit):
            content = remote[url]
            self.assertLessEqual(len(content), limit)
            return content
        with tempfile.TemporaryDirectory() as folder:
            result = pit_binance_pilot.collect(
                root=Path(folder), fetcher=downloader,
                retrieved_utc="2026-11-23T08:00:00Z",
            )
            self.assertEqual(result["archive_count"], 24)
            self.assertEqual(result["verified_zip_checksums"], 24)
            self.assertEqual(result["bars"], 731)
            self.assertGreater(result["events"], 600)
            self.assertEqual(result["variants"], 6)
            self.assertEqual(result["source_id"], "BINANCE_SPOT_VISION_TAKER_BUY_BASE_VOLUME")
            self.assertFalse(result["paper_eligible"])
            self.assertFalse(result["forward_evidence"])
            manifest = json.loads((Path(folder) / "manifest.json").read_text())
            self.assertFalse(manifest["historical_revisions_asof_original_publication_verified"])
            self.assertEqual(len(manifest["archive_provenance"]), 24)

    def test_archive_checksum_and_zip_bomb_are_fail_closed(self):
        zipped, checksum = monthly_archive("2024-01")
        with self.assertRaisesRegex(ValueError, "SHA256"):
            pit_binance_pilot.unpack_verified(
                zipped + b"corrupt", checksum, "BTCUSDT-1d-2024-01.zip")
        with self.assertRaisesRegex(ValueError, "SHA256"):
            pit_binance_pilot.unpack_verified(
                zipped, b"unverified", "BTCUSDT-1d-2024-01.zip")
        zip_name = "BTCUSDT-1d-2024-01.zip"
        with io.BytesIO() as buffer:
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("wrong.csv", b"oops")
            data = buffer.getvalue()
        signed = f"{hashlib.sha256(data).hexdigest()}  {zip_name}".encode()
        with self.assertRaisesRegex(ValueError, "archive contents"):
            pit_binance_pilot.unpack_verified(data, signed, zip_name)

    def test_checksum_failure_cannot_write_research_package(self):
        def corrupt(url, limit):
            if url.endswith(".CHECKSUM"):
                return b"missing"
            return b"not-a-zip"
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaisesRegex(ValueError, "SHA256"):
                pit_binance_pilot.collect(root=root, fetcher=corrupt)
            self.assertFalse((root / "manifest.json").exists())

    def test_unsupported_symbol_and_cherry_picked_months_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "symbol"):
                pit_binance_pilot.collect(root=Path(folder), symbol="DOGEUSDT")
            with self.assertRaisesRegex(ValueError, "months"):
                pit_binance_pilot.collect(root=Path(folder), months=("2025-06", "2024-01"))


if __name__ == "__main__":
    unittest.main()
