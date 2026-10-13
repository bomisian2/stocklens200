# Personal data source decision record (2026-10-13)

This is an engineering interpretation, not legal advice. A successful HTTP call
does not establish a license. When permission is ambiguous, StockLens blocks the
feature or keeps the data local and marks redistribution as unauthorized.

| Source | Official policy / source | Local personal automation decision | Free-scale consequence |
|---|---|---|---|
| Yahoo Finance / yfinance | [Yahoo Terms](https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html) prohibit automated data collection without express prior permission | Disabled in the personal pipeline; existing public code is not reused | No compliant automatic path established |
| Alpha Vantage | [Terms](https://www.alphavantage.co/terms_of_service/) grant personal, non-commercial device use; [support](https://www.alphavantage.co/support/) states 25 free requests/day and that real-time/15-minute US data is premium; [documentation](https://www.alphavantage.co/documentation/) limits free `TIME_SERIES_DAILY` to `compact` (latest 100 points), while `full` and adjusted daily history are premium | Optional user-key raw EOD adapter; local cache only; no redistribution claim; repeated calls merge new rows | Three pilot symbols plus QQQ fit within a day, but an initial free call cannot produce EMA200 or a 252-session drawdown; 200 symbols cannot refresh daily at 25 requests/day |
| SEC EDGAR | [Developer resources](https://www.sec.gov/about/developer-resources) and [API docs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) allow keyless public API access subject to fair access and identifiable automation | Not used in this run: this PC previously received 403 and no user-supplied SEC contact identity is configured; no bypass or retry loop | Potentially scalable if a compliant environment and real contact User-Agent work |
| Applied Materials | [Official IR release](https://ir.appliedmaterials.com/news-releases/news-release-details/applied-materials-announces-third-quarter-2026-results); issuer release mirrored by [GlobeNewswire](https://www.globenewswire.com/news-release/2026/08/13/3344890/0/en/applied-materials-announces-third-quarter-2026-results.html) | IR host returned 403 locally; issuer-distributed GlobeNewswire HTML is collected once per filing and stored locally | Company-specific parser; feasible but not yet generic for 200 firms |
| Broadcom | [Official IR release](https://investors.broadcom.com/news-releases/news-release-details/broadcom-inc-announces-third-quarter-fiscal-year-2026-financial); issuer release distributed by [PR Newswire](https://www.prnewswire.com/news-releases/broadcom-inc-announces-third-quarter-fiscal-year-2026-financial-results-and-quarterly-dividend-302868129.html) | IR host returned 403 locally; issuer-distributed PR Newswire HTML is stored locally | Company-specific parser; public redistribution is not asserted |
| Qualcomm | [Official IR results](https://investor.qualcomm.com/financial-info-sec-filings/historical-financial-results/default.aspx) and official IR CDN earnings PDFs | Official PDF is downloaded, hashed, parsed, and stored locally | Repeatable for Qualcomm; CDN structure is issuer-specific |

## What is and is not currently permitted by the implementation

- Official issuer results may be downloaded for this user's local research. The
  implementation does not assert a separate right to republish complete documents
  or raw tables and therefore keeps them out of Git and the public app.
- Alpha Vantage integration is limited to personal, non-commercial local use under
  the user's own account/key. It stores raw, as-traded EOD history, never labels
  it real-time, and does not authorize public display. The free adapter requests
  `outputsize=compact`; it does not pretend that premium full/adjusted history is
  available.
- Market capitalization and shares outstanding are not collected yet. Adding an
  `OVERVIEW` request doubles Alpha Vantage calls and requires field-quality tests.
- Actual personal price/history availability is determined from the ignored
  local audit. Cache contents and derived values are not committed or published
  by this branch.
- Guidance is the issuer's published range. It is not an analyst consensus and is
  never silently converted into a target price.

## Expansion limit

The financial pilot proves three issuer-specific paths, not a universal 200-company
collector. A 200-company implementation needs a common licensed market source and
a scalable official filing source. Under the current free Alpha Vantage limit,
one EOD request per company requires at least eight quota days for a full sweep;
adding QQQ and metadata makes daily Top-200 refresh infeasible. Monthly cost can
remain zero for the three-company pilot, but daily 200-company automation is not
proven. Corporate actions also make raw-price long-horizon backtests unsuitable
until an adjusted-data source is licensed.
