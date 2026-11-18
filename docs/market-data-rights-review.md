# Market-data rights and zero-cost provider review

Review date: 2026-10-13. This is an engineering compliance review, not legal advice. An API being
technically reachable does not grant a right to automate collection, store snapshots, display data
on a public website, or publish derived signals.

## Current sources

| Source | Local research | Public price display | Snapshot redistribution | Derived public signals | Decision |
|---|---|---|---|---|---|
| Yahoo Finance through yfinance | The yfinance project describes research/education use and says Yahoo data is intended for personal use. | No explicit public-display grant was found. | No explicit grant was found; Yahoo's terms restrict substitute data services and reuse. | No explicit grant was found. | Keep outside public deployment until written permission or a suitable license exists. |
| Nasdaq website screener | Nasdaq's legal notice grants limited personal, non-commercial use. | Third-party platform distribution requires written consent. | Reproduction and distribution are restricted. | Automated extraction for data-analysis software is expressly restricted without permission. | Do not use as an automated operating or public redistribution source. |

Official references:

- Yahoo terms: <https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html>
- yfinance notice: <https://github.com/ranaroussi/yfinance#readme>
- Nasdaq legal notice: <https://www.nasdaq.com/legal>

## Zero-cost alternatives

| Source | Free scope / calls | Public display and redistribution | Top-200 daily feasibility | 2026-10-13 connection check | Conclusion |
|---|---|---|---|---|---|
| Alpha Vantage | Most datasets: 25 requests/day; verified educational/open-source projects may request higher limits. Real-time and 15-minute delayed US data are premium. | The standard terms do not establish a public redistribution right for this dashboard; commercial or third-party access requires a different arrangement. | 200 symbol-by-symbol calls exceed the normal free daily limit. A bulk, licensed Top-200 route was not confirmed. | Demo daily IBM endpoint returned HTTP 200 and data through 2026-10-12. | Technically reachable, but the ordinary free tier is insufficient and public-display rights remain unconfirmed. |
| Twelve Data | Free tier exists, subject to its subscription limits. | Terms restrict the free tier to non-commercial use and require an add-on or written agreement for redistribution/external display. | Not acceptable for this public MVP without a redistribution agreement. | No account or key was created; no authenticated call was made. | Blocked for public deployment. |
| Financial Modeling Prep | Personal/free access exists with account limits. | Terms require a specific Data Display and Licensing Agreement for multi-user display, even when free. | Technically broad coverage, but not a zero-cost public-display route. | No account or key was created; no authenticated call was made. | Blocked without a display agreement. |
| Tiingo | Starter advertises 500 unique symbols/month and 1,000 calls/day. | API data are internal-use only; redistribution requires special permission and additional fees. | Capacity could cover 200 symbols, rights cannot. | No account or key was created; no authenticated call was made. | Blocked for public deployment. |
| Polygon / Massive | Free individual plan advertises US stocks, five calls/minute, two years of end-of-day history. | Market-data terms restrict use to personal/non-business use and prohibit public display/distribution without written consent; redistribution uses business products. | Technical coverage exists, but the individual license does not cover this public app. | No account or key was created; no authenticated call was made. | Blocked for public deployment. |
| Finnhub | Redistribution rights are listed under enterprise/custom pricing. | No verified zero-cost redistribution right. | Coverage is not the blocker; the required right is not free/confirmed. | No account or key was created; no authenticated call was made. | Blocked unless a commercial agreement is approved. |
| Cboe delayed quote endpoint | A keyless delayed AAPL JSON endpoint is reachable. | Cboe policies require agreements/fees for external distribution; delayed SIP redistribution is not zero cost. | A single reachable endpoint is not a licensed consolidated Top-200 feed. | AAPL endpoint returned HTTP 200 JSON. | Technical PoC only; not a zero-cost public source. |
| Stooq CSV | A keyless URL is commonly used for historical data. | A clear official redistribution grant for this public use was not verified. | Browser verification also prevented an automated CSV response in this environment. | HTTP 200 returned a JavaScript browser-verification page, not CSV. | Do not integrate. |
| SEC issuer files / EDGAR | Official issuer associations and company filings are publicly accessible subject to fair-access rules. | Suitable for issuer identity and filings; it does not supply consolidated market prices or daily market caps. | Useful as a metadata/financial source, not a replacement for the Top-200 market-data feed. | `www.sec.gov/files/company_tickers.json` returned HTTP 403 from this PC; an independent web fetch could read it. | Retain as a future metadata source with compliant caching; it does not remove the price-license blocker. |

Official references:

- Alpha Vantage limits: <https://www.alphavantage.co/support/>
- Alpha Vantage terms: <https://www.alphavantage.co/terms_of_service/>
- Twelve Data terms: <https://twelvedata.com/terms>
- FMP terms: <https://site.financialmodelingprep.com/terms-of-service>
- Tiingo terms: <https://app.tiingo.com/tos/>
- Tiingo pricing: <https://www.tiingo.com/about/pricing>
- Polygon/Massive market-data terms: <https://polygon.io/terms/market_data_terms.pdf>
- Finnhub enterprise licensing: <https://api.finnhub.io/pricing-startups-and-enterprise>
- Cboe market-data policies: <https://cdn.cboe.com/resources/membership/Market_Data_Policies.pdf>
- Cboe SIP fees: <https://datashop.cboe.com/sip-fees>
- SEC developer resources: <https://www.sec.gov/about/developer-resources>

## Operating boundary

No reviewed provider currently satisfies all four requirements at monthly cost zero: complete US
equity coverage, daily Top-200 market-cap selection, automated storage, and public display or
redistribution. The public deployment gate must therefore remain closed. A safe interim split is:

1. Internal/private research may use a provider only within that provider's personal/internal terms.
2. A public shell may show application structure, status, source links, and non-market-data research
   whose publication rights are independently verified, but must not expose the current restricted
   price, market-cap snapshot, rankings, or derived market signals.
3. Production Top-200 prices and rankings require written provider permission or an approved paid
   display/redistribution license. Provider access, data entitlement, and signal-publication rights
   must each be recorded separately.
