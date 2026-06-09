import time
import json
import os
import sys
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime

# --- CONFIGURATION & PATHS ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
CONFIG_PATH = os.path.join(BASE_DIR, "trader_v1_config.json")
BASE_URL = "https://contract.mexc.com"

if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

try:
    import twilio_alert
except ImportError:
    print("⚠️ twilio_alert.py not found in the scripts folder. WhatsApp alerts disabled.")
    twilio_alert = None

def get_env_variable(key, env_path="/Users/sattarde/.gemini/skills/prop-trader/.env"):
    if key in os.environ: return os.environ[key]
    try:
        with open(env_path, "r") as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    if k.strip() == key: return v.strip().strip(' "\'')
    except Exception: pass
    return None

# --- ACCOUNT LEDGER & SHADOW TRACKER ---
def get_account_state():
    state_file = os.path.join(BASE_DIR, "prop_state.json")
    if not os.path.exists(state_file):
        state = {"start_balance": 50000.0, "current_equity": 50000.0, "high_water_mark": 50000.0, "active_trades": []}
        with open(state_file, 'w') as f: json.dump(state, f, indent=4)
        return state
    try:
        with open(state_file, 'r') as f: state = json.load(f)
        if "active_trades" not in state: state["active_trades"] = []
        if "current_equity" not in state: state["current_equity"] = 50000.0
        if "high_water_mark" not in state: state["high_water_mark"] = 50000.0
        eq = float(state["current_equity"])
        peak = float(state["high_water_mark"])
        if eq > peak:
            state["high_water_mark"] = eq
            with open(state_file, 'w') as f: json.dump(state, f, indent=4)
        return state
    except:
        return {"start_balance": 50000.0, "current_equity": 50000.0, "high_water_mark": 50000.0, "active_trades": []}

def save_account_state(state):
    state_file = os.path.join(BASE_DIR, "prop_state.json")
    with open(state_file, 'w') as f: json.dump(state, f, indent=4)

def update_open_trades_pnl():
    state = get_account_state()
    active_trades = state.get("active_trades", [])
    if not active_trades: return
    
    remaining_trades = []
    equity = state["current_equity"]
    state_changed = False
    
    for t in active_trades:
        k = get_mexc_klines(f"{t['asset']}_USDT", "Min5", limit=5)
        if not k:
            remaining_trades.append(t)
            continue
            
        closed = False
        pnl = 0.0
        dist_sl = abs(t['entry'] - t['sl']) / t['entry']
        dist_tp = abs(t['tp'] - t['entry']) / t['entry']
        rr = dist_tp / dist_sl if dist_sl > 0 else 2.0
        reward = t['risk_amt'] * rr
        loss = -t['risk_amt']
        
        for h, l in zip(k['high'], k['low']):
            if t['side'] == "LONG":
                if l <= t['sl']: pnl = loss; closed = True; break
                elif h >= t['tp']: pnl = reward; closed = True; break
            else: 
                if h >= t['sl']: pnl = loss; closed = True; break
                elif l <= t['tp']: pnl = reward; closed = True; break
        
        if closed:
            equity += pnl
            state_changed = True
            result = "✅ WIN" if pnl > 0 else "❌ LOSS"
            close_msg = (f"🔔 V1 SHADOW TRACKER 🔔\n"
                         f"Asset: {t['asset']} ({t['side']})\n"
                         f"Result: {result} (${abs(pnl):,.2f})\n"
                         f"New Equity: ${equity:,.2f}")
            print("\n" + "="*40); print(close_msg); print("="*40 + "\n")
            if twilio_alert: twilio_alert.send_whatsapp(close_msg)
            
            if "trade_history" not in state: state["trade_history"] = []
            t["pnl"] = pnl
            t["result"] = "WIN" if pnl > 0 else "LOSS"
            t["close_time"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            state["trade_history"].insert(0, t)
            state["trade_history"] = state["trade_history"][:50]
        else:
            remaining_trades.append(t)
            
    if state_changed:
        state['current_equity'] = equity
        if equity > state.get('high_water_mark', 50000.0): state['high_water_mark'] = equity
    if state_changed or len(remaining_trades) != len(active_trades):
        state['active_trades'] = remaining_trades
        save_account_state(state)

# --- GEMINI AI INTEGRATION ---
def ask_gemini_apex_q(payload):
    api_key = get_env_variable("GEMINI_API_KEY")
    if not api_key or not api_key.startswith("AIza"): return None
        
    system_prompt = """You are Apex-Q V1, Chief Quantitative Strategist for a Prop Firm INTRADAY setup (1H).
Analyze the STATE PAYLOAD. Output ONLY a valid JSON object. No conversational text.
Your horizon is 12-24 hours. You seek steady 3-6% intraday swings.
Schema requirement:
{
  "market_regime": "Trending_Bullish | Trending_Bearish | Choppy_Ranging | Volatility_Squeeze",
  "reasoning_summary": "1 sentence logic explanation connecting Trend, Volume, RSI, and MACD",
  "active_strategy": "STRAT_TREND_RECLAIM | STRAT_LIQUIDITY_SWEEP | HOLD_CASH",
  "directional_bias": "LONG_ONLY | SHORT_ONLY | NEUTRAL",
  "approved_assets": ["TOP_20"],
  "risk_per_trade_pct": 0.25,
  "action_override": "NONE"
}
Rules: 
- If macro news exists for today, strategy MUST be HOLD_CASH.
- If 1H ADX is > 25, use STRAT_TREND_RECLAIM. If 1H ADX < 20, use STRAT_LIQUIDITY_SWEEP.
- 🚨 OSCILLATOR RULES: If 1H RSI > 70 (Overbought) -> Favor SHORT_ONLY. If 1H RSI < 30 (Oversold) -> Favor LONG_ONLY.
- 🚨 MACD RULE: If MACD Histogram shows momentum opposing the 1D Trend, deploy NEUTRAL bias to prevent traps.
- Risk MUST be 0.10 (if Drawdown > 3.0%), 0.25 (Standard), or 0.35 (Profit > $51K)."""

    data = {"system_instruction": {"parts": [{"text": system_prompt}]}, "contents": [{"parts": [{"text": f"STATE PAYLOAD:\n{payload}"}]}], "generationConfig": {"response_mime_type": "application/json", "temperature": 0.1}}
    for model in ["gemini-pro-latest", "gemini-flash-latest", "gemini-3.5-flash", "gemini-3.1-pro-preview"]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key.strip()}"
        req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers={'Content-Type': 'application/json'}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                text_out = json.loads(resp.read().decode('utf-8'))['candidates'][0]['content']['parts'][0]['text']
                text_out = text_out.replace('```json', '').replace('```', '').strip()
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 🧠 V1 (1H) Successfully Connected via: {model}")
                return json.loads(text_out)
        except: continue
    return None

