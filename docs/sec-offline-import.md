# Official SEC offline import

This path exists for environments where direct SEC API and bulk downloads return HTTP 403. It
does not retry, proxy, mirror, or otherwise bypass SEC access controls. The operator downloads the
files in an environment where access is permitted, then transfers them to a local directory.

## Accepted inputs

- Individual official Company Facts JSON:
  `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`
- Individual official Submissions JSON:
  `https://data.sec.gov/submissions/CIK##########.json`
- Official nightly `companyfacts.zip` and `submissions.zip` archives described by the SEC API page.
- Optional official `company_tickers.json` for ticker-to-CIK mapping.

The quarterly Financial Statement Data Sets are a reviewed official source, but their flattened
TSV schema is not parsed by this version. Do not rename one as a Company Facts archive.

Place the files in one directory, for example `data/sec-import/`. That directory is gitignored so
large raw archives are not accidentally committed. The manifest and every artifact must remain in
the same directory.

## Create the integrity manifest

For individual files:

```bash
python scripts/create_sec_import_manifest.py \
  --companyfacts data/sec-import/CIK0001652044.json \
  --companyfacts data/sec-import/CIK0000006951.json \
  --companyfacts data/sec-import/CIK0001730168.json \
  --companyfacts data/sec-import/CIK0000804328.json \
  --submissions data/sec-import/CIK0001652044-submissions.json \
  --submissions data/sec-import/CIK0000006951-submissions.json \
  --submissions data/sec-import/CIK0001730168-submissions.json \
  --submissions data/sec-import/CIK0000804328-submissions.json \
  --obtained-at 2026-10-12T12:00:00+09:00 \
  --output data/sec-import/manifest.json
```

For official nightly ZIPs, pass each ZIP once instead. The generator records the canonical SEC URL,
CIK for individual JSON, SHA-256, and the operator-supplied acquisition time. The loader rejects a
hash mismatch, a path outside the manifest directory, a non-SEC source URL, a future acquisition
time, or a manifest not explicitly marked `fixture=false`.

The four mandatory sample identifiers were checked on 2026-10-12 against the official SEC ticker
file at `https://www.sec.gov/files/company_tickers.json`:

| Ticker | SEC CIK | Ten-digit filename form |
| --- | ---: | --- |
| GOOGL | 1652044 | `CIK0001652044` |
| AMAT | 6951 | `CIK0000006951` |
| AVGO | 1730168 | `CIK0001730168` |
| QCOM | 804328 | `CIK0000804328` |

A conflicting ticker map, CLI override, Company Facts CIK, or Submissions CIK is a hard error. For
other companies, identity must be supported by an imported official ticker map or the ticker list in
the official Submissions payload; a bare user-entered ticker/CIK pair is not sufficient evidence.

## Import and evaluate

```bash
python scripts/import_sec_financials.py \
  --manifest data/sec-import/manifest.json \
  --tickers GOOGL AMAT AVGO QCOM \
  --as-of 2026-10-09T20:00:00+00:00

python scripts/update_valuations.py \
  --financial-source offline \
  --sec-manifest data/sec-import/manifest.json \
  --tickers GOOGL AMAT AVGO QCOM \
  --allow-partial
```

`financials.csv` stores the input artifact hash, acquisition time, and normalization version for
every period. `financial_provenance.json` stores all raw artifact hashes plus a deterministic hash of
the normalized periods. Existing companies outside the requested import set are retained.

## Validation and safety gates

- SEC `acceptanceDateTime` wins when available. If it is missing, the fact is unavailable until the
  end of the next UTC day after `filed`, is marked as estimated, and receives degraded validation.
- Facts published after `--as-of` are excluded. Later eligible filings may supply restated or
  split-adjusted comparative values; later filings that were not yet public cannot.
- Annual duration must be 300-430 days and quarter duration 65-120 days. Short fiscal transition
  periods are held pending a dedicated normalization rule.
- Financial data whose latest availability is more than 190 days before the valuation timestamp is
  `stale`. It cannot generate a new valuation or trading signal.
- Test fixtures are never accepted by the operational manifest loader.
- No verified input means `hold`; the system never invents EPS, growth, multiples, or cash flow.

The raw files are official public filings, but SEC itself warns that compiled data sets may contain
issuer or extraction errors. Review the filing before making an investment decision.
