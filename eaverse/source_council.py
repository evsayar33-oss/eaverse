"""Research source council; alternatives never silently replace original market observations."""
from __future__ import annotations
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

SCHEMA="eaverse.source_council.v7"
ROWS=[["CRYPTO","BINANCE_SPOT_VISION","BINANCE_SPOT","spot_taker_base_volume","2023-2025 verified","NONCOMMERCIAL_RESEARCH","IMPLEMENTED_CHECKSUM_REPLAY","https://github.com/binance/binance-public-data"],["CRYPTO","BINANCE_USDM_FUNDING","BINANCE_USDM_PERP","funding_settlement","historical monthly archive","NONCOMMERCIAL_RESEARCH","SOURCE_SCHEMA_PENDING","https://github.com/binance/binance-public-data"],["CRYPTO","BINANCE_USDM_METRICS","BINANCE_USDM_PERP","open_interest_ratios","historical daily metrics; potential labeling changes","NONCOMMERCIAL_RESEARCH","SOURCE_SCHEMA_PENDING","https://github.com/binance/binance-public-data"],["CRYPTO","KRAKEN_OHLCVT","KRAKEN_SPOT","venue_ohlcvt","pair inception through available quarterly ZIP","LICENSE_VERIFY","DOCUMENTED_NOT_INGESTED","https://support.kraken.com/articles/360047124832-downloadable-historical-ohlcvt-open-high-low-close-volume-trades-data"],["CRYPTO","KRAKEN_TRADES","KRAKEN_SPOT","venue_tape","pair inception through available quarterly ZIP","LICENSE_VERIFY","DOCUMENTED_NOT_INGESTED","https://support.kraken.com/articles/360047543791-downloadable-historical-market-data-time-and-sales-"],["CRYPTO","OKX_TRADES","OKX_EXCHANGE","venue_tape","September 2021 onward","LICENSE_VERIFY","DOCUMENTED_NOT_INGESTED","https://www.okx.com/historical-data"],["CRYPTO","OKX_ORDERBOOK","OKX_EXCHANGE","l2_orderbook_sequence","March 2023 onward","LICENSE_VERIFY","DOCUMENTED_NOT_INGESTED","https://www.okx.com/historical-data"],["CRYPTO","OKX_FUNDING","OKX_PERP","funding_settlement","March 2022 onward","LICENSE_VERIFY","DOCUMENTED_NOT_INGESTED","https://www.okx.com/historical-data"],["CRYPTO","COINBASE_DAILY","COINBASE_SPOT_USD","venue_ohlcv","pair inception / endpoint bounded","PUBLIC_API","IMPLEMENTED_DAILY_ENGINE","https://docs.cdp.coinbase.com/exchange/reference/exchangerestapi_getproductcandles"],["CRYPTO","DERIBIT_HISTORY","DERIBIT_DERIVATIVES","options_iv_oi_funding","instrument dependent","ACCESS_VERIFY","RESEARCH_LEAD","https://docs.deribit.com/"],["CRYPTO","DEFILLAMA","ONCHAIN_AGGREGATE","stablecoin_liquidity_supply","revisions possible","LICENSE_VERIFY","RESEARCH_LEAD","https://defillama.com/"],["CRYPTO","COINMETRICS_COMMUNITY","ONCHAIN_AGGREGATE","chain_activity_metrics","free community subset","FREE_TIER_VERIFY","RESEARCH_LEAD","https://community-api.coinmetrics.io/"],["US","SEC_EDGAR_SUBMISSIONS","SEC_ISSUER","issuer_filing_acceptance","1990s onward; recent endpoint partial","USER_AGENT_REQUIRED","IMPLEMENTED_CONDITIONAL_ADAPTER","https://www.sec.gov/search-filings/edgar-application-programming-interfaces"],["US","SEC_COMPANYFACTS","SEC_ISSUER","xbrl_reported_fundamentals","reported financial statement vintages","USER_AGENT_REQUIRED","RESEARCH_LEAD","https://www.sec.gov/search-filings/edgar-application-programming-interfaces"],["US","SEC_OLDLOADS","SEC_ISSUER","historical_filing_headers","1990s onward archive","USER_AGENT_REQUIRED","RESEARCH_LEAD","https://www.sec.gov/about/developer-resources"],["US","FINRA_SHORT_SALE_VOLUME","FINRA","daily_short_sale_volume","publication lag required","TERMS_VERIFY","RESEARCH_LEAD","https://www.finra.org/finra-data/browse-catalog/short-sale-volume-data"],["US","FINRA_SHORT_INTEREST","FINRA","short_interest_position","semi-monthly settlement not live","TERMS_VERIFY","RESEARCH_LEAD","https://www.finra.org/finra-data/browse-catalog/equity-short-interest/data"],["US","CBOE_VIX_CLOSE","CBOE_INDEX","vix_daily_close","1990 onward with known OHLC anomalies","PUBLIC_ARCHIVE","IMPLEMENTED_DIAGNOSTIC","https://www.cboe.com/tradable_products/vix/vix_historical_data/"],["US","CFTC_COT","CFTC","weekly_futures_position","Tuesday position versus later public release","PUBLIC_DATA","RESEARCH_LEAD","https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm"],["US","FRED_ALFRED","FRED_MACRO","macro_release_vintages","depends on vintage and series","API_KEY_CONDITIONAL","IMPLEMENTED_RELEASE_DATE_ONLY","https://fred.stlouisfed.org/docs/api/fred/"],["US","TREASURY_FISCAL_DATA","US_TREASURY","treasury_cash_auction","historical reporting releases","PUBLIC_API","RESEARCH_LEAD","https://fiscaldata.treasury.gov/api-documentation/"],["US","NYFED_MARKETS","NY_FED","repo_primary_dealer_rates","publication clock required","PUBLIC_API","RESEARCH_LEAD","https://markets.newyorkfed.org/static/docs/markets-api.html"],["US","NASDAQ_TRADER_DIRECTORY","NASDAQ","current_us_listings","current not PIT","PUBLIC_DIRECTORY","IMPLEMENTED_LISTING_BACKUP","https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs"],["US","YAHOO_US_DAILY","YAHOO_ADJUSTED_US","adjusted_ohlcv","history incomplete for delist/PIT","THIRD_PARTY_TERMS","IMPLEMENTED_NON_PIT_DAILY","https://finance.yahoo.com/"],["BIST","KAP_DISCLOSURES","KAP","issuer_announcements","historical publication and rights conditional","REGISTRATION_CONDITIONAL","IMPLEMENTED_CURRENT_LIST_ONLY","https://www.kap.org.tr/"],["BIST","SPK_BULLETINS","SPK","capital_corporate_events","bulletins back to 2005","PUBLIC_PDF_POLICY","RESEARCH_LEAD","https://spk.gov.tr/spk-bultenleri/gecmis-yillara-ait-bultenler"],["BIST","TCMB_EVDS","CENTRAL_BANK","rates_fx_credit","long official macro histories","API_KEY_CONDITIONAL","RESEARCH_LEAD","https://evds2.tcmb.gov.tr/"],["BIST","TEFAS_HISTORY","TEFAS","fund_nav_allocations","browser is not documented open API","AUTOMATION_RIGHTS_UNKNOWN","RESEARCH_LEAD","https://www.tefas.gov.tr/"],["BIST","BIST_HISTORICAL_MEMBERSHIP","COMMUNITY_PIT","constituent_universe","2000-2026; 25 ambiguous keys","UNDERLYING_DATA_RIGHTS_UNKNOWN","IMPLEMENTED_AMBIGUITY_AUDIT","https://github.com/GocmenCo/bist-constituents"],["BIST","YAHOO_BIST_DAILY","YAHOO_ADJUSTED_TR","adjusted_ohlcv","delisted PIT incomplete","THIRD_PARTY_TERMS","IMPLEMENTED_NON_PIT_DAILY","https://finance.yahoo.com/"]]
KEYS=("market","id","venue","feature_family","history","access","status","url")
SOURCES=tuple(dict(zip(KEYS, r)) for r in ROWS)

