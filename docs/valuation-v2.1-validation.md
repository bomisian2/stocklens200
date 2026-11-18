# Valuation v2.1 validation report

- Branch: `feat/v2-valuation-engine`
- Model: `valuation-v2.1.0`
- Validation date: 2026-10-12
- Scope: GOOGL, AMAT, AVGO, QCOM, INTC

## Operational-data result

The isolated safety run used the committed verified universe but no operator-supplied SEC manifest
and no non-fixture valuation evidence bundle. The correct result was five selected companies, zero
verified financial periods, zero evaluated companies, zero historical-only provisional companies,
and five held companies with `manifest_not_configured`.

No fixture value was substituted and no fair price was produced. This is the only operational result
in this report. Unit-test examples validate formulas and gates, not current company values.

## Model comparison

| Case | Previous behavior | v2.1 behavior |
| --- | --- | --- |
| Missing historical P/E | Fixed sector P/E fallback | P/E unavailable; reason and observation count recorded |
| Distorted positive EPS | GOOGL-only core-EPS proxy | Normalized EPS requires dated, categorized adjustment evidence |
| Negative/structural EPS | Positive years could dominate a median | P/E disabled; INTC is a structural-loss regression case |
| EV/EBITDA | Fixed sector multiple | At least five dated comparable observations; quartile scenarios |
| Growth and multiple | Fixed growth coefficient | Growth/ROIC effects require accepted pre-holdout calibration |
| Growth and DCF | Historical scenarios | Forecast used once; otherwise explicitly `historical_only` |
| Macro | Potential additive price haircut | Rate enters capital cost; no VIX/oil/fixed macro haircut |
| Model weights | Fixed family weights | Inverse scenario-uncertainty weights |
| Value band | Fixed percentage-like bands | P/E/comparable quartiles and DCF forecast uncertainty |
| Safety margin | Confidence and family premium | Model/forecast/error/leverage/data uncertainty |
| Boundary changes | Immediate threshold change | Dispersion-derived hysteresis for a prior judgement |

## Automated validation

The suite covers deterministic reproduction, price independence, EPS adjustment evidence, negative
and structural EPS gates, INTC classification, future-filing exclusion, absence of fixed multiple
fallbacks, company-specific bands, rejected calibrations, hysteresis, fixture isolation, future-EPS
blocking, five issuer CIKs, stale-cache behavior, and Streamlit smoke tests.

Local result: `59 passed`. The isolated no-source gate also passed with
`valuation_count=5 evaluated=0 hold=5 model=valuation-v2.1.0 source=unverified`.

## Remaining limitations

1. Official SEC Company Facts/Submissions remain unavailable from the observed execution network.
2. No operational five-to-ten-year point-in-time price/EPS bundle has been supplied.
3. No redistribution-safe point-in-time analyst forecast source is configured.
4. No accepted independent holdout calibration exists for growth or ROIC; those P/E adjustments
   remain disabled.
5. VIX is not used for intrinsic value. Oil sensitivity is disabled until point-in-time issuer or
   industry evidence is available.
6. Historical-only DCF is `provisional`, keeps the investment judgement on hold, and is ineligible
   for new signals.
7. Fair value has no directly observable ground truth. Future returns and QQQ-relative performance
   are validation outcomes, not proof that an intrinsic-value estimate was correct.
8. Real model-selection and final-validation periods remain incomplete.

Main merge and Streamlit production deployment remain prohibited while PR #2 is Draft.
