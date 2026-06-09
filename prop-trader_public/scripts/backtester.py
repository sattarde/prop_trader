import urllib.request
import json
import time
import os
from datetime import datetime

BASE_URL = "https://contract.mexc.com"

# --- NATIVE MATH & INDICATORS (DIRECT FROM CODES) ---
def calculate_ema(prices, window):
    if not prices or len(prices) < window: return [prices[-1]] * len(prices)
    ema = [sum(prices[:window]) / window]
    multiplier = 2 / (window + 1)
    for price in prices[window:]: ema.append((price - ema[-1]) * multiplier + ema[-1])
    return ema

def calculate_macd_native(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow + signal: return 0.0, 0.0, 0.0
    ema_fast = calculate_ema(closes, fast)
    ema_slow = calculate_ema(closes, slow)
    diff = len(ema_fast) - len(ema_slow)
    ema_fast_aligned = ema_fast[diff:] if diff > 0 else ema_fast
    ema_slow_aligned = ema_slow[-diff:] if diff < 0 else ema_slow
    macd_line = [f - s for f, s in zip(ema_fast_aligned, ema_slow_aligned)]
    signal_line = calculate_ema(macd_line, signal)
    histogram = macd_line[-1] - signal_line[-1]
    return round(macd_line[-1], 2), round(signal_line[-1], 2), round(histogram, 2)

def calculate_rsi_native(prices, window=14):
    if len(prices) < window + 1: return 50.0
    gains, losses = [], []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i-1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains[:window]) / window
    avg_loss = sum(losses[:window]) / window
    if avg_loss == 0: return 100.0
    rs = avg_gain / avg_loss
    rsi = [100 - (100 / (1 + rs))]
    for i in range(window, len(gains)):
        avg_gain = (avg_gain * (window - 1) + gains[i]) / window
        avg_loss = (avg_loss * (window - 1) + losses[i]) / window
        if avg_loss == 0: rsi.append(100.0)
        else: rsi.append(100 - (100 / (1 + (avg_gain / avg_loss))))
    return round(rsi[-1], 2)

# --- FRACTAL SWING & STRUCTURE ENGINE (PART 2 CONCEPT) ---
def detect_swing_structure(highs, lows, closes, length=30):
    """
    Identifies major swing structure (Swing Highs and Swing Lows) in a window.
    Returns: (swing_high, swing_low, equilibrium, premium_discount_ratio)
    """
    if len(closes) < length:
        return max(highs), min(lows), (max(highs) + min(lows))/2, 0.5
    
    swing_high = max(highs[-length:])
    swing_low = min(lows[-length:])
    
    # Premium / Discount equilibrium
    equilibrium = (swing_high + swing_low) / 2
    
    # Calculate where current price is relative to swing structure (0.0 = bottom, 1.0 = top)
    current_price = closes[-1]
    range_size = swing_high - swing_low
    ratio = (current_price - swing_low) / range_size if range_size > 0 else 0.5
    
    return swing_high, swing_low, equilibrium, ratio

# --- DATA FETCHING ---
def get_mexc_klines_backtest(symbol, interval, limit=1000):
    req = urllib.request.Request(
        f"{BASE_URL}/api/v1/contract/kline/{symbol}?interval={interval}&limit={limit}",
        headers={"User-Agent": "Mozilla/5.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("success"):
                d = data["data"]
                return {"close":[float(x) for x in d["close"]], "high":[float(x) for x in d["high"]], "low":[float(x) for x in d["low"]], "vol":[float(x) for x in d["vol"]]}
    except Exception as e:
        print(f"Error fetching data for {symbol}: {e}")
    return None

# --- BACKTEST RUNNER CLASS ---
class Backtester:
    def __init__(self, data, strategy_type="baseline", timeframe="1H"):
        self.close = data["close"]
        self.high = data["high"]
        self.low = data["low"]
        self.vol = data["vol"]
        self.strategy_type = strategy_type
        self.timeframe = timeframe
        self.trades = []
        self.active_trade = None
        
    def run(self):
        start_idx = 45 # Buffer for indicators to stabilize
        limit = len(self.close)
        
        for t in range(start_idx, limit):
            # 1. Check open trade exit conditions first
            if self.active_trade:
                trade = self.active_trade
                high_t = self.high[t]
                low_t = self.low[t]
                
                closed = False
                pnl = 0.0
                
                if trade["side"] == "LONG":
                    if low_t <= trade["sl"]:
                        pnl = -1.0 # Loss
                        closed = True
                    elif high_t >= trade["tp"]:
                        pnl = 2.0 if self.timeframe == "1H" else 3.0 # Reward (1H is 1:2 R:R, 4H is 1:3 R:R)
                        closed = True
                elif trade["side"] == "SHORT":
                    if high_t >= trade["sl"]:
                        pnl = -1.0 # Loss
                        closed = True
                    elif low_t <= trade["tp"]:
                        pnl = 2.0 if self.timeframe == "1H" else 3.0 # Reward
                        closed = True
                        
                if closed:
                    trade["pnl"] = pnl
                    trade["close_idx"] = t
                    self.trades.append(trade)
                    self.active_trade = None
                    continue # Wait until next candle to look for a new entry
            
            # 2. If no active trade, look for entry signal on candle t-1
            # We use historical data up to t-1 to avoid forward-looking bias
            sub_close = self.close[:t]
            sub_high = self.high[:t]
            sub_low = self.low[:t]
            sub_vol = self.vol[:t]
            
            # Simulated completed candle t-1
            closed_c = sub_close[-1]
            closed_h = sub_high[-1]
            closed_l = sub_low[-1]
            closed_v = sub_vol[-1]
            
            avg_vol = sum(sub_vol[-21:-1]) / 20 if sum(sub_vol[-21:-1]) > 0 else 1
            current_rvol = closed_v / avg_vol
            _, _, macd_hist = calculate_macd_native(sub_close)
            
            # Run specific strategy scan
            signal = None
            
            # A. BASELINE (CURRENT STRATEGY: Stale-First, No Mitigation Check)
            if self.strategy_type == "baseline":
                lookback = 10 if self.timeframe == "1H" else 15
                for i in range(len(sub_close) - lookback, len(sub_close) - 2):
                    if sub_low[i] > sub_high[i-2]: # Bullish FVG
                        mid = (sub_low[i] + sub_high[i-2]) / 2
                        if closed_l <= mid < closed_c and macd_hist > 0 and current_rvol > 1.0:
                            sl = sub_high[i-2] * (0.999 if self.timeframe == "1H" else 0.995)
                            dist = abs(closed_c - sl) / closed_c
                            # SL cushion
                            min_dist = 0.015 if self.timeframe == "1H" else 0.035
                            if dist < min_dist:
                                dist = min_dist
                                sl = closed_c * (1 - dist)
                            tp = closed_c * (1 + dist * (2.0 if self.timeframe == "1H" else 3.0))
                            signal = {"side": "LONG", "entry": closed_c, "sl": sl, "tp": tp, "open_idx": t}
                            break
                    elif sub_high[i] < sub_low[i-2]: # Bearish FVG
                        mid = (sub_high[i] + sub_low[i-2]) / 2
                        if closed_h >= mid > closed_c and macd_hist < 0 and current_rvol > 1.0:
                            sl = sub_low[i-2] * (1.001 if self.timeframe == "1H" else 1.005)
                            dist = abs(sl - closed_c) / closed_c
                            min_dist = 0.015 if self.timeframe == "1H" else 0.035
                            if dist < min_dist:
                                dist = min_dist
                                sl = closed_c * (1 + dist)
                            tp = closed_c * (1 - dist * (2.0 if self.timeframe == "1H" else 3.0))
                            signal = {"side": "SHORT", "entry": closed_c, "sl": sl, "tp": tp, "open_idx": t}
                            break
                            
            # B. UPGRADE A: FVG + Mitigation Validation
            elif self.strategy_type == "upgrade_a":
                lookback = 10 if self.timeframe == "1H" else 15
                for i in range(len(sub_close) - lookback, len(sub_close) - 2):
                    if sub_low[i] > sub_high[i-2]: # Bullish FVG
                        # Upgrade A: Check if FVG was already mitigated
                        mitigated = False
                        for j in range(i + 1, len(sub_close) - 1):
                            if sub_close[j] < sub_high[i-2]:
                                mitigated = True
                                break
                        if mitigated: continue
                        
                        mid = (sub_low[i] + sub_high[i-2]) / 2
                        if closed_l <= mid < closed_c and macd_hist > 0 and current_rvol > 1.0:
                            sl = sub_high[i-2] * (0.999 if self.timeframe == "1H" else 0.995)
                            dist = abs(closed_c - sl) / closed_c
                            min_dist = 0.015 if self.timeframe == "1H" else 0.035
                            if dist < min_dist:
                                dist = min_dist
                                sl = closed_c * (1 - dist)
                            tp = closed_c * (1 + dist * (2.0 if self.timeframe == "1H" else 3.0))
                            signal = {"side": "LONG", "entry": closed_c, "sl": sl, "tp": tp, "open_idx": t}
                            break
                    elif sub_high[i] < sub_low[i-2]: # Bearish FVG
                        # Upgrade A: Check if FVG was already mitigated
                        mitigated = False
                        for j in range(i + 1, len(sub_close) - 1):
                            if sub_close[j] > sub_low[i-2]:
                                mitigated = True
                                break
                        if mitigated: continue
                        
                        mid = (sub_high[i] + sub_low[i-2]) / 2
                        if closed_h >= mid > closed_c and macd_hist < 0 and current_rvol > 1.0:
                            sl = sub_low[i-2] * (1.001 if self.timeframe == "1H" else 1.005)
                            dist = abs(sl - closed_c) / closed_c
                            min_dist = 0.015 if self.timeframe == "1H" else 0.035
                            if dist < min_dist:
                                dist = min_dist
                                sl = closed_c * (1 + dist)
                            tp = closed_c * (1 - dist * (2.0 if self.timeframe == "1H" else 3.0))
                            signal = {"side": "SHORT", "entry": closed_c, "sl": sl, "tp": tp, "open_idx": t}
                            break
                            
            # C. UPGRADE A+B: Newest-First + Mitigation Validation
            elif self.strategy_type == "upgrade_a_b":
                lookback = 10 if self.timeframe == "1H" else 15
                # Upgrade B: Loop backwards from t-3 to t-lookback
                for i in range(len(sub_close) - 3, len(sub_close) - lookback - 1, -1):
                    if i-2 < 0: continue
                    if sub_low[i] > sub_high[i-2]: # Bullish FVG
                        mitigated = False
                        for j in range(i + 1, len(sub_close) - 1):
                            if sub_close[j] < sub_high[i-2]:
                                mitigated = True
                                break
                        if mitigated: continue
                        
                        mid = (sub_low[i] + sub_high[i-2]) / 2
                        if closed_l <= mid < closed_c and macd_hist > 0 and current_rvol > 1.0:
                            sl = sub_high[i-2] * (0.999 if self.timeframe == "1H" else 0.995)
                            dist = abs(closed_c - sl) / closed_c
                            min_dist = 0.015 if self.timeframe == "1H" else 0.035
                            if dist < min_dist:
                                dist = min_dist
                                sl = closed_c * (1 - dist)
                            tp = closed_c * (1 + dist * (2.0 if self.timeframe == "1H" else 3.0))
                            signal = {"side": "LONG", "entry": closed_c, "sl": sl, "tp": tp, "open_idx": t}
                            break
                    elif sub_high[i] < sub_low[i-2]: # Bearish FVG
                        mitigated = False
                        for j in range(i + 1, len(sub_close) - 1):
                            if sub_close[j] > sub_low[i-2]:
                                mitigated = True
                                break
                        if mitigated: continue
                        
                        mid = (sub_high[i] + sub_low[i-2]) / 2
                        if closed_h >= mid > closed_c and macd_hist < 0 and current_rvol > 1.0:
                            sl = sub_low[i-2] * (1.001 if self.timeframe == "1H" else 1.005)
                            dist = abs(sl - closed_c) / closed_c
                            min_dist = 0.015 if self.timeframe == "1H" else 0.035
                            if dist < min_dist:
                                dist = min_dist
                                sl = closed_c * (1 + dist)
                            tp = closed_c * (1 - dist * (2.0 if self.timeframe == "1H" else 3.0))
                            signal = {"side": "SHORT", "entry": closed_c, "sl": sl, "tp": tp, "open_idx": t}
                            break

            # D. PART 2 (FULL MARKET STRUCTURE): Upgrade A+B + Premium/Discount Constraint
            elif self.strategy_type == "part_2":
                lookback = 10 if self.timeframe == "1H" else 15
                # Get swing structure on current dataset up to t-1
                swing_high, swing_low, eq, discount_ratio = detect_swing_structure(sub_high, sub_low, sub_close, length=30)
                
                for i in range(len(sub_close) - 3, len(sub_close) - lookback - 1, -1):
                    if i-2 < 0: continue
                    if sub_low[i] > sub_high[i-2]: # Bullish FVG
                        mitigated = False
                        for j in range(i + 1, len(sub_close) - 1):
                            if sub_close[j] < sub_high[i-2]:
                                mitigated = True
                                break
                        if mitigated: continue
                        
                        # Part 2: Premium/Discount Rule (Only Long in DISCOUNT zone, i.e., ratio < 0.5)
                        if discount_ratio > 0.50: continue
                        
                        mid = (sub_low[i] + sub_high[i-2]) / 2
                        if closed_l <= mid < closed_c and macd_hist > 0 and current_rvol > 1.0:
                            sl = sub_high[i-2] * (0.999 if self.timeframe == "1H" else 0.995)
                            dist = abs(closed_c - sl) / closed_c
                            min_dist = 0.015 if self.timeframe == "1H" else 0.035
                            if dist < min_dist:
                                dist = min_dist
                                sl = closed_c * (1 - dist)
                            tp = closed_c * (1 + dist * (2.0 if self.timeframe == "1H" else 3.0))
                            signal = {"side": "LONG", "entry": closed_c, "sl": sl, "tp": tp, "open_idx": t}
                            break
                    elif sub_high[i] < sub_low[i-2]: # Bearish FVG
                        mitigated = False
                        for j in range(i + 1, len(sub_close) - 1):
                            if sub_close[j] > sub_low[i-2]:
                                mitigated = True
                                break
                        if mitigated: continue
                        
                        # Part 2: Premium/Discount Rule (Only Short in PREMIUM zone, i.e., ratio > 0.50)
                        if discount_ratio < 0.50: continue
                        
                        mid = (sub_high[i] + sub_low[i-2]) / 2
                        if closed_h >= mid > closed_c and macd_hist < 0 and current_rvol > 1.0:
                            sl = sub_low[i-2] * (1.001 if self.timeframe == "1H" else 1.005)
                            dist = abs(sl - closed_c) / closed_c
                            min_dist = 0.015 if self.timeframe == "1H" else 0.035
                            if dist < min_dist:
                                dist = min_dist
                                sl = closed_c * (1 + dist)
                            tp = closed_c * (1 - dist * (2.0 if self.timeframe == "1H" else 3.0))
                            signal = {"side": "SHORT", "entry": closed_c, "sl": sl, "tp": tp, "open_idx": t}
                            break
            
            if signal:
                self.active_trade = signal
                
        # Calculate final stats
        total_trades = len(self.trades)
        if total_trades == 0:
            return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0, "net_r": 0.0}
            
        wins = sum(1 for x in self.trades if x["pnl"] > 0)
        losses = total_trades - wins
        win_rate = (wins / total_trades) * 100
        net_r = sum(x["pnl"] for x in self.trades)
        
        return {
            "total": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 2),
            "net_r": round(net_r, 2)
        }