def validate():
    ids={s["id"] for s in SOURCES}
    if len(ids)!=len(SOURCES) or {s["market"] for s in SOURCES}!={"BIST","US","CRYPTO"}:
        raise ValueError("INVALID_SOURCE_COUNCIL_MARKETS_OR_IDS")
    if not all(s["url"].startswith("https://") and s["history"] and s["feature_family"] for s in SOURCES):
        raise ValueError("UNSUPPORTED_SOURCE_REFERENCE")
    return True

def source_plan(market, information_families):
    """Conservative source leads. Actual PIT, license and venue execution not inferred."""
    validate()
    if market not in {"BIST","US","CRYPTO"}:
        raise ValueError("UNKNOWN_MARKET")
    families=set(information_families)
    if not families or not all(isinstance(x,str) and x for x in families):
        raise ValueError("EXPLICIT_INFORMATION_REQUIREMENT_NEEDED")
    supported=[x for x in SOURCES if x["market"]==market and x["feature_family"] in families]
    return {
        "market":market,"requested":sorted(families),"research_leads":supported,
        "unsupported":sorted(families-{x["feature_family"] for x in supported}),
        "new_source_trading_permission":False,
        "silent_provider_switch":False,
    }

def fallback(primary, backup, *, purpose, same_instrument=False, rights_verified=False,
             same_venue=False, original_publication_clock_verified=False):
    """Never create a synthetic Binance/Coinbase/Kraken/OKX book or CVD tape."""
    validate()
    lookup={s["id"]:s for s in SOURCES}
    if primary not in lookup or backup not in lookup or primary==backup:
        return {"status":"INVALID_PROVIDER_PAIR","replay_same_experiment":False}
    if lookup[primary]["market"]!=lookup[backup]["market"]:
        return {"status":"CROSS_MARKET_NOT_EQUIVALENT","replay_same_experiment":False}
    if purpose in {"venue_tape","spot_taker_base_volume","l2_orderbook_sequence",
                   "funding_settlement","trade_fill","adjusted_ohlcv"}:
        return {"status":"VENUE_NATIVE_OR_ADJUSTMENT_NOT_INTERCHANGEABLE",
                "replay_same_experiment":False}
    if not all((same_instrument, rights_verified, same_venue,
                original_publication_clock_verified)):
        return {"status":"INDEPENDENT_EXPERIMENT_RE_REGISTRATION_REQUIRED",
                "replay_same_experiment":False}
    return {"status":"NEW_SOURCE_VERSION_AND_REGRESSION_REVIEW_REQUIRED",
            "replay_same_experiment":False}

def report():
    validate()
    by_market=dict(Counter(x["market"] for x in SOURCES))
    return {
        "schema":SCHEMA,"providers":len(SOURCES),"by_market":by_market,
        "sources":SOURCES,
        "catalog_sha256":hashlib.sha256(json.dumps(SOURCES,sort_keys=True).encode()).hexdigest(),
        "independent_backup_not_silent_splice":True,
        "all_external_sources_operational":False,
        "new_strategies_paper_accepted":0,
        "priorities":[
            "USD-M funding with exact settlement/first-publication clock",
            "Kraken time and sales independent venue aggressor comparator",
            "OKX snapshot and ordered L2 deltas with gap checks",
            "SEC filing/vintage issuer mapping against delist-aware US history",
            "BIST KAP/SPK publication clocks and historical delist-adjusted prices",
        ],
    }

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--out",default="out/source_council")
    args=parser.parse_args()
    root=Path(args.out);root.mkdir(parents=True,exist_ok=True)
    data=report()
    (root/"sources.json").write_text(json.dumps(data,indent=2)+"\n",encoding="utf-8")
    print("EAVERSE_SOURCE_COUNCIL",data["providers"],data["by_market"],
          "NO_INVISIBLE_FALLBACK")

if __name__=="__main__":
    main()
