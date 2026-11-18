# ADR-0003: FCFF economic-definition and enterprise-equity gates

- Status: Accepted for Draft PR validation
- Date: 2026-10-12
- Model version: `valuation-v2.2.0`

## Context

The first GOOGL research DCF discounts issuer-defined CFO minus CAPEX as an equity-cash-flow proxy. Renaming that value
FCFF would be economically incorrect because CFO is after interest and tax. A formal enterprise DCF additionally requires
an operating tax rate, operating working-capital changes, a WACC and a complete enterprise-to-common-equity bridge.

## Decision

1. Formal FCFF is defined as `NOPAT + D&A - CAPEX - increase in operating working capital`.
2. NOPAT requires a source-verified operating tax rate. Consolidated effective tax and an event-adjusted consolidated rate
   are research diagnostics, not automatic operating tax rates.
3. Working capital is built from classified operating components. Cash, securities, taxes and debt-like claims are excluded.
   Mixed `other` captions or a missing comparative component block formal change in operating NWC.
4. FCFF is discounted only with a source-verified WACC. Risk-free rate, beta, ERP, pre-tax debt cost, marginal tax rate and
   market-value capital weights remain separate evidence fields.
5. A data-quality premium is a model-confidence signal, not necessarily economic company risk. It cannot become a formal
   WACC component without calibration evidence.
6. Enterprise value converts to common equity only after verified cash, non-operating securities, debt, preferred claims,
   NCI, debt-like leases and other potential claims are classified consistently. Missing values are never treated as zero.
7. Non-marketable securities are excluded until operating/strategic ownership, liquidity, taxes and double-counting are
   resolved. Lease liabilities require a treatment consistent with EBIT and FCFF.
8. Revenue-to-FCFF scenarios require sourced revenue growth, operating margin, operating tax, D&A, CAPEX and working
   capital. Positive growth with non-positive net reinvestment is rejected unless capacity or asset-light evidence exists.
9. Reverse DCF remains `underdetermined` when one market price cannot identify all operating assumptions. It is a market-
   expectations diagnostic, not a forecast or target.
10. The CFO-minus-CAPEX model remains available only as `provisional`. It is not renamed or silently migrated to FCFF.

## Consequences

- GOOGL formal FCFF, WACC, enterprise value and per-share common-equity value remain null with the current evidence.
- The $64.44 result is reproducible but not operationally eligible.
- Additional source data can unlock each gate independently without changing the old model or manufacturing assumptions.
- Expanding to another issuer is postponed until the GOOGL economic-definition gate can be completed or its hold state is
  shown to be the correct outcome.

