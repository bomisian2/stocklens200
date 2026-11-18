# ADR 0005: Enterprise value to common-equity bridge gate

- Status: Accepted for Draft PR research
- Date: 2026-10-12
- Research audits: `googl-equity-bridge-audit-v2.4.0`, `googl-practical-reference-audit-v2.4.1`
- Formal valuation model remains: `valuation-v2.2.0`

## Context

StockLens has a v2.3 research enterprise-value sensitivity surface for GOOGL, but enterprise value does not belong entirely
to common shareholders. Cash and investments may include operating cash, restrictions and tax leakage; debt, leases, NCI,
preferred shares, legal claims and contingent commitments require mutually consistent treatment. Period-weighted diluted EPS
shares are not a point-in-time fully diluted valuation denominator.

## Decision

Each bridge item carries two independent labels:

1. evidence basis: `official_observed`, `book_value_proxy`, `model_estimate`, or `missing`;
2. bridge treatment: `add`, `subtract`, `disclosure_only`, or `unresolved`.

A reported balance is not automatically eligible for arithmetic. Missing values stay null and cannot be added or subtracted.
The model applies three value states:

- **A**: formal value range with sufficient official inputs, forecast support and a complete accounting-consistent bridge;
- **B**: research range using official actuals and explicitly identified estimates, never an operating signal;
- **C**: required inputs are incomplete, so the relevant equity or per-share output is unavailable.

The initial v2.4 audit left GOOGL at B-grade enterprise value and C-grade common-equity/per-share outputs. The v2.4.1
addendum permits a B-grade per-share *research envelope* only when every endpoint is an explicit bound with a source/basis,
no midpoint or probability is selected, mutually exclusive claim policies are shown separately, and the result remains
non-operational. This does not promote the result to A-grade formal fair value.

Operating lease liabilities cannot be subtracted when FCFF begins with unadjusted GAAP EBIT that already includes operating
lease expense. Mandatory convertible preferred is handled either as a claim or through if-converted dilution, never both.
Issued shares and period-weighted diluted shares cannot substitute for point-in-time fully diluted common shares.

## Consequences

- No unexplained partial net-cash bridge is divided by shares. A B-grade envelope may divide explicitly bounded bridge values
  by explicitly bounded share counts, provided both are labelled mechanical research limits rather than verified fair value.
- Official carrying values can be displayed as evidence while remaining ineligible for formal arithmetic.
- Investment gains excluded from operating income are not automatically double-counted, but investment balances still need
  operating/non-operating, restriction, liquidity and tax classification before addition.
- Legal accruals and contingent funding commitments remain disclosures until forecasts and claim treatment are reconciled.
- A mechanical dollars-per-issued-share scale diagnostic is explicitly not fair value.
- Streamlit, scheduled valuation output and buy/sell decisions remain unchanged.

