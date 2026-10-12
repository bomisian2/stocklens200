# Valuation follow-up priorities

The next work is ranked by evidence value divided by data, maintenance and model-risk cost.

1. **GOOGL normalized-earnings evidence** — map each securities gain to an issuer-stated after-tax effect; do not infer
   the missing TTM tax allocation.
2. **IR manifest generator** — generalize the reviewed hash/identity/period workflow without scraping issuer pages or
   assuming identical table layouts.
3. **Independent financial cross-check** — find a redistribution-compatible source before promoting the DCF.
4. **Point-in-time forecast evidence** — obtain a lawful dated forecast source or keep historical DCF provisional.
5. **Reverse DCF** — show the FCF growth implied by market price; label it as an expectation diagnostic, not value.
6. **Sensitivity surface** — publish a system-generated WACC/growth/margin grid around validated inputs, with no user
   tuning controls.
7. **Value-trap warning** — prioritize directly observable deterioration in revenue, operating margin, FCF conversion,
   leverage and ROIC; require multiple periods before changing state.
8. **NOPAT ROIC** — implement only after operating tax, excess cash and invested-capital definitions are fixed and
   reconciled; reject net-income ROIC for distorted earnings.
9. **State stability** — apply the existing hysteresis design to evidence-quality and value-trap transitions so one
   quarter near a boundary does not flip a label.
10. **Earnings-event filter** — first evaluate issuer IR calendar feeds and exchange/company filings; without a reliable
    timestamped source, do not block or encourage a trade.
11. **Long-term/short-term separation** — keep intrinsic-value status independent from technical entry signals and join
    them only in a later read-only explanation layer.
12. **Portfolio analytics ledger** — make the signal branch emit real daily positions, cash, costs and QQQ returns
    before using QuantStats.

Reverse DCF and sensitivity are the lowest-cost next analytical features, but neither should precede normalization and
independent-source work if the goal is a formal investment judgement. Value-trap warnings can begin with verified
historical facts. Earnings-event filters and portfolio performance depend on new timestamped datasets and rank later.
