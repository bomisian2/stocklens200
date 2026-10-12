# Windows official SEC sample collection

## Purpose and safety boundary

`scripts/run_sec_windows.ps1` is the single operator command for the four-company validation gate.
It uses `scripts/collect_sec_sample_windows.py` and `stocklens/sec_windows.py` to:

1. make exactly one access-probe request to official GOOGL Company Facts;
2. stop immediately on HTTP 403 or any network/TLS/timeout/response error;
3. continue only after a valid official JSON response;
4. reuse the successful probe response rather than download it twice;
5. fetch the official ticker map and the remaining Company Facts/Submissions files once each, at
   least 0.6 seconds apart;
6. validate CIK/ticker identity, raw SHA-256, acquisition time, filing acceptance time, amendments,
   fiscal years, metric coverage, and possible split/restatement comparative changes;
7. load the result through the existing `SecFileProvider` before any valuation is attempted; and
8. write raw and derived sample outputs under the gitignored `data/sec-import/` directory.

There is no retry loop, proxy support, IP rotation, alternate hostname, browser impersonation, or
access-control bypass. Response bodies are never logged on failure. Only an allow-list of response
headers is recorded.

## Install and run on Windows

From PowerShell in the repository root:

```powershell
git switch feat/v2-valuation-engine
python -m pip install -r requirements.txt
$env:STOCKLENS_SEC_CONTACT = 'your-monitored-email@example.com'
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_sec_windows.ps1
Remove-Item Env:STOCKLENS_SEC_CONTACT
Get-Content .\data\sec-import\last_attempt.json
```

Alternatively, pass `-Contact 'your-monitored-email@example.com'`. The contact must be an actual
monitored address because SEC asks automated clients to identify themselves. It is sent only in the
SEC User-Agent and is not written to result files or application logs. Do not put an API key,
password, or other secret in this value.

Exit codes:

| Code | Meaning |
| ---: | --- |
| 0 | Four official inputs produced four evaluated results |
| 2 | SEC access or raw validation failed; no partial run was published |
| 3 | Raw collection succeeded but at least one valuation remained on hold |

On success, inspect:

- `data/sec-import/<UTC>/manifest.json`: official URLs, CIKs, file names, SHA-256 hashes;
- `collection_report.json`: safe HTTP diagnostics and raw validation summaries;
- `financials.csv`: point-in-time normalized annual and quarter rows;
- `valuations.csv`: the four reproducible model outputs or explicit holds; and
- `result.json`: risk-free input, counts, valuation details, and run summary.

`data/sec-import/` is intentionally gitignored. Raw SEC files must not be committed to this PR.

## Observed PC result — 2026-10-12

The command was executed twice only because the first implementation probed the ticker-map host and
was then corrected to probe the required Company Facts endpoint directly. Neither endpoint was
repeated.

| Time | Endpoint | Result | Follow-up requests |
| --- | --- | --- | ---: |
| 11:42 KST | `https://www.sec.gov/files/company_tickers.json` | HTTP 403, `AkamaiGHost` | 0 |
| 11:43 KST | `https://data.sec.gov/api/xbrl/companyfacts/CIK0001652044.json` | HTTP 403, `AkamaiGHost` | 0 |

The direct Company Facts run recorded `request_count=1`, `retry_count=0`, and did not request
Submissions or the other three companies. DNS/TLS reached the HTTP server; the failure was an HTTP
policy response, not a JSON parsing error. The response body was not recorded.

Consequently:

- official raw financials acquired: **0/4**;
- actual valuations completed: **0/4**;
- GOOGL, AMAT, AVGO, and QCOM remain **hold**;
- fixture data was not substituted; and
- this result does not show that SEC is unavailable globally, only that this PC/network path was
  denied at the observed time.

Do not repeat the request from this network merely to see whether the block changes. Use a normal,
SEC-permitted environment and run the same one-shot gate there.

## Fair-access references

- SEC developer and fair-access guidance: https://www.sec.gov/about/developer-resources
- SEC EDGAR API and bulk data documentation:
  https://www.sec.gov/search-filings/edgar-application-programming-interfaces

The implementation is materially below SEC's published aggregate ceiling of 10 requests per
second: it enforces at least 0.6 seconds between requests (at most about 1.67 requests per second)
and downloads only the requested four-company sample.

