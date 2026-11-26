import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from eaverse import archive, combination_research as cr, coverage, data, engine, events, foundry, research
from test_research import market_frame


def insert(db, asset, identity, version, d, v=None, spec="{}", padding=""):
    d = {**d, "large_detail": padding}
    row = (version, asset, identity, "coverage:1d:parameter", spec, "source", "snapshot", "2026-11-26",
           "code", "{}", research.canonical(d), research.canonical(v) if v is not None else None)
    db.execute("INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", row)


class ArchiveOutcomeTests(unittest.TestCase):
    def test_early_compaction_labels_actual_negative_not_research_only_flag(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "db.sqlite"
            with engine.connect(path) as db:
                spec = research.canonical(cr.plan("US:A", "1d", "single", 0))
                identity = research.digest([cr.VERSION, json.loads(spec)])
                negative = {"net": -.2, "entries": 2, "status": "evaluated"}
                positive = {"net": .3, "entries": 2, "status": "evaluated"}
                insert(db, "US:A", identity, cr.VERSION, {"pass": False, "metrics": negative},
                       {"metrics": negative}, spec, "x" * 300000)
                insert(db, "US:B", identity, cr.VERSION, {"pass": False, "metrics": positive},
                       {"metrics": positive}, spec)
                insert(db, "US:A", "gate", research.VERSION, {"pass": False, "net": -.1})
                insert(db, "US:A", "no_entries", cr.VERSION, {"pass": False, "metrics": positive},
                       {"metrics": {"status": "no_entries", "net": 0, "entries": 0}})
                insert(db, "US:A", "invalid", cr.VERSION, {"pass": False, "metrics": negative,
                    "superlab_receipt": {"status": "INVALID_EVIDENCE"}}, {"metrics": negative})
                db.commit()
                before = path.stat().st_size
                archived = []
                def upload(path, sha, tag):
                    self.assertEqual(archive.file_digest(path), sha)
                    archived.extend(json.loads(s) for s in gzip.decompress(path.read_bytes()).splitlines())
                    return "https://example.org/verified"
                with patch.object(archive, "upload", side_effect=upload):
                    self.assertIsNotNone(archive.compact(db, limit=100, failed_batch=2))
                self.assertEqual(len(archived), 3)
                self.assertLess(path.stat().st_size, before)
                self.assertEqual(db.execute("SELECT count(*) FROM attempts").fetchone()[0], 5)
                self.assertEqual(db.execute("SELECT policy FROM attempts WHERE asset='US:B'").fetchone()[0], "{}")
                self.assertEqual(db.execute("SELECT policy FROM attempts WHERE id='invalid'").fetchone()[0], "{}")
                row = db.execute("SELECT * FROM attempts WHERE asset='US:A' AND id=?", (identity,)).fetchone()
                summary = json.loads(row["discovered"])
                self.assertEqual(summary["outcome"], "historical_loss")
                self.assertEqual(summary["validation_metrics"]["net"], -.2)
                self.assertEqual(row["snapshot"], "snapshot")
                self.assertEqual(row["asof"], "2026-11-26")
                self.assertIn("large_detail", json.loads(archived[0]["row"]["discovered"]))
                counts = archive.outcome_counts(db)
                self.assertEqual((counts["archived"], counts["negative"], counts["no_trade_evidence"]), (3, 2, 1))
                with patch.object(foundry, "execute", side_effect=AssertionError("duplicate backtest")):
                    status, same_id = cr.evaluate(db, "US:A", data.MARKETS[0], market_frame(800),
                                                json.loads(spec), "single", {}, {})
                self.assertEqual((status, same_id), ("covered_existing", identity))
                status, other_id = cr.evaluate(db, "US:C", data.MARKETS[0], market_frame(2200),
                                                json.loads(spec), "single", {}, {})
                self.assertEqual((status, other_id), ("evaluated", identity))
                self.assertEqual(db.execute("SELECT count(*) FROM attempts WHERE id=?", (identity,)).fetchone()[0],3)

    def test_byte_threshold_preserves_foundry_parent_inputs_and_events(self):
        with engine.connect(":memory:") as db:
            metrics = {"net": .2, "drawdown": .1, "entries": 3, "status": "evaluated"}
            insert(db, "A", "parent", foundry.VERSION, {"pass": False, "metrics": metrics},
                   {"metrics": {"net": -.3, "entries": 2}}, '{"rules":[]}', "A"*20000)
            db.execute("INSERT INTO event_trials VALUES (?,?,?,?,?,?,?)",
                       (events.STUDY_VERSION, "A", "event", "snapshot", "today", "calendar",
                        research.canonical({"quarter":"2026-Q4","discovery":{"net":.1,"trades":2},
                                            "recent":{"net":-.3,"trades":2}})))
            with patch.object(archive, "upload", return_value="verified"):
                archive.compact(db, limit=100, byte_limit=10000)
                archive.compact(db, limit=1)
            r = db.execute("SELECT * FROM attempts").fetchone()
            self.assertEqual(r["spec"], '{"rules":[]}')
            self.assertEqual(json.loads(r["discovered"])["metrics"], metrics)
            event = db.execute("SELECT * FROM event_trials").fetchone()
            self.assertEqual(event["calendar"], "")
            self.assertEqual(json.loads(event["summary"])["outcome"], "historical_loss")
            self.assertEqual(json.loads(event["summary"])["calendar_sha256"], "calendar")
            self.assertEqual(archive.outcome_counts(db)["negative"], 2)

    def test_event_same_quarter_not_repeated_after_archiving(self):
        bars = market_frame(750)
        dates = {"US_CPI":[day.date().isoformat() for day in bars.index[::15]]}
        with engine.connect(":memory:") as db:
            first, _ = events.evaluate(db, "A", bars, dates, "today", "snapshot", 35, 8)
            self.assertEqual(first["new_trials"],8)
            with patch.object(archive,"upload",return_value="verified"):
                archive.compact(db,limit=8)
            self.assertEqual(db.execute("SELECT count(*) FROM event_trials WHERE calendar=''").fetchone()[0],8)
            second, _ = events.evaluate(db, "A", bars, dates, "today", "snapshot", 35, 8)
            self.assertFalse({x["id"] for x in first["trials"]}&{x["id"] for x in second["trials"]})

    def test_legacy_backfill_verifies_hash_and_does_not_resurrect_full_payload(self):
        with engine.connect(":memory:") as db:
            insert(db, "A", "old", cr.VERSION, {"pass":False, "metrics":{"net":-.1}}, {"metrics":{"net":-.2}})
            row = dict(db.execute("SELECT * FROM attempts").fetchone())
            content = gzip.compress((research.canonical({"type":"technical","row":row})+"\n").encode(),mtime=0)
            import hashlib
            sha = hashlib.sha256(content).hexdigest()
            name = f"research-{sha}.jsonl.gz"
            url = f"https://github.com/owner/repo/releases/download/research-history-2026-11/{name}"
            db.execute("INSERT INTO history_archives VALUES (?,?,?)", (sha,url,1))
            db.execute("UPDATE attempts SET spec='',policy='',source='',snapshot='',asof='',code='',validated=NULL,discovered=?",
                      (research.canonical({"pass":False,"archive":sha}),))
            db.commit()
            self.assertEqual(archive.outcome_counts(db)["unknown"], 1)
            def download(*args):
                (Path(args[-1]) / name).write_bytes(content)
            with patch.dict("os.environ", {"GITHUB_REPOSITORY":"owner/repo"}):
                with patch.object(archive,"gh",side_effect=lambda *a:(Path(a[-1])/name).write_bytes(b"corrupt")):
                    with self.assertRaisesRegex(ValueError,"checksum"):
                        archive.backfill_one(db)
                self.assertEqual(archive.outcome_counts(db)["unknown"],1)
                with patch.object(archive,"gh",side_effect=download):
                    self.assertEqual(archive.backfill_one(db),sha)
                    self.assertIsNone(archive.backfill_one(db))
            saved = db.execute("SELECT * FROM attempts").fetchone()
            self.assertEqual(saved["spec"],"")
            self.assertEqual(saved["snapshot"],"snapshot")
            self.assertEqual(json.loads(saved["discovered"])["outcome"],"historical_loss")
            self.assertEqual(archive.outcome_counts(db)["negative"],1)

    def test_legacy_coverage_cursor_recovery_does_not_repeat_archived_test(self):
        with engine.connect(":memory:") as db:
            spec = coverage.plan("1d", "single", 0)
            ident = research.digest([coverage.VERSION, spec])
            insert(db, "A", ident, coverage.VERSION,
                   {"pass":False,"metrics":{"net":-.1}}, {"metrics":{"net":-.2}})
            with patch.object(archive, "upload", return_value="verified"):
                archive.compact(db, limit=1)
            with patch.object(coverage, "FRAMES", ("1d",)), patch.object(coverage, "LANES", ("single",)), patch.object(
                coverage, "eligible", side_effect=AssertionError("repeated eligibility/backtest")):
                out = coverage.run_asset(db, "A", data.MARKETS[0], market_frame(800), {}, {})
            self.assertEqual(out["1d:single"]["status"], "duplicate_not_counted")
            self.assertEqual(out["1d:single"]["id"], ident)
            self.assertEqual(db.execute("SELECT count(*) FROM attempts").fetchone()[0],1)

    def test_upload_searches_past_first_page_and_requires_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"record.gz"
            path.write_bytes(b"evidence")
            sha=archive.file_digest(path)
            others=[{"name":str(i)} for i in range(100)]
            match={"name":path.name,"digest":f"sha256:{sha}","size":path.stat().st_size,
                   "browser_download_url":"verified","state":"uploaded"}
            with patch.dict("os.environ",{"GITHUB_REPOSITORY":"owner/repo"}), patch.object(
                archive,"gh",side_effect=[json.dumps({"id":1}),json.dumps(others),json.dumps([match])]) as gh:
                self.assertEqual(archive.upload(path,sha,"tag"),"verified")
                self.assertIn("page=2",gh.call_args.args[1])