# --- NATIVE QUANTITATIVE UTILS (UPGRADED) ---
def calculate_ema(prices, window):
    if not prices or len(prices) < window: return [prices[-1]] * len(prices)
    ema = [sum(prices[:window]) / window]
    multiplier = 2 / (window + 1)
    for price in prices[window:]: ema.append((price - ema[-1]) * multiplier + ema[-1])
    return ema

def calculate_macd_native(closes, fast=12, slow=26, signal=9):
    """Natively calculates MACD to detect Institutional Momentum shifts."""
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
    """Natively calculates RSI for Mean Reversion extremes."""
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

def calculate_adx_native(highs, lows, closes, window=14):
    if len(closes) < window * 2: return 20.0
    tr, plus_dm, minus_dm = [], [], []
    for i in range(1, len(closes)):
        h, l, c_p = highs[i], lows[i], closes[i-1]
        hp, lp = highs[i-1], lows[i-1]
        tr.append(max(h - l, abs(h - c_p), abs(l - c_p)))
        u, d = h - hp, lp - l
        plus_dm.append(u if u > d and u > 0 else 0)
        minus_dm.append(d if d > u and d > 0 else 0)

    def smooth(data, win):
        if not data: return []
        s = [sum(data[:win]) / win]
        alpha = 1 / win
        for val in data[win:]: s.append(val * alpha + s[-1] * (1 - alpha))
        return s

    tr_s, p_dm_s, m_dm_s = smooth(tr, window), smooth(plus_dm, window), smooth(minus_dm, window)
    dx = []
    for i in range(len(tr_s)):
        p_di = 100 * (p_dm_s[i] / tr_s[i]) if tr_s[i] > 0 else 0
        m_di = 100 * (m_dm_s[i] / tr_s[i]) if tr_s[i] > 0 else 0
        dx.append(100 * abs(p_di - m_di) / (p_di + m_di) if (p_di + m_di) > 0 else 0)
    adx = smooth(dx, window)
    return round(adx[-1], 2) if adx else 20.0

def calculate_market_structure_native(highs, lows, closes, vols):
    if len(closes) < 20: return 1.0, 0.0, "Unknown"
    avg_vol = sum(vols[-21:-1]) / 20
    rvol = round(vols[-1] / avg_vol, 2) if avg_vol > 0 else 1.0
    tr = []
    for i in range(len(closes)-15, len(closes)):
        tr.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    atr_pct = round(((sum(tr)/len(tr)) / closes[-1]) * 100, 2)
    state = "Expanding" if atr_pct > 1.5 else "Compressing (Squeeze Imminent)"
    return rvol, atr_pct, state

def get_top_20_mexc_assets():
    req = urllib.request.Request(f"{BASE_URL}/api/v1/contract/ticker", headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("success"):
                tickers = [t for t in data["data"] if t['symbol'].endswith('_USDT')]
                sorted_pairs = sorted(tickers, key=lambda x: float(x.get('amount24', 0)), reverse=True)
                top_20 = [t['symbol'].replace('_USDT', '') for t in sorted_pairs if t['symbol'].replace('_USDT', '') not in ["USDC", "BUSD", "TUSD", "FDUSD", "USDE"]]
                return top_20[:20]
    except: return ["BTC", "ETH", "SOL"]
    return ["BTC", "ETH", "SOL"]

def get_mexc_klines(symbol, interval, limit=100):
    req = urllib.request.Request(f"{BASE_URL}/api/v1/contract/kline/{symbol}?interval={interval}&limit={limit}", headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("success"):
                d = data["data"]
                return {"close":[float(x) for x in d["close"]], "high":[float(x) for x in d["high"]], "low":[float(x) for x in d["low"]], "vol":[float(x) for x in d["vol"]]}
    except: return None
    return None

def get_sentiment():
    try:
        req = urllib.request.Request("https://api.alternative.me/fng/", headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            d = json.loads(resp.read().decode())
            return d['data'][0]['value'], d['data'][0]['value_classification']
    except: return "Unknown", "Unknown"

def get_funding_rate():
    try:
        req = urllib.request.Request("https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT", headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            d = json.loads(resp.read().decode())
            return float(d['lastFundingRate']) * 100
    except: return 0.0

def get_ls_ratio():
    try:
        req = urllib.request.Request("https://fapi.binance.com/futures/data/topLongShortPositionRatio?symbol=BTCUSDT&period=1d&limit=1", headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            d = json.loads(resp.read().decode())
            return float(d[0]['longShortRatio'])
    except: return 1.0

def get_macro_status():
    try:
        req = urllib.request.Request("https://nfs.faireconomy.media/ff_calendar_thisweek.xml", headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            root = ET.fromstring(resp.read())
        events = []
        today = datetime.now().strftime("%m-%d-%Y")
        for item in root.findall('item'):
            if item.find('impact') is not None and item.find('impact').text == 'High' and item.find('country') is not None and item.find('country').text == 'USD' and today in item.find('date').text:
                events.append(f"{item.find('title').text} at {item.find('time').text}")
        return f"WARNING: {', '.join(events)}" if events else "Clear. No high-impact USD news today."
    except: return "Macro Data Unavailable"

# --- 1-HOUR CONFLUENCE STRATEGY ENGINES (THE MUSCLE) ---
def execute_trend_reclaim(symbol, k60, risk_pct, bias):
    c, h, l, v = k60['close'], k60['high'], k60['low'], k60['vol']
    if len(c) < 30: return None
    closed_c, closed_h, closed_l = c[-2], h[-2], l[-2]
    
    # 🚨 CONFLUENCE FILTERS
    avg_vol = sum(v[-22:-2]) / 20 if sum(v[-22:-2]) > 0 else 1
    current_rvol = v[-2] / avg_vol
    _, _, macd_hist = calculate_macd_native(c[:-1]) 
    
    signal = None
    for i in range(len(c)-10, len(c)-2):
        if l[i] > h[i-2]: 
            # 🚨 UPGRADE A: Check if FVG was mitigated by any intermediate candle
            mitigated = False
            for j in range(i + 1, len(c) - 2):
                if c[j] < h[i-2]:
                    mitigated = True
                    break
            if mitigated: continue
            
            mid = (l[i] + h[i-2]) / 2
            # Long requires MACD Momentum > 0 AND Vol > Average
            if closed_l <= mid < closed_c and bias in ["LONG_ONLY", "NEUTRAL"] and macd_hist > 0 and current_rvol > 1.0:
                signal = {"side": "LONG", "entry": closed_c, "sl": h[i-2] * 0.999}
                break
        elif h[i] < l[i-2]: 
            # 🚨 UPGRADE A: Check if FVG was mitigated by any intermediate candle
            mitigated = False
            for j in range(i + 1, len(c) - 2):
                if c[j] > l[i-2]:
                    mitigated = True
                    break
            if mitigated: continue
            
            mid = (h[i] + l[i-2]) / 2
            # Short requires MACD Momentum < 0 AND Vol > Average
            if closed_h >= mid > closed_c and bias in ["SHORT_ONLY", "NEUTRAL"] and macd_hist < 0 and current_rvol > 1.0:
                signal = {"side": "SHORT", "entry": closed_c, "sl": l[i-2] * 1.001}
                break
                
    if signal:
        dist = abs(signal['entry'] - signal['sl']) / signal['entry']
        if dist < 0.015:
            dist = 0.015
            signal['sl'] = signal['entry'] * (1 - dist) if signal['side'] == "LONG" else signal['entry'] * (1 + dist)

        signal['tp'] = signal['entry'] * (1 + dist * 2) if signal['side'] == "LONG" else signal['entry'] * (1 - dist * 2)
        state = get_account_state()
        signal['risk_amt'] = state["current_equity"] * risk_pct
        return signal
    return None

def execute_liquidity_sweep(symbol, k60, risk_pct, bias):
    c, h, l, v = k60['close'], k60['high'], k60['low'], k60['vol']
    if len(c) < 35: return None
    swing_low, swing_high = min(l[-32:-3]), max(h[-32:-3])
    closed_c, closed_h, closed_l, closed_v = c[-2], h[-2], l[-2], v[-2]
    
    # 🚨 CONFLUENCE FILTERS
    current_rsi = calculate_rsi_native(c[:-1]) 
    avg_vol = sum(v[-22:-2]) / 20 if sum(v[-22:-2]) > 0 else 1
    current_rvol = closed_v / avg_vol
    
    signal = None
    # Long requires cooled off RSI (< 45) AND Volume spike
    if bias in ["LONG_ONLY", "NEUTRAL"] and closed_l < swing_low and closed_c > swing_low and current_rsi < 45 and current_rvol > 1.1:
        signal = {"side": "LONG", "entry": closed_c, "sl": closed_l * 0.998}
    # Short requires overbought RSI (> 55) AND Volume spike
    elif bias in ["SHORT_ONLY", "NEUTRAL"] and closed_h > swing_high and closed_c < swing_high and current_rsi > 55 and current_rvol > 1.1:
        signal = {"side": "SHORT", "entry": closed_c, "sl": closed_h * 1.002}
        
    if signal:
        dist = abs(signal['entry'] - signal['sl']) / signal['entry']
        if dist < 0.015:
            dist = 0.015
            signal['sl'] = signal['entry'] * (1 - dist) if signal['side'] == "LONG" else signal['entry'] * (1 + dist)

        signal['tp'] = signal['entry'] * (1 + dist * 2) if signal['side'] == "LONG" else signal['entry'] * (1 - dist * 2)
        state = get_account_state()
        signal['risk_amt'] = state["current_equity"] * risk_pct
        return signal
    return None

def generate_state_payload(equity, drawdown):
    k1d = get_mexc_klines("BTC_USDT", "Day1", 100)
    k1 = get_mexc_klines("BTC_USDT", "Min60", 100)
    if k1d and k1:
        ema50 = calculate_ema(k1d['close'], 50)
        trend = "UP" if k1d['close'][-1] > ema50[-1] else "DOWN"
        rvol, atr_pct, vol_state = calculate_market_structure_native(k1d['high'], k1d['low'], k1d['close'], k1d['vol'])
        adx = calculate_adx_native(k1['high'], k1['low'], k1['close'])
        
        # 🚨 Fetching global 1H RSI and MACD for AI interpretation
        rsi = calculate_rsi_native(k1['close'])
        _, _, macd_hist = calculate_macd_native(k1['close'])
    else: trend, rvol, atr_pct, vol_state, adx, rsi, macd_hist = "Unknown", 0, 0, "Unknown", 0, 50.0, 0.0
    
    fng_v, fng_c = get_sentiment()
    fund, ls = get_funding_rate(), get_ls_ratio()
    macro = get_macro_status()
    
    return f"""CURRENT STATE PAYLOAD:
Account Equity: ${equity:,.2f} (Drawdown: {drawdown:.1f}%)
Macro Calendar: {macro}

Technicals (BTC Proxy for Macro Regime):
1D Trend: {trend}, 1H ADX: {adx}, 1D RVOL: {rvol}, 1D ATR%: {atr_pct}% ({vol_state})
Momentum Vectors: 1H RSI: {rsi}, 1H MACD Histogram: {macd_hist}

Sentiment: Fear & Greed: {fng_v} ({fng_c}), Funding: {fund:.4f}%, Whale L/S: {ls}"""

def main():
    print("🤖 APEX-Q: V1 1-HOUR ENGINE (CONFLUENCE UPGRADED) ONLINE")
    print("-------------------------------------------------")
    
    last_p_time = 0 
    alerted_signals = {}
    cached_top_20 = []
    last_top_20_fetch = 0
    
    while True:
        current_time = time.time()
        
        update_open_trades_pnl()
        state = get_account_state()
        equity = state["current_equity"]
        peak = state["high_water_mark"]
        drawdown = ((peak - equity) / peak) * 100 if peak > equity else 0.0
        
        if current_time - last_p_time > 14400 or last_p_time == 0:
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 🧠 Generating V1 State Payload (Equity: ${equity:,.2f} | DD: {drawdown:.2f}%)...")
            payload = generate_state_payload(equity, drawdown)
            ai_directive = ask_gemini_apex_q(payload)
            if ai_directive:
                with open(CONFIG_PATH, 'w') as f: json.dump(ai_directive, f, indent=4)
                print(f"✅ V1 Strategy Updated: {ai_directive.get('active_strategy')} ({ai_directive.get('directional_bias')})")
                last_p_time = current_time
            else:
                time.sleep(60); continue
            
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, 'r') as f: config = json.load(f)
            except:
                time.sleep(5); continue
                
            strat = config.get("active_strategy")
            assets = config.get("approved_assets", [])
            risk = config.get("risk_per_trade_pct", 0.0) / 100
            bias = config.get("directional_bias", "NEUTRAL")
            
            if "TOP_20" in assets or "TOP20" in assets:
                if current_time - last_top_20_fetch > 3600 or not cached_top_20:
                    cached_top_20 = get_top_20_mexc_assets()
                    last_top_20_fetch = current_time
                assets = cached_top_20
            
            if strat != "HOLD_CASH":
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Radar locked. Scanning 1H charts for {strat}...", end="\r")
                for asset in assets:
                    k60 = get_mexc_klines(f"{asset}_USDT", "Min60", limit=100)
                    if not k60: time.sleep(0.2); continue
                    
                    sig = execute_trend_reclaim(asset, k60, risk, bias) if strat == "STRAT_TREND_RECLAIM" else execute_liquidity_sweep(asset, k60, risk, bias)                
                    
                    if sig:
                        sig_id = f"{asset}_{strat}_{sig['side']}_1H"
                        state = get_account_state()
                        active_ids = [t['id'] for t in state.get('active_trades', [])]
                        if sig_id not in alerted_signals and sig_id not in active_ids:
                            def fmt_p(p): return f"{p:,.4f}" if p >= 0.1 else f"{p:.10f}".rstrip('0').rstrip('.')
                            sig_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S BST')
                            
                            sl_pct = (abs(sig['sl'] - sig['entry']) / sig['entry']) * 100
                            tp_pct = (abs(sig['tp'] - sig['entry']) / sig['entry']) * 100

                            alert_text = (
                                f"🚨 APEX-Q V1 (1H INTRADAY) 🚨\n"
                                f"Time:        {sig_time}\n"
                                f"Asset:       {asset}USDT\n"
                                f"Action:      {sig['side']} at Market ({fmt_p(sig['entry'])})\n"
                                f"Stop Loss:   {fmt_p(sig['sl'])} ({sl_pct:.1f}%)\n"
                                f"Take Profit: {fmt_p(sig['tp'])} ({tp_pct:.1f}%)\n"
                                f"Risk Amount: ${sig['risk_amt']:,.2f}\n"
                                f"Validation:  RSI/MACD/VOL Checked ✅"
                            )
                            print("\n\a\a\a" + "🔥"*20); print(alert_text); print("🔥"*20 + "\n")
                            if twilio_alert: twilio_alert.send_whatsapp(alert_text)
                                
                            state["active_trades"].append({
                                "id": sig_id, "asset": asset, "side": sig['side'], "entry": sig['entry'], 
                                "sl": sig['sl'], "tp": sig['tp'], "risk_amt": sig['risk_amt'], "timestamp": current_time
                            })
                            save_account_state(state)
                            alerted_signals[sig_id] = current_time                    
                    time.sleep(0.2)
            else:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Status: HOLDING CASH (Macro News). Radar Paused.", end="\r")
                
        alerted_signals = {k: v for k, v in alerted_signals.items() if current_time - v < 14400}
        time.sleep(30)

if __name__ == "__main__":
    main()