# Point-in-time valuation evidence

`valuation-v2.1.0` does not invent a P/E, normalized EPS, comparable multiple, forecast, or safety
margin. Operational runs may provide `--valuation-evidence PATH`, where `PATH` is a JSON bundle with
`schema_version: 1`, `fixture: false`, `generated_at_utc`, and an `issuers` object keyed by ticker.
The loader hashes the complete file and rejects fixture bundles, future-generated bundles, malformed
timestamps, non-HTTPS sources, and historical P/E rows whose EPS became public after the price date.

## Required evidence by model

- P/E: at least 20 clean observations spanning five years. Each row records observation time, price,
  public TTM EPS, EPS availability time, risk-free rate, sector P/E when available, distortion flags,
  and separate price/EPS source URLs.
- Normalized EPS: `earnings_quality` records GAAP EPS, normalized EPS, quality state, loss assessment,
  publication time, source, and categorized adjustments. Negative, structural-loss, and unverified
  states disable P/E. A distorted state without traceable adjustments also disables P/E.
- EV/EBITDA: at least five comparable observations whose inputs were available on the observation date.
- Forecasts: optional EPS/FCF growth, dispersion and ROIC, all with publication time and source.
- Calibration: optional coefficients are ignored unless selection preceded an independent holdout,
  the holdout has at least 30 observations, and the calibration is explicitly accepted.

Historical-only FCF growth can produce a provisional DCF when verified SEC periods exist. The result
records `growth_basis=historical_only`; it is not represented as analyst consensus. Missing P/E
evidence never falls back to a hard-coded sector multiple.

## Fixture boundary

Unit tests use in-memory or `fixture: true` evidence. `scripts/update_valuations.py` calls the loader
with `allow_fixture=False`, so fixture data cannot enter an operational valuation. Actual results for
GOOGL, AMAT, AVGO, QCOM and INTC remain on hold until official financial inputs and a non-fixture,
source-attributed evidence bundle are both available.

## Band and state transition

The engine combines model scenario width, disagreement among models, forecast dispersion, historical
forecast error, leverage and data coverage. The largest supported uncertainty component becomes the
automatic safety margin, subject only to disclosed risk-control bounds. The favorable-buy interval is
derived from the conservative and base values after that margin. A buffer derived from valuation
dispersion is applied when a prior judgement exists, reducing boundary churn without mixing technical
signals into intrinsic value.
