# GOOGL B-grade research preview

## Scope

`research_preview.py` is an isolated Streamlit entry point for one GOOGL v2.4.1 research result. It does not modify or import
the production `app.py`, `data/valuations.csv`, `data/signals.csv`, Top-200 sorting, alerts, or scheduled refresh workflow.
The displayed snapshot lives at `research/googl-v2.4.1-preview.json`, outside the production `data/` directory.

The preview shows:

- `B-grade research reference · not an investment signal`;
- official financial date and U.S. market-session date;
- the $41.63–$84.88 outer research range with no official midpoint;
- claim and if-converted preferred branches;
- WACC, growth/margin and investment-value impact ranking;
- official filing identity, acceptance time and missing raw-file hash;
- formal blockers and `operational eligibility false / hold`.

It does not display a target price, buy zone, upside, undervaluation/overvaluation label or automatic recommendation.

## Hard data boundary

The loader rejects a snapshot unless all of these conditions hold:

- `dataset_kind=isolated_research_preview`;
- `fixture=false`;
- `operational_use=false`;
- signal, ranking and alert eligibility are all false;
- formal fair value and official midpoint are null;
- current-price comparison is disabled;
- judgement remains `hold`;
- recommendation, target-price, buy-zone, upside and buy/sell-signal keys are absent;
- the file is outside any directory named `data`.

The committed display values are compared against a fresh deterministic v2.4.1 audit during tests, preventing a fixture or
manually edited number from being shown as the actual research result.

## Responsive verification

The local isolated app was rendered directly in a browser at two explicit viewports:

| Viewport | Result |
|---|---|
| 390×844 mobile | no horizontal overflow (`scrollWidth=clientWidth=390`); six Streamlit columns stack to 359 px; badge, dates, range and hold state visible |
| 1440×900 desktop | no horizontal overflow (`scrollWidth=clientWidth=1440`); date and branch columns render at 436 px each |

Visual inspection found and fixed two issues before commit: the warning badge overlapped the fixed Streamlit header, and the
UTC market timestamp was being converted into a next-day KST date. The final screen uses extra header clearance and displays
the official U.S. market-session date (`2026-10-09`) separately from timestamps.

The first post-preview CI run occurred during the October 12 XNYS regular session. A cacheless provider request correctly
returned current-session timestamps, while StockLens correctly expected the latest *completed* October 9 session, so the
fresh-collection job failed. PR CI now detects an active regular session and the first 15 post-close minutes, records a notice,
and validates the committed last-completed snapshot instead. Outside that window it still performs the cacheless live
collection. The production freshness gate is unchanged and never accepts an incomplete session as completed data.

## Local preview

```powershell
streamlit run research_preview.py
```

This command starts only the isolated preview. The production command remains `streamlit run app.py`.

## Known usability limits

- The wide research envelope is intentionally not summarized by a midpoint; some users may still mistake endpoints for
  price targets, so the non-signal warning appears before the company name and again under the range.
- Source details and accounting calculations are collapsed by default to keep the mobile reading order short.
- Only GOOGL is supported; there is no ticker selector or user-adjustable valuation input.
- This preview has not been deployed and has no public preview URL.

