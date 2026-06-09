# Apex-Q Strategy Playbook (Quantitative Architecture)

The Python Execution Radar strictly executes these mathematical setups using CLOSED [-2] candles. Predictive limit orders are BANNED.

## 1. STRAT_LIQUIDITY_SWEEP (Mean Reversion / SFP)
- **Regime:** Choppy, Ranging, Weekend, or Squeeze (Low ADX).
- **Trigger:** Price wicks past the 30-candle support/resistance level to trigger retail stops, but the 15m candle **CLOSES** back inside the range.

## 2. STRAT_TREND_RECLAIM (Institutional FVG Defense)
- **Regime:** High Momentum / Trending (High ADX / RVOL > 1.1).
- **Trigger:** Price pulls back into a Fair Value Gap, but the 15m candle **CLOSES** back outside the midpoint, proving institutional defense.

## 3. HOLD_CASH (Capital Preservation)
- **Trigger:** High-impact USD Macro News. Radar is paused.