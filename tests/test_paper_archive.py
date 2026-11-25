import unittest
from eaverse import data, engine, paper_archive, research
from test_research import market_frame


class PaperArchiveTests(unittest.TestCase):
    def test_exact_append_and_no_rewrite(self):
        bars=market_frame(500)
        with engine.connect(":memory:") as db:
            db.execute("""INSERT INTO paper(asset,spec,cost,start,anchor,cutoff,digest,version,policy,origin,status)
                       VALUES(?,?,?,?,?,?,?,?,?,?,'active')""",
                       ("CRYPTO:BTC-USD","{}",35,engine.date(bars.index[401]),engine.date(bars.index[0]),
                        engine.date(bars.index[399]),data.fingerprint(bars.iloc[:400]),research.VERSION,
                        research.canonical(research.POLICY),'{"source":"coinbase"}'))
            row=db.execute("SELECT * FROM paper").fetchone()
            paper_archive.capture(db,row,bars.iloc[:400])
            paper_archive.capture(db,row,bars.iloc[:450])
            paper_archive.capture(db,row,bars.iloc[:450])
            self.assertEqual(db.execute("SELECT count(*) FROM paper_candle_archive").fetchone()[0],2)
            frame,source=paper_archive.read_prefix(db,row["sequence"])
            self.assertEqual(source,"coinbase")
            self.assertEqual(data.fingerprint(frame),data.fingerprint(bars.iloc[:450]))
            self.assertTrue((frame.to_numpy()==bars.iloc[:450].to_numpy()).all())
            revised=bars.iloc[:450].copy()
            revised.iloc[410,0]*=1.01
            with self.assertRaisesRegex(ValueError,"conflicts"):
                paper_archive.capture(db,row,revised)
            self.assertEqual(db.execute("SELECT count(*) FROM paper_candle_archive").fetchone()[0],2)
            db.execute("UPDATE paper_candle_archive SET content_sha256='bad'")
            with self.assertRaisesRegex(ValueError,"digest mismatch"):
                paper_archive.read_prefix(db,row["sequence"])
