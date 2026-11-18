---
name: quant-regime-playbook
description: >-
  Institutional regime-conditional position sizing, execution algorithm selection
  (TWAP vs LIMIT), and stop-loss placement rules for AlphaPulse trading agents.
---

# Institutional Quantitative Regime Playbook

Apply these deterministic rules whenever proposing or reviewing an equity or ETF trade:

1. **Market Regime Sizing Multipliers**:
   - `TRENDING_BULLISH` / `TRENDING_BEARISH`: Allow up to `100%` of the trader's compliant single-order budget when `alpha_signal_score` aligns with the trend (`|score| >= 0.40`).
   - `MEAN_REVERTING_RANGE`: Scale target order size to `50%` of standard notional and prefer passive `LIMIT` orders near VWAP or outer Bollinger Bands (`percent_b < 0.20` for buys, `percent_b > 0.80` for sells).
   - `HIGH_VOLATILITY_BREAKOUT`: Scale target order size to `40%` of standard notional to respect the `3.5%` 1-day 95% portfolio VaR ceiling, and enforce `TWAP` execution to minimize L2 slippage.

2. **Mandatory Pre-Trade Checks**:
   - Never exceed `$50,000` single-order notional or `25%` single-position NAV weight.
   - Always attach a protective stop-loss anchored at `1.8 * atr_proxy` away from entry.
   - Lock approved execution quotes via an **AP2 v0.2 `CartMandate`** before settlement.
