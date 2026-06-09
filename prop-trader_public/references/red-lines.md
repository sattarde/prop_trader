# Apex-Q Prop Fund Red Lines & Risk Math

Strict quantitative adherence guarantees mathematical survival.

## 1. Hard Boundaries ($50K Challenge)
- **Max Daily Loss (3%):** $1,500 hard daily stop relative to start-of-day balance.
- **Max Trailing Drawdown (5%):** Account equity must never drop below $47,500 absolute floor.
- **Max Leverage:** 5x absolute maximum.

## 2. Asymmetric Risk Sizing (The Core Edge)
Fixed 1% or 2% risk is BANNED. Apex-Q dynamically outputs the `risk_per_trade_pct` in the JSON payload based on Account Drawdown:
- **DEFENSE MODE (Drawdown > 3.0%):** Risk strictly **0.10%**.
- **STANDARD MODE (Base Equity $48.5K - $51K):** Risk strictly **0.25%**.
- **HOUSE MONEY MODE (Profit > $51K):** Risk strictly **0.35%**.

## 3. Correlated Asset Protocol
Because the Radar scans `TOP_20` assets, one macro drop can trigger 10 simultaneous alarms. The Human Executor must manually select the TOP 1 or 2 setups. Executing 10 trades at once violates the risk limits.