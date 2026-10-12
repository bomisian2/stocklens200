# Financial data source review (2026-10-12)

The target use is a free, public Streamlit dashboard. An endpoint returning HTTP 200 does not by
itself grant public-display or redistribution rights.

| Source | One-time access probe | US issuer / GAAP coverage | Public dashboard finding | Decision |
| --- | --- | --- | --- | --- |
| SEC EDGAR Company Facts / Submissions | Direct requests previously returned HTTP 403 in the local and GitHub runner environments; not repeated in this change | Official XBRL facts and filing metadata for SEC filers | SEC states that information on sec.gov is public and may be copied/further distributed with attribution; fair-access rules still apply | Approved source; direct API remains explicit opt-in, offline official-file import is default |
| SEC nightly Company Facts / Submissions ZIP | Same environment previously returned HTTP 403; not repeated | Same official JSON corpus, republished nightly | Same SEC public-information policy | Approved operator-supplied offline input |
| SEC quarterly Financial Statement Data Sets | Official download catalogue verified; direct file download not attempted from the blocked runtime | Numeric face statements, as filed, quarterly packages from 2009 | Official public SEC data | Approved for manual acquisition; parser not implemented in this PR |
| Alpha Vantage demo | HTTP 200 JSON on 2026-10-12; body not retained | Broad company overview/fundamentals, but full coverage was not measured without a key | Free license is personal/non-commercial unless otherwise agreed in writing | Rejected from automatic and public-display paths |
| Twelve Data demo | HTTP 200 JSON on 2026-10-12; body not retained | Fundamentals endpoint reachable, but authenticated 200-company coverage was not measured | External display/redistribution requires an add-on or separate agreement; free tier is not for commercial use | Rejected |
| Financial Modeling Prep without key | HTTP 401 JSON on 2026-10-12 | Coverage not tested without credentials | Public display requires a Data Display and Licensing Agreement | Rejected; no paid inquiry or subscription started |
| Polygon without key | HTTP 401 on 2026-10-12 | Coverage not tested without credentials | No public redistribution right was established for the free/no-key path | Rejected |
| SimFin Free / Basic | Not probed because account use would require accepting restrictive terms | Structured statements are offered | Personal research only; sharing/distribution prohibited | Rejected |
| Yahoo/Nasdaq consumer fundamentals | Not re-probed | Broad consumer-facing coverage | No redistribution-safe financial-statement license established | Rejected as a fundamentals source |

The access probe is intentionally limited to HTTP status, domain, and content type. It logs no
response bodies, credentials, cookies, or personal data. It is not run in CI.

Result: no third-party free source was both technically available and licensed clearly enough for
this public dashboard. Therefore only official SEC data is connected, and only from a verified
operator-supplied file when direct access is blocked. Independent financial cross-validation remains
`not_configured`; this lowers confidence and blocks any claim that the four sample valuations have
been completed.

References:

- SEC Financial Statement Data Sets: https://www.sec.gov/dera/data/financial-statement-data-sets.html
- SEC EDGAR APIs and bulk files: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- SEC developer and fair-access policy: https://www.sec.gov/about/developer-resources
- SEC website dissemination policy: https://www.sec.gov/about/privacy-information
- Alpha Vantage terms: https://www.alphavantage.co/terms_of_service/
- SimFin data license: https://www.simfin.com/en/commercial-license/
- FMP public-display guidance: https://site.financialmodelingprep.com/insights/platform/can-you-use-fmp-data-in-a-public-app-website-or-client-dashboard
- Twelve Data terms: https://twelvedata.com/terms
- Polygon market-data terms: https://polygon.io/terms/market_data_terms.pdf
