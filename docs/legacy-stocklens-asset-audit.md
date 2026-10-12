# Legacy StockLens asset audit

Audit date: 2026-10-12 (Asia/Seoul)  
Branch: `feat/v2-valuation-engine`  
Baseline: `2e97354925be172184bea3710b8d9291399ca7da`

## Decision

The accessible legacy candidate is **not** the described 39-stock StockLens project. It is a small,
non-Git Streamlit prototype containing `app.py`, `engine.py`, `README.md`, and `requirements.txt`.
No reusable `bandTable`, `analystLog.oneTime`, `computePriceValidity`, distorted/PEG veto,
confidence classifier, or signature-based review queue was found. No legacy observation was converted,
and no operational valuation evidence was generated.

The audit used two complementary views: data-lineage analysis (source, publication time, accounting
basis, split treatment, and redistribution rights) and dependency decomposition (price history,
public EPS vintage, adjustment evidence, financial periods, and model gate). This avoids treating a
function name or an undated memo as an investable data asset.

## Reproducible inventory

The local candidate was audited with:

```powershell
python scripts/audit_legacy_assets.py D:\GPT\StockLens200_prototype --output .tmp\legacy-prototype-audit.json
```

| Item | Result |
|---|---:|
| UTF-8 source/data files scanned | 3 |
| `bandTable` observations | 0 |
| complete point-in-time P/E observations | 0 |
| effective observations after price-date / EPS-vintage de-duplication | 0 |
| `analystLog.oneTime` records | 0 |
| complete quantitative one-time adjustments | 0 |
| parse errors | 0 |
| evidence conversion performed | no |

The prototype has no `.git` directory. Its `engine.py` reads Yahoo `forwardEps` and applies hard-coded
sector centers, growth adjustments, 75%/125% scenario multipliers, and an 85% buy threshold. Those
heuristics are incompatible with the v2.1 evidence rules and were not reused. Its README also says
point-in-time price/consensus history remains an unmet operating requirement.

