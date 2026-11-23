import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from eaverse import causal_discovery as c


class CausalDiscoveryTests(unittest.TestCase):
    def test_real_source_council_and_registered_designs(self):
        c.validate()
        report=c.report()
        self.assertEqual(report["by_market"],{"CRYPTO":72,"US":56,"BIST":20})
        self.assertEqual(len(report["designs"]),148)
        self.assertEqual(len({x["id"] for x in report["designs"]}),148)
        self.assertEqual(report["new_backtests_completed"],0)
        self.assertEqual(report["new_paper_approvals"],0)
        self.assertFalse(report["live_authorized"])

    def test_all_candidates_are_explicitly_unvalidated(self):
        for x in c.designs():
            self.assertFalse(x["history_backtest_run"])
            self.assertFalse(x["pit_validated"])
            self.assertFalse(x["out_of_sample_validated"])
            self.assertFalse(x["prospective_validated"])
            self.assertFalse(x["paper_eligible"])
            self.assertFalse(x["live_allowed"])
            self.assertTrue(x["test_contract"]["pre_register_before_outcome_inspection"])
            self.assertIn("first_seen",x["test_contract"]["prospective"])

    def test_causal_pairs_are_trigger_plus_distinct_information(self):
        for market,pairs in c.ALLOWED.items():
            for trigger,other in pairs:
                self.assertEqual(c.CHANNELS[market][trigger][0],"trigger")
                self.assertNotEqual(c.CHANNELS[market][other][0],"trigger")
                self.assertNotEqual(c.CHANNELS[market][trigger][1],
                                    c.CHANNELS[market][other][1])
        self.assertEqual(len([x for x in c.designs()
                              if len(x["event_features"])==2]),84)

    def test_source_unavailable_is_not_silently_promoted(self):
        us=next(x for x in c.designs() if x["market"]=="US" and
                "sec_filing_shock" in x["event_features"])
        self.assertIn("NEEDS_PIT_PROOF",us["data_readiness"][0])
        crypto=next(x for x in c.designs() if "funding_dislocation" in x["event_features"])
        self.assertIn("UNVERIFIED",crypto["data_readiness"][0])

    def test_bad_role_or_unknown_source_is_rejected(self):
        bad=copy.deepcopy(c.CHANNELS)
        bad["US"]["sec_filing_shock"]=("context","issuer_filing_acceptance",
                                       "SEC_EDGAR_SUBMISSIONS")
        with patch.object(c,"CHANNELS",bad):
            with self.assertRaisesRegex(ValueError,"CAUSAL_PAIR"):
                c.validate()
        bad=copy.deepcopy(c.CHANNELS)
        bad["CRYPTO"]["aggressor_imbalance"]=("trigger","spot_taker_base_volume",
                                               "UNKNOWN_VENDOR")
        with patch.object(c,"CHANNELS",bad):
            with self.assertRaisesRegex(ValueError,"MISMATCHED_SOURCE"):
                c.validate()

    def test_deterministic_queue_and_report(self):
        first=c.report()
        self.assertEqual(first,c.report())
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)
            with patch("sys.argv",["causal_discovery","--out",str(output)]):
                c.main()
            self.assertEqual(json.loads((output/"queue.json").read_text()),first)


if __name__=="__main__":
    unittest.main()
