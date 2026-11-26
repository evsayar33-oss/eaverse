# Expanded universe and longer daily price history

## Scope

- US: union of the SEC exchange-ticker directory, Nasdaq stock screener, and Nasdaq Trader directory. Normalized ticker deduplication; not a historical point-in-time membership database.
- BIST: KAP company codes plus Borsa Istanbul's participation ETF list (candidate Yahoo symbols; no price claim before verification).
- Crypto: preserve original Coinbase USD identities. Add Binance Spot, Bybit Spot, OKX Spot USD/USDT/USDC pairs. Venue and quote are part of each identity, so this count is not a count of unique underlying coins. Only live spot listings are eligible; no perpetuals. New venues are research-only, never PAPER or live.

Each source has an independently timestamped cached listing and error. A failure does not discard another directory or imply newly observed symbols. First/last observations are saved prospectively, not filled retrospectively. Historical candidates outside the latest directory union are counted separately; they have not automatically acquired a historical price dataset. No claims of complete delisted coverage, survivorship-free testing, or permanent equity ticker mapping.

## Daily history

- Yahoo: request daily adjusted OHLCV from 1990-01-01 through the last completed exchange session, limited by real listing and provider coverage. Equity calendar requests explicitly include the extended period.
- Coinbase: request completed UTC daily candles backwards towards 2012-01-01.
- Public spot venue APIs: backward daily paging towards 2012-01-01, with bounded requests, explicit instrument identity, UTC daily boundaries, and confirmed/closed bars. No mixing venues. Each request is bounded; pagination time and page cap produce explicit statuses.
- Where an old gap exists, only an intact latest suffix with at least 600 bars is used. Missing recent bars, invalid candles and insufficient history are rejected. Legacy source-specific fallback is labelled; new venues never fall back to Coinbase/Yahoo.
- Actual oldest/latest observation, bar count, status, source and snapshot digest are saved to a compact last-success ledger. The report and panel distinguish discovery candidates, validated histories, and successful histories older than six years. This does not store raw vendor data for every asset; history is retrieved from providers on demand.

Existing frozen PAPER checks, archived trial identities, and cohort code hash stay unchanged. Longer windows apply to subsequent discovery; already-completed trial IDs are not silently overwritten or repeated. The persistent combination focus is unchanged. New venue discoveries are excluded from PAPER nomination.

Public data access can fail due to availability, geography, rate limits or terms; unavailable is not a completed test or a zero-return result. No synthetic observations, no fabricated historical membership. A fully delist-aware history requires licensed or independently validated point-in-time archives.

Provider documentation:
- Binance public market-data API: https://developers.binance.com/docs/binance-spot-api-docs/faqs/market_data_only
- Bybit spot instruments and candles: https://bybit-exchange.github.io/docs/v5/market/instrument and https://bybit-exchange.github.io/docs/v5/market/kline
- OKX instruments and historical candles: https://www.okx.com/docs-v5/en/
- Borsa Istanbul historical data: https://datastore.borsaistanbul.com/