The public GitHub account API returned one repository only:
[`bomisian2/stocklens200`](https://github.com/bomisian2/stocklens200). The reachable local Git graph
contains 16 commits. Searching every reachable commit and historical path found no legacy asset; the
initial commit contains only `README.md`, `app.py`, `engine.py`, and `requirements.txt`. The public
repository inventory endpoint was
[`users/bomisian2/repos`](https://api.github.com/users/bomisian2/repos?per_page=100).

## Requested legacy features

| Feature | Accessible implementation | Data structure / observations | Reuse decision |
|---|---|---|---|
| `bandTable` | not found | no table, source, as-of date, or observations | blocked |
| `analystLog.oneTime` | not found | no adjustment amount, tax effect, filing time, or recurrence evidence | blocked |
| `computePriceValidity` | not found | no function or test | blocked |
| distorted + PEG veto | not found in legacy candidate | v2.1 has its own independently implemented distorted-EPS gate; no legacy reuse | not a legacy asset |
| confidence classification | not found in legacy candidate | v2.1 `confidence_score` is unrelated and must not be represented as migrated | not a legacy asset |
| signature-based review queue | not found | no state schema, signature function, or history | blocked |

An additional filename-only search of the user's Desktop, Documents, and Downloads directories found
no StockLens, `bandTable`, or `analystLog` package. If the 39-stock project exists in a private
repository, another computer, a browser-only artifact, or an archive with a different name, its exact
path or export is required. This audit does not infer its contents.

## Read-only evidence audit

`stocklens.legacy_assets` does not write to the supplied source and never emits v2.1 evidence. It
separates text-name discovery from actual JSON observations.

A `bandTable` row is complete only when it has timezone-aware price observation and EPS-publication
timestamps; numeric price and public TTM EPS; separate HTTPS price and EPS sources; and explicit GAAP,
split-adjustment, and distortion states. EPS must have been public no later than the price observation.
Formal P/E eligibility still requires every row to be complete, at least 20 observations, and at least
five years of coverage. Exact duplicate rows are rejected, price dates must be unique, and the effective
count permits no more than two price observations per EPS publication vintage.

`analystLog.oneTime` is adapter-ready only when every record has a quantified per-share adjustment,
category, publication timestamp, HTTPS filing source, recurrence decision, and tax basis. A qualitative
note can be displayed as research context but cannot change EPS.

## Five-company real-data status

Official issuer IR archives are visible for several samples, but visibility is not the same as an
operational, validated import or permission to republish a derived historical database.

| Ticker | Official material located | Imported verified periods | Historical P/E rows | Valuation |
|---|---|---:|---:|---|
| GOOGL | [Alphabet 2024 annual report](https://abc.xyz/assets/e1/57/8a65483e43feaa709f6cc5cc0737/annualreport2024-web.pdf) and quarterly IR pages | 0 | 0 | hold |
| AMAT | [2025 annual report archive](https://ir.appliedmaterials.com/annual-report-proxy/) and quarterly results archive | 0 | 0 | hold |
| AVGO | [annual reports 2009-2025](https://investors.broadcom.com/financial-information/annual-reports) | 0 | 0 | hold |
| QCOM | [historical financial results](https://investor.qualcomm.com/financial-info-sec-filings/historical-financial-results/default.aspx) | 0 | 0 | hold |
| INTC | [annual report archive](https://www.intc.com/filings-reports/annual-reports/default.aspx), including 2025 | 0 | 0 | hold |

Why the count remains zero:

1. The IR documents have not been normalized into `FinancialPeriod` rows with accession, filing or
   acceptance time, amendment selection, taxonomy mapping, source hash, and split checks.
2. No redistribution-safe point-in-time price source has been approved for an operational P/E history.
   Local Yahoo-derived price caches remain research artifacts and are not converted into a public
   evidence bundle. [Yahoo's general terms](https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html)
   restrict creating a database or substitute service from its content and do not establish the
   required redistribution right.
3. There is no independently checked TTM GAAP EPS vintage series paired to those price dates.
4. No quantitative one-time adjustment set exists for the five issuers. INTC therefore remains blocked
   from P/E rather than being normalized by assumption.

Actual result: **0/5 verified financial imports, 0/5 formal P/E histories, 0/5 real valuations, 5/5
holds**. The official materials above are acquisition candidates, not successful production inputs.

## Five-year / 20-observation gate

The gate is defensible for a formal P/E model because five years can cover more than one market and
rate regime, while 20 observations permit quartiles and basic outlier handling. It is not, by itself,
proof of independence: twenty daily points from one month, repeated EPS values, or retrospectively
restated EPS can still create false precision. The audit therefore checks time span and point-in-time
availability rather than count alone. A later importer should also enforce quarterly or wider sampling,
unique price dates, EPS-vintage grouping, split consistency, and effective sample size.

The threshold can cause legitimate young listings or recently reorganized issuers to remain on hold.
That is preferable to lowering it without validation. Two output tiers are appropriate:

- **Reference analysis:** shorter or incomplete history may show sourced GAAP metrics and data gaps. It
  must not emit a fair value, buy zone, judgement, or signal eligibility.
- **Formal valuation:** keeps the five-year/20-clean-observation gate plus financial freshness,
  accounting-quality, price-license, and model-specific requirements. Only this tier can contribute to
  a value judgement.

The existing historical-only DCF remains `provisional` and judgement-hold; it is not a shortcut around
the P/E evidence gate.

## Minimum next work, in order

1. Obtain the exact private repository URL, archive, or export directory for the 39-stock project.
2. Run the read-only auditor and preserve the report with the source commit or archive SHA-256.
3. Record each asset's original schema, issuer count, earliest/latest date, and source URLs.
4. Reject interpolation, repeated EPS, timezone-free dates, and rows without accounting basis.
5. Build a schema-specific adapter only after actual `bandTable` and `analystLog` records are visible.
6. Pilot one issuer's structured IR XBRL/filing download without evading access controls.
7. Obtain terms evidence for storing and publicly displaying the historical price series.
8. Reconstruct dated TTM GAAP EPS vintages and amendment precedence before computing P/E.
9. Sample price dates by disclosure windows and measure effective, not nominal, observation count.
10. Treat one-time-item memos as non-quantitative until amount, tax basis, time, and recurrence are sourced.
11. Cross-check normalized financial totals against a second permitted official or issuer source.
12. Only then run one reproducible real valuation, review its calculation, and expand to four more.

No paid service, SEC access workaround, main merge, Streamlit deployment, or short-term-signal branch
change is part of this audit.