def run_backtest_suite():
    # 5 Major Assets
    assets = ["BTC", "ETH", "SOL", "TAO", "NEAR"]
    timeframes = [("1H", "Min60"), ("4H", "Hour4")]
    
    print("\n" + "="*70)
    print("🚀 APEX-Q HISTORICAL BACKTEST SUITE (1000 CANDLES)")
    print("----------------------------------------------------------------------")
    print("Timeframe 1H  = ~41 Days  | Reward: 2.0R (Win) / -1.0R (Loss)")
    print("Timeframe 4H  = ~166 Days | Reward: 3.0R (Win) / -1.0R (Loss)")
    print("="*70)
    
    for tf_label, tf_interval in timeframes:
        print(f"\n⚡ TIMEFRAME: {tf_label} ({tf_interval})")
        print("-"*70)
        print(f"{'Asset':<8} | {'Baseline':^12} | {'Upgrade A':^12} | {'Upgrd A+B':^12} | {'Part 2 (MS)':^12}")
        print(f"{'':<8} | {'(Win% / NetR)':^12} | {'(Win% / NetR)':^12} | {'(Win% / NetR)':^12} | {'(Win% / NetR)':^12}")
        print("-"*70)
        
        totals = {
            "baseline": {"trades": 0, "net_r": 0.0, "wins": 0},
            "upgrade_a": {"trades": 0, "net_r": 0.0, "wins": 0},
            "upgrade_a_b": {"trades": 0, "net_r": 0.0, "wins": 0},
            "part_2": {"trades": 0, "net_r": 0.0, "wins": 0}
        }
        
        for asset in assets:
            data = get_mexc_klines_backtest(f"{asset}_USDT", tf_interval, limit=1000)
            if not data:
                print(f"{asset:<8} | Offline")
                continue
                
            # Run the 4 strategies
            s_baseline = Backtester(data, "baseline", tf_label).run()
            s_upgrade_a = Backtester(data, "upgrade_a", tf_label).run()
            s_upgrade_ab = Backtester(data, "upgrade_a_b", tf_label).run()
            s_part2 = Backtester(data, "part_2", tf_label).run()
            
            # Accumulate totals
            for s_name, s_res in [("baseline", s_baseline), ("upgrade_a", s_upgrade_a), ("upgrade_a_b", s_upgrade_ab), ("part_2", s_part2)]:
                totals[s_name]["trades"] += s_res["total"]
                totals[s_name]["net_r"] += s_res["net_r"]
                totals[s_name]["wins"] += s_res["wins"]
                
            # Format cell strings
            def fmt(r):
                if r["total"] == 0: return "0/0.0R"
                return f"{r['win_rate']:.0f}%/{r['net_r']:.1f}R"
                
            print(f"{asset:<8} | {fmt(s_baseline):^12} | {fmt(s_upgrade_a):^12} | {fmt(s_upgrade_ab):^12} | {fmt(s_part2):^12}")
            time.sleep(0.5)
            
        print("-"*70)
        # Summary row
        def fmt_total(s_name):
            t = totals[s_name]
            if t["trades"] == 0: return "0/0.0R"
            wr = (t["wins"] / t["trades"]) * 100
            return f"{wr:.0f}%/{t['net_r']:.1f}R"
            
        print(f"{'OVERALL':<8} | {fmt_total('baseline'):^12} | {fmt_total('upgrade_a'):^12} | {fmt_total('upgrade_a_b'):^12} | {fmt_total('part_2'):^12}")
        print("="*70)

if __name__ == "__main__":
    run_backtest_suite()
