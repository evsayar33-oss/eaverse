"""Source council has no implicit substitute for OHLCV, venue CVD or SEC issuer vintage."""
import unittest
from eaverse import source_council as council


class SourceCouncilContracts(unittest.TestCase):
    def test_three_markets_and_strict_30_source_provenance(self):
        r=council.report()
        self.assertEqual(r["providers"],30)
        self.assertGreaterEqual(r["by_market"]["CRYPTO"],10)
        self.assertGreaterEqual(r["by_market"]["US"],10)
        self.assertGreaterEqual(r["by_market"]["BIST"],5)
        self.assertEqual(len({x["id"] for x in r["sources"]}),30)
        self.assertFalse(r["all_external_sources_operational"])
        self.assertEqual(r["new_strategies_paper_accepted"],0)

    def test_independent_venue_not_spliced_into_binance_volume(self):
        z=council.fallback(
            "BINANCE_SPOT_VISION","KRAKEN_TRADES",
            purpose="spot_taker_base_volume",same_instrument=True,
            rights_verified=True,same_venue=False,
            original_publication_clock_verified=True)
        self.assertEqual(z["status"],"VENUE_NATIVE_OR_ADJUSTMENT_NOT_INTERCHANGEABLE")
        self.assertFalse(z["replay_same_experiment"])

    def test_same_provider_publication_replacement_still_needs_new_protocol(self):
        z=council.fallback(
            "SEC_EDGAR_SUBMISSIONS","SEC_OLDLOADS",purpose="issuer_filing_acceptance",
            same_instrument=True,rights_verified=True,same_venue=True,
            original_publication_clock_verified=True)
        self.assertEqual(z["status"],"NEW_SOURCE_VERSION_AND_REGRESSION_REVIEW_REQUIRED")
        self.assertFalse(z["replay_same_experiment"])

    def test_new_event_information_requires_real_historical_provider(self):
        p=council.source_plan("CRYPTO",{"venue_tape","funding_settlement","nonexistent_family"})
        self.assertTrue(any(s["id"]=="KRAKEN_TRADES" for s in p["research_leads"]))
        self.assertEqual(p["unsupported"],["nonexistent_family"])
        self.assertFalse(p["silent_provider_switch"])

    def test_empty_requests_or_wrong_market_rejected(self):
        with self.assertRaises(ValueError):
            council.source_plan("US",[])
        with self.assertRaises(ValueError):
            council.source_plan("MARS",["x"])
        self.assertFalse(council.fallback("UNKNOWN","KRAKEN_OHLCVT",purpose="ohlcv")
                         ["replay_same_experiment"])

    def test_unapproved_paper_not_hidden_as_data_readiness(self):
        for source in council.SOURCES:
            self.assertIn(source["status"],{
                "IMPLEMENTED_CHECKSUM_REPLAY","SOURCE_SCHEMA_PENDING",
                "DOCUMENTED_NOT_INGESTED","IMPLEMENTED_DAILY_ENGINE",
                "RESEARCH_LEAD","IMPLEMENTED_CONDITIONAL_ADAPTER",
                "IMPLEMENTED_DIAGNOSTIC","IMPLEMENTED_RELEASE_DATE_ONLY",
                "IMPLEMENTED_LISTING_BACKUP","IMPLEMENTED_NON_PIT_DAILY",
                "IMPLEMENTED_CURRENT_LIST_ONLY","IMPLEMENTED_AMBIGUITY_AUDIT"})
        self.assertEqual(council.report()["new_strategies_paper_accepted"],0)


if __name__=="__main__":
    unittest.main()
