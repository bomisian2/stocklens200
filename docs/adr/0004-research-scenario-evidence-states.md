# ADR 0004: Research scenario evidence states and non-operational valuation boundary

- Status: Accepted for Draft PR research
- Date: 2026-10-12
- Research model: `googl-scenario-research-v2.3.0`
- Formal valuation model remains: `valuation-v2.2.0`

## Context

GOOGL has verified issuer financial statements and a one-year CAPEX range, but it does not have a verified operating tax
rate, complete operating-working-capital history, point-in-time beta, formal WACC, complete enterprise-to-common-equity
bridge, or source-supported multi-year operating forecast. Hiding all scenario analysis would discard useful evidence, while
promoting assumptions to facts would create a false fair value.

## Decision

Every scenario input must be one of four states:

1. `observed`: a dated value in a traceable source;
2. `issuer_guidance`: a dated forward statement by the issuer, not an actual result;
3. `model_assumption`: an explicit research stress input with a rationale;
4. `missing`: null, never zero-filled.

The engine preserves the state and sources at field level. A research scenario can calculate projected FCFF and enterprise
value sensitivity, but it cannot populate formal FCFF, formal WACC, common-equity value, per-share fair value, or a buy/sell
signal. No WACC or terminal-growth cell is designated as the default. Cells are marked unstable when terminal value exceeds
80% of enterprise value or the WACC-minus-growth spread is below two percentage points.

Positive growth must be supported by positive net reinvestment. An explicit, assumption-labelled efficiency ceiling rejects
paths where incremental revenue per unit of net reinvestment is internally implausible. CAPEX-to-growth lag is recorded but
does not claim a measured causal return.

## Consequences

- Conservative, reference, and upside paths are research scenarios, not official price targets.
- A reference scenario is not promoted to a formal base fair value.
- Negative early FCFF caused by the investment cycle is retained rather than normalized away.
- Enterprise value may be shown, but per-share value remains null until the equity bridge is complete.
- Market-implied operating conditions remain underdetermined; one stock price cannot identify growth, margin, reinvestment,
  WACC, and terminal growth simultaneously.
- Scenario outputs remain disconnected from Streamlit operational decisions and scheduled trading signals.
