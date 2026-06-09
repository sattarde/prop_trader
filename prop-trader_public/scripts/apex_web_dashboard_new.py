import os
import json
import time
import threading
import urllib.request
import urllib.error
import ssl
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime
import xml.etree.ElementTree as ET

BASE_DIR = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
BASE_URL = "https://contract.mexc.com"

def get_env_variable(key, env_path="/Users/sattarde/.gemini/skills/prop-trader/.env"):
    if key in os.environ: return os.environ[key]
    try:
        with open(env_path, "r") as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    if k.strip() == key: return v.strip().strip(' "\'')
    except: pass
    return None

# --- BYPASS MAC SSL CERTIFICATE BLOCKS ---
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

# --- IN-MEMORY RAM CACHE ---
GLOBAL_MARKET_DATA = {
    "news": [], "news_status": "Loading...",
    "calendar": [], "calendar_status": "Loading...",
    "movers": {"gainers": [], "losers": []},
    "derivatives": {"funding": "Loading...", "ls_ratio": "Loading...", "oi": "Loading..."}, 
    "pulse": {
        "fng": "Loading...", 
        "btc_d": "Loading...", 
        "mcap": "Loading...", 
        "vol": "Loading...",
        "total2": "Loading...",
        "total3": "Loading...",
        "changes": {}
    },
    "liquidations": {"status": "Loading...", "data": None},
    "mexc_movers": {"status": "Starting...", "movers": []},
    "vwap_radar": [], "vwap_status": "Scanning...",
    "structure_radar": [], "structure_status": "Starting...",
    "last_updated": "--:--:--"
}

def calculate_vwap_rolling(highs, lows, closes, vols, length=96):
    if len(closes) < length: length = len(closes)
    h, l, c, v = highs[-length:], lows[-length:], closes[-length:], vols[-length:]
    sum_tp_v = sum(((h[i] + l[i] + c[i]) / 3.0) * v[i] for i in range(len(c)))
    sum_v = sum(v)
    return sum_tp_v / sum_v if sum_v > 0 else c[-1]

def calculate_anchored_vwap(highs, lows, closes, vols, anchor_index):
    h, l, c, v = highs[anchor_index:], lows[anchor_index:], closes[anchor_index:], vols[anchor_index:]
    sum_tp_v = sum(((h[i] + l[i] + c[i]) / 3.0) * v[i] for i in range(len(c)))
    sum_v = sum(v)
    return sum_tp_v / sum_v if sum_v > 0 else c[-1]

def calculate_ema(prices, window):
    if not prices or len(prices) < window: return [prices[-1]] * len(prices)
    ema = [sum(prices[:window]) / window]
    multiplier = 2 / (window + 1)
    for price in prices[window:]:
        ema.append((price - ema[-1]) * multiplier + ema[-1])
    return ema


def convert_est_to_london(date_str, time_str):
    if not date_str:
        return time_str
    try:
        date_str = date_str.strip()
        time_str = time_str.strip()
        dt_date = datetime.strptime(date_str, "%m-%d-%Y")
        
        if ":" in time_str:
            t_str = time_str.replace(" ", "").lower()
            dt_time = datetime.strptime(t_str, "%I:%M%p")
            dt_combined = datetime(
                dt_date.year, dt_date.month, dt_date.day,
                dt_time.hour, dt_time.minute
            )
            from datetime import timedelta
            dt_london = dt_combined + timedelta(hours=5)
            return dt_london.strftime("%b-%d %H:%M")
        else:
            return f"{dt_date.strftime('%b-%d')} {time_str.upper()}"
    except Exception:
        return f"{date_str} {time_str}"

def fmt_sig_price(p):
    if p is None: return "0.00"
    if p >= 1000: return f"${p:,.2f}"
    if p >= 1: return f"${p:,.4f}"
    return f"${p:.8f}".rstrip('0').rstrip('.')

BASIC_HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}

HISTORY_FILE = os.path.join(BASE_DIR, "prop_pulse_history.json")
GLOBAL_PULSE_HISTORY = []

def load_pulse_history():
    global GLOBAL_PULSE_HISTORY
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                GLOBAL_PULSE_HISTORY = json.load(f)
            now_ts = time.time()
            GLOBAL_PULSE_HISTORY = [x for x in GLOBAL_PULSE_HISTORY if now_ts - x["time"] <= 90000]
        except: pass

def save_pulse_history():
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(GLOBAL_PULSE_HISTORY, f)
    except: pass

def find_closest_pulse(target_ts, max_diff=3600):
    best_entry = None
    best_diff = max_diff
    for entry in GLOBAL_PULSE_HISTORY:
        diff = abs(entry["time"] - target_ts)
        if diff < best_diff:
            best_diff = diff
            best_entry = entry
            
    # Fallback: if target_ts is older than the start of history, use the oldest available entry
    if not best_entry and GLOBAL_PULSE_HISTORY:
        oldest = GLOBAL_PULSE_HISTORY[0]
        if oldest["time"] > target_ts:
            best_entry = oldest
            
    return best_entry

load_pulse_history()

def write_json(filename, data):
    path = os.path.join(BASE_DIR, filename)
    try:
        with open(path, 'w') as f:
            json.dump(data, f, indent=4)
        return True
    except:
        return False

def update_derivatives_data():
    try:
        req = urllib.request.Request("https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol=BTCUSDT&period=1h&limit=1", headers=BASIC_HEADERS)
        with urllib.request.urlopen(req, timeout=10, context=CTX) as resp:
            GLOBAL_MARKET_DATA["derivatives"]["ls_ratio"] = json.loads(resp.read().decode('utf-8'))[0]['longShortRatio']
    except: pass
        
    try:
        req = urllib.request.Request("https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT", headers=BASIC_HEADERS)
        with urllib.request.urlopen(req, timeout=10, context=CTX) as resp:
            fund = float(json.loads(resp.read().decode('utf-8'))['lastFundingRate']) * 100
            GLOBAL_MARKET_DATA["derivatives"]["funding"] = f"{fund:.4f}%"
    except: pass

    try:
        req = urllib.request.Request("https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDT", headers=BASIC_HEADERS)
        with urllib.request.urlopen(req, timeout=10, context=CTX) as resp:
            oi = float(json.loads(resp.read().decode('utf-8'))['openInterest'])
            GLOBAL_MARKET_DATA["derivatives"]["oi"] = f"{oi:,.0f} BTC"
    except: pass

def update_liquidations_data():
    btc_price = 67000.0
    try:
        req = urllib.request.Request("https://fapi.binance.com/fapi/v1/ticker/price?symbol=BTCUSDT", headers=BASIC_HEADERS)
        with urllib.request.urlopen(req, timeout=5, context=CTX) as resp:
            btc_price = float(json.loads(resp.read().decode('utf-8'))['price'])
    except: pass

    coinalyze_key = get_env_variable("COINALYZE_API_KEY")
    if coinalyze_key and len(coinalyze_key) > 5:
        liq_data = {}
        error_msg = None
        now_ts = int(time.time())
        start_ts = now_ts - (3 * 86400)
        for fetch_int, dict_key in [("1hour", "1h"), ("4hour", "4h"), ("daily", "24h")]:
            try:
                url = f"https://api.coinalyze.net/v1/liquidation-history?symbols=BTCUSDT_PERP.A&interval={fetch_int}&from={start_ts}&to={now_ts}"
                ca_headers = BASIC_HEADERS.copy()
                ca_headers['api_key'] = coinalyze_key.strip()
                req = urllib.request.Request(url, headers=ca_headers)
                with urllib.request.urlopen(req, timeout=10, context=CTX) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                    if isinstance(data, list) and len(data) > 0 and 'history' in data[0] and isinstance(data[0]['history'], list) and len(data[0]['history']) > 0:
                        last = data[0]['history'][-1]
                        liq_data[dict_key] = {"l": last.get('l', 0), "s": last.get('s', 0)}
                    else:
                        liq_data[dict_key] = {"l": 0, "s": 0}
            except urllib.error.HTTPError as e:
                err_body = e.read().decode('utf-8')
                error_msg = f"HTTP {e.code}: {err_body[:30]}"
                break
            except Exception as e:
                error_msg = str(e)[:30]
                break
        
        if error_msg:
            GLOBAL_MARKET_DATA["liquidations"]["status"] = f"Coinalyze Error: {error_msg}"
        elif len(liq_data) > 0:
            GLOBAL_MARKET_DATA["liquidations"]["status"] = "Loaded"
            GLOBAL_MARKET_DATA["liquidations"]["data"] = {
                "1h_l_raw": float(liq_data.get('1h', {}).get('l', 0)) * btc_price,
                "1h_s_raw": float(liq_data.get('1h', {}).get('s', 0)) * btc_price,
                "4h_l_raw": float(liq_data.get('4h', {}).get('l', 0)) * btc_price,
                "4h_s_raw": float(liq_data.get('4h', {}).get('s', 0)) * btc_price,
                "24h_l_raw": float(liq_data.get('24h', {}).get('l', 0)) * btc_price,
                "24h_s_raw": float(liq_data.get('24h', {}).get('s', 0)) * btc_price
            }
    else:
        GLOBAL_MARKET_DATA["liquidations"]["status"] = "Missing COINALYZE_API_KEY in .env"

MEXC_PRICE_HISTORY = {}
MEXC_SAMPLES_COLLECTED = 0
MEXC_LOCK = threading.Lock()

def update_mexc_movers_data():
    global MEXC_SAMPLES_COLLECTED
    try:
        req = urllib.request.Request("https://contract.mexc.com/api/v1/contract/ticker", headers=BASIC_HEADERS)
        with urllib.request.urlopen(req, timeout=10, context=CTX) as resp:
            res_data = json.loads(resp.read().decode("utf-8"))
            if res_data.get("success") and isinstance(res_data.get("data"), list):
                tickers = res_data["data"]
                now = time.time()
                with MEXC_LOCK:
                    MEXC_SAMPLES_COLLECTED += 1
                    for t in tickers:
                        sym = t.get("symbol")
                        if not sym or not sym.endswith("_USDT"):
                            continue
                        price = float(t.get("lastPrice", 0))
                        if price <= 0:
                            continue
                        if sym not in MEXC_PRICE_HISTORY:
                            MEXC_PRICE_HISTORY[sym] = []
                        MEXC_PRICE_HISTORY[sym].append((now, price))
                        while MEXC_PRICE_HISTORY[sym] and now - MEXC_PRICE_HISTORY[sym][0][0] > 4000:
                            MEXC_PRICE_HISTORY[sym].pop(0)
                    
                    movers = []
                    for sym, hist in MEXC_PRICE_HISTORY.items():
                        if len(hist) < 2:
                            continue
                        current_price = hist[-1][1]
                        target_15m = now - 900
                        target_1h = now - 3600
                        price_15m = None
                        price_1h = None
                        best_diff_15m = 60
                        best_diff_1h = 120
                        for ts, p in hist:
                            diff_15m = abs(ts - target_15m)
                            if diff_15m < best_diff_15m:
                                best_diff_15m = diff_15m
                                price_15m = p
                            diff_1h = abs(ts - target_1h)
                            if diff_1h < best_diff_1h:
                                best_diff_1h = diff_1h
                                price_1h = p
                        
                        chg_15m = 0.0
                        chg_1h = 0.0
                        triggered = False
                        if price_15m:
                            chg_15m = (current_price - price_15m) / price_15m
                            if abs(chg_15m) >= 0.05:
                                triggered = True
                        if price_1h:
                            chg_1h = (current_price - price_1h) / price_1h
                            if abs(chg_1h) >= 0.05:
                                triggered = True
                        
                        if triggered:
                            movers.append({
                                "symbol": sym.replace("_USDT", ""),
                                "price": current_price,
                                "price_str": fmt_sig_price(current_price),
                                "chg_15m": chg_15m * 100,
                                "chg_1h": chg_1h * 100,
                                "has_15m": bool(price_15m),
                                "has_1h": bool(price_1h)
                            })
                    
                    movers = sorted(movers, key=lambda x: max(abs(x["chg_15m"]), abs(x["chg_1h"])), reverse=True)
                    status_msg = "Scanning"
                    if MEXC_SAMPLES_COLLECTED < 60:
                        status_msg = f"Building 15m history ({MEXC_SAMPLES_COLLECTED}/60)"
                    elif MEXC_SAMPLES_COLLECTED < 240:
                        status_msg = f"Building 1h history ({MEXC_SAMPLES_COLLECTED}/240)"
                    else:
                        status_msg = "Live"
                    GLOBAL_MARKET_DATA["mexc_movers"] = {
                        "status": status_msg,
                        "movers": movers[:15]
                    }
    except Exception as e:
        GLOBAL_MARKET_DATA["mexc_movers"]["status"] = f"Error: {str(e)[:30]}"

def fetch_macro_data():
    """Background Worker Thread: Updates the RAM cache every 60 seconds without ever crashing."""
    while True:
        try: 
            # 1. Market Pulse (CoinGecko Global)
            try:
                req = urllib.request.Request("https://api.coingecko.com/api/v3/global", headers=BASIC_HEADERS)
                with urllib.request.urlopen(req, timeout=10, context=CTX) as resp:
                    cg = json.loads(resp.read().decode('utf-8'))['data']
                    
                    mcap_val = float(cg['total_market_cap']['usd'])
                    btc_d_val = float(cg['market_cap_percentage']['btc'])
                    eth_d_val = float(cg['market_cap_percentage']['eth'])
                    vol_val = float(cg['total_volume']['usd'])
                    
                    btc_mcap = mcap_val * (btc_d_val / 100.0)
                    eth_mcap = mcap_val * (eth_d_val / 100.0)
                    total2_val = mcap_val - btc_mcap
                    total3_val = mcap_val - btc_mcap - eth_mcap
                    
                    now_ts = time.time()
                    GLOBAL_PULSE_HISTORY.append({
                        "time": now_ts,
                        "mcap": mcap_val,
                        "btc_d": btc_d_val,
                        "total2": total2_val,
                        "total3": total3_val
                    })
                    # Prune history older than 25 hours
                    while GLOBAL_PULSE_HISTORY and now_ts - GLOBAL_PULSE_HISTORY[0]["time"] > 90000:
                        GLOBAL_PULSE_HISTORY.pop(0)
                    
                    save_pulse_history()
                    
                    # Calculate changes
                    pulse_4h = find_closest_pulse(now_ts - 14400)
                    pulse_24h = find_closest_pulse(now_ts - 86400)
                    
                    changes = {
                        "mcap_4h": (mcap_val - pulse_4h["mcap"]) / pulse_4h["mcap"] * 100 if pulse_4h else None,
                        "mcap_24h": (mcap_val - pulse_24h["mcap"]) / pulse_24h["mcap"] * 100 if pulse_24h else None,
                        "total2_4h": (total2_val - pulse_4h["total2"]) / pulse_4h["total2"] * 100 if pulse_4h else None,
                        "total2_24h": (total2_val - pulse_24h["total2"]) / pulse_24h["total2"] * 100 if pulse_24h else None,
                        "total3_4h": (total3_val - pulse_4h["total3"]) / pulse_4h["total3"] * 100 if pulse_4h else None,
                        "total3_24h": (total3_val - pulse_24h["total3"]) / pulse_24h["total3"] * 100 if pulse_24h else None,
                        "btc_d_4h": (btc_d_val - pulse_4h["btc_d"]) / pulse_4h["btc_d"] * 100 if pulse_4h else None,
                        "btc_d_24h": (btc_d_val - pulse_24h["btc_d"]) / pulse_24h["btc_d"] * 100 if pulse_24h else None
                    }
                    
                    GLOBAL_MARKET_DATA["pulse"]["mcap"] = f"${mcap_val/1e12:.2f}T"
                    GLOBAL_MARKET_DATA["pulse"]["total2"] = f"${total2_val/1e12:.2f}T"
                    GLOBAL_MARKET_DATA["pulse"]["total3"] = f"${total3_val/1e12:.2f}T"
                    GLOBAL_MARKET_DATA["pulse"]["btc_d"] = f"{btc_d_val:.1f}%"
                    GLOBAL_MARKET_DATA["pulse"]["vol"] = f"${vol_val/1e9:.2f}B"
                    GLOBAL_MARKET_DATA["pulse"]["changes"] = changes
            except: pass

            try:
                req = urllib.request.Request("https://api.alternative.me/fng/", headers=BASIC_HEADERS)
                with urllib.request.urlopen(req, timeout=10, context=CTX) as resp:
                    fng = json.loads(resp.read().decode('utf-8'))['data'][0]
                    GLOBAL_MARKET_DATA["pulse"]["fng"] = f"{fng['value']} ({fng['value_classification']})"
            except: pass

            # 2. Major News (Primary: CoinDesk RSS, Fallback: CryptoCompare)
            news_loaded = False
            try:
                req = urllib.request.Request("https://www.coindesk.com/arc/outboundfeeds/rss", headers=BASIC_HEADERS)
                with urllib.request.urlopen(req, timeout=10, context=CTX) as resp:
                    root = ET.fromstring(resp.read())
                    n_data = []
                    for item in root.findall('.//item')[:6]:
                        title = item.find('title')
                        link = item.find('link')
                        pub_date = item.find('pubDate')
                        if title is not None and link is not None:
                            ts = 0
                            if pub_date is not None:
                                try:
                                    import email.utils
                                    ts = int(email.utils.parsedate_to_datetime(pub_date.text).timestamp())
                                except: pass
                            n_data.append({"title": title.text, "source": "CoinDesk RSS", "url": link.text, "time": ts})
                    if n_data:
                        GLOBAL_MARKET_DATA["news"] = n_data
                        GLOBAL_MARKET_DATA["news_status"] = "Loaded via CoinDesk"
                        news_loaded = True
            except: pass

            if not news_loaded:
                try:
                    req = urllib.request.Request("https://min-api.cryptocompare.com/data/v2/news/?lang=EN", headers=BASIC_HEADERS)
                    with urllib.request.urlopen(req, timeout=10, context=CTX) as resp:
                        news_data = json.loads(resp.read().decode('utf-8'))
                        if isinstance(news_data, dict) and isinstance(news_data.get('Data'), list):
                            GLOBAL_MARKET_DATA["news"] = [{"title": n['title'], "source": n.get('source_info', {}).get('name', 'CryptoCompare'), "url": n['url'], "time": int(n.get('published_on', 0))} for n in news_data['Data'][:6]]
                            GLOBAL_MARKET_DATA["news_status"] = "Loaded via CryptoCompare"
                        else:
                            GLOBAL_MARKET_DATA["news_status"] = "News APIs Rate Limited"
                except Exception as e: pass

            # 3. Top Movers (Binance Futures)
            try:
                req = urllib.request.Request("https://fapi.binance.com/fapi/v1/ticker/24hr", headers=BASIC_HEADERS)
                with urllib.request.urlopen(req, timeout=10, context=CTX) as resp:
                    tickers = json.loads(resp.read().decode('utf-8'))
                    valid = [t for t in tickers if t.get('symbol', '').endswith('USDT') and float(t.get('quoteVolume', 0)) > 50000000]
                    sorted_tickers = sorted(valid, key=lambda x: float(x.get('priceChangePercent', 0)), reverse=True)
                    GLOBAL_MARKET_DATA["movers"]["gainers"] = [{"sym": t['symbol'].replace('USDT',''), "pct": float(t['priceChangePercent'])} for t in sorted_tickers[:4]]
                    GLOBAL_MARKET_DATA["movers"]["losers"] = [{"sym": t['symbol'].replace('USDT',''), "pct": float(t['priceChangePercent'])} for t in sorted_tickers[-4:]]
            except: pass
            
            # 4. Economic Calendar (Primary: ForexFactory)
            events_loaded = False
            events = []
            try:
                req = urllib.request.Request("https://nfs.faireconomy.media/ff_calendar_thisweek.xml", headers=BASIC_HEADERS)
                with urllib.request.urlopen(req, timeout=10, context=CTX) as resp:
                    root = ET.fromstring(resp.read())
                    for item in root.findall('event'):
                        country = item.find('country')
                        impact = item.find('impact')
                        if country is not None and impact is not None:
                            if country.text in ['USD', 'EUR', 'GBP', 'JPY', 'AUD', 'CAD', 'CHF'] and impact.text == 'High':
                                date_el = item.find('date')
                                time_el = item.find('time')
                                date_str = date_el.text if date_el is not None else ""
                                time_str = time_el.text if time_el is not None else ""
                                london_time = convert_est_to_london(date_str, time_str)
                                events.append({
                                    "title": item.find('title').text, 
                                    "time": london_time,
                                    "impact": impact.text, 
                                    "currency": country.text
                                })
                    if events:
                        GLOBAL_MARKET_DATA["calendar"] = events[:8]
                        GLOBAL_MARKET_DATA["calendar_status"] = "Loaded via ForexFactory"
                        events_loaded = True
                    else:
                        GLOBAL_MARKET_DATA["calendar_status"] = "ForexFactory: No major high-impact events"
            except Exception as e:
                GLOBAL_MARKET_DATA["calendar_status"] = f"ForexFactory Offline: {str(e)[:35]}"

            # 5. Liquidations
            try:
                update_liquidations_data()
            except: pass

            # 7. Derivatives
            try:
                update_derivatives_data()
            except: pass

            GLOBAL_MARKET_DATA['last_updated'] = datetime.now().strftime('%H:%M:%S')
            time.sleep(60)

        except Exception as e:
            time.sleep(10)
        
def fetch_vwap_radar_data():
    GLOBAL_MARKET_DATA["vwap_radar"] = []
    GLOBAL_MARKET_DATA["vwap_status"] = "Scanning..."
    
    while True:
        try:
            top_100 = []
            req = urllib.request.Request("https://contract.mexc.com/api/v1/contract/ticker", headers=BASIC_HEADERS)
            with urllib.request.urlopen(req, timeout=10, context=CTX) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("success"):
                    tickers = [t for t in data["data"] if t['symbol'].endswith('_USDT')]
                    sorted_pairs = sorted(tickers, key=lambda x: float(x.get('amount24', 0)), reverse=True)
                    top_100 = [t['symbol'].replace('_USDT', '') for t in sorted_pairs if t['symbol'].replace('_USDT', '') not in ["USDC", "BUSD", "TUSD", "FDUSD", "USDE"]][:100]
            
            if not top_100:
                top_100 = ["BTC", "ETH", "SOL", "XRP", "ADA", "AVAX", "LINK", "DOT", "NEAR", "DOGE"]
                
            setups = []
            scan_time = datetime.now().strftime('%b-%d %H:%M')
            
            for asset in top_100:
                try:
                    # --- 1. 5-MINUTE TIMEFRAME SCAN ---
                    url_5m = f"https://contract.mexc.com/api/v1/contract/kline/{asset}_USDT?interval=Min5&limit=300"
                    req_5m = urllib.request.Request(url_5m, headers=BASIC_HEADERS)
                    with urllib.request.urlopen(req_5m, timeout=5, context=CTX) as resp:
                        res_5m = json.loads(resp.read().decode("utf-8"))
                        if res_5m.get("success") and res_5m.get("data"):
                            d = res_5m["data"]
                            closes = [float(x) for x in d["close"]]
                            highs = [float(x) for x in d["high"]]
                            lows = [float(x) for x in d["low"]]
                            vols = [float(x) for x in d["vol"]]
                            
                            if len(closes) >= 288:
                                last_close = closes[-1]
                                vwap_5m = calculate_vwap_rolling(highs, lows, closes, vols, 288)
                                dist = (last_close - vwap_5m) / vwap_5m
                                if 0 <= dist <= 0.003: # Within 0.3% above VWAP
                                    entry = vwap_5m
                                    sl = entry * 0.99
                                    tp = entry * 1.02
                                    setups.append({
                                        "time": scan_time,
                                        "asset": asset,
                                        "type": "5M Limit Long",
                                        "side": "LONG",
                                        "entry": fmt_sig_price(entry),
                                        "tp": fmt_sig_price(tp),
                                        "sl": fmt_sig_price(sl),
                                        "reason": "Price testing 5M rolling VWAP floor. High probability institutional buy zone."
                                    })
                                elif -0.003 <= dist < 0: # Within 0.3% below VWAP
                                    entry = vwap_5m
                                    sl = entry * 1.01
                                    tp = entry * 0.98
                                    setups.append({
                                        "time": scan_time,
                                        "asset": asset,
                                        "type": "5M Limit Short",
                                        "side": "SHORT",
                                        "entry": fmt_sig_price(entry),
                                        "tp": fmt_sig_price(tp),
                                        "sl": fmt_sig_price(sl),
                                        "reason": "Price rejecting 5M rolling VWAP ceiling. Strong distribution limit sell zone."
                                    })

                    # --- 2. 15-MINUTE TIMEFRAME SCAN ---
                    url_15m = f"https://contract.mexc.com/api/v1/contract/kline/{asset}_USDT?interval=Min15&limit=120"
                    req_15m = urllib.request.Request(url_15m, headers=BASIC_HEADERS)
                    with urllib.request.urlopen(req_15m, timeout=5, context=CTX) as resp:
                        res_15m = json.loads(resp.read().decode("utf-8"))
                        if res_15m.get("success") and res_15m.get("data"):
                            d = res_15m["data"]
                            closes = [float(x) for x in d["close"]]
                            highs = [float(x) for x in d["high"]]
                            lows = [float(x) for x in d["low"]]
                            vols = [float(x) for x in d["vol"]]
                            
                            if len(closes) >= 96:
                                last_close = closes[-1]
                                vwap_15m = calculate_vwap_rolling(highs, lows, closes, vols, 96)
                                dist = (last_close - vwap_15m) / vwap_15m
                                if 0 <= dist <= 0.004: # Within 0.4% above VWAP
                                    entry = vwap_15m
                                    sl = entry * 0.99
                                    tp = entry * 1.02
                                    setups.append({
                                        "time": scan_time,
                                        "asset": asset,
                                        "type": "15M Limit Long",
                                        "side": "LONG",
                                        "entry": fmt_sig_price(entry),
                                        "tp": fmt_sig_price(tp),
                                        "sl": fmt_sig_price(sl),
                                        "reason": "15M dynamic institutional floor retest. High probability buy response."
                                    })
                                elif -0.004 <= dist < 0: # Within 0.4% below VWAP
                                    entry = vwap_15m
                                    sl = entry * 1.01
                                    tp = entry * 0.98
                                    setups.append({
                                        "time": scan_time,
                                        "asset": asset,
                                        "type": "15M Limit Short",
                                        "side": "SHORT",
                                        "entry": fmt_sig_price(entry),
                                        "tp": fmt_sig_price(tp),
                                        "sl": fmt_sig_price(sl),
                                        "reason": "15M dynamic institutional ceiling reject. Limit sell supply shelf."
                                    })

                    # --- 3. ANCHORED VWAP FOR SWING TRADING (1-HOUR TIMEFRAME, 7-DAY WINDOW) ---
                    url_60m = f"https://contract.mexc.com/api/v1/contract/kline/{asset}_USDT?interval=Min60&limit=200"
                    req_60m = urllib.request.Request(url_60m, headers=BASIC_HEADERS)
                    with urllib.request.urlopen(req_60m, timeout=5, context=CTX) as resp:
                        res_60m = json.loads(resp.read().decode("utf-8"))
                        if res_60m.get("success") and res_60m.get("data"):
                            d = res_60m["data"]
                            closes = [float(x) for x in d["close"]]
                            highs = [float(x) for x in d["high"]]
                            lows = [float(x) for x in d["low"]]
                            vols = [float(x) for x in d["vol"]]
                            
                            if len(closes) >= 168: # 7 days of 1-hour candles
                                last_close = closes[-1]
                                
                                # 7-Day Swing Low Anchor
                                last_7d_lows = lows[-168:]
                                min_low_7d = min(last_7d_lows)
                                anchor_l_idx = len(lows) - 168 + last_7d_lows.index(min_low_7d)
                                avwap_swing_l = calculate_anchored_vwap(highs, lows, closes, vols, anchor_l_idx)
                                
                                # 7-Day Swing High Anchor
                                last_7d_highs = highs[-168:]
                                max_high_7d = max(last_7d_highs)
                                anchor_s_idx = len(highs) - 168 + last_7d_highs.index(max_high_7d)
                                avwap_swing_s = calculate_anchored_vwap(highs, lows, closes, vols, anchor_s_idx)
                                
                                # Check Swing AVWAP Setups (within 0.5%)
                                dist_l = (last_close - avwap_swing_l) / avwap_swing_l
                                if 0 <= dist_l <= 0.005:
                                    entry = avwap_swing_l
                                    sl = entry * 0.975
                                    tp = entry * 1.05
                                    setups.append({
                                        "time": scan_time,
                                        "asset": asset,
                                        "type": "7D Swing Long",
                                        "side": "LONG",
                                        "entry": fmt_sig_price(entry),
                                        "tp": fmt_sig_price(tp),
                                        "sl": fmt_sig_price(sl),
                                        "reason": "7D Weekly Low AVWAP Support tested. Deep value swing accumulation floor."
                                    })
                                    
                                dist_s = (last_close - avwap_swing_s) / avwap_swing_s
                                if -0.005 <= dist_s < 0:
                                    entry = avwap_swing_s
                                    sl = entry * 1.025
                                    tp = entry * 0.95
                                    setups.append({
                                        "time": scan_time,
                                        "asset": asset,
                                        "type": "7D Swing Short",
                                        "side": "SHORT",
                                        "entry": fmt_sig_price(entry),
                                        "tp": fmt_sig_price(tp),
                                        "sl": fmt_sig_price(sl),
                                        "reason": "7D Weekly High AVWAP Resistance tested. Swing distribution ceiling limit entry."
                                    })
                except: pass
                time.sleep(0.03) # Pacing to prevent MEXC API rate limits
                
            GLOBAL_MARKET_DATA["vwap_radar"] = setups[:12] # Show top 12 active setups
            GLOBAL_MARKET_DATA["vwap_status"] = "Scan Complete"
            
        except Exception as e:
            GLOBAL_MARKET_DATA["vwap_status"] = "Scan Error"
            
        time.sleep(180) # Scan every 3 minutes

def fetch_mexc_movers_data():
    while True:
        try:
            update_mexc_movers_data()
        except: pass
        time.sleep(15)

# --- MARKET STRUCTURE RADAR FOR SWING TRADING ---
def detect_swing_structure_dashboard(highs, lows, closes, length=30):
    if len(closes) < length:
        return max(highs), min(lows), (max(highs) + min(lows)) / 2, 0.5
    swing_high = max(highs[-length:])
    swing_low = min(lows[-length:])
    equilibrium = (swing_high + swing_low) / 2
    current_price = closes[-1]
    range_size = swing_high - swing_low
    ratio = (current_price - swing_low) / range_size if range_size > 0 else 0.5
    return swing_high, swing_low, equilibrium, ratio

def fetch_structure_radar_data():
    GLOBAL_MARKET_DATA["structure_radar"] = []
    GLOBAL_MARKET_DATA["structure_status"] = "Initializing..."
    assets_fallback = ["BTC", "ETH", "SOL", "TAO", "NEAR", "XRP", "ADA", "AVAX", "LINK", "DOT", "SUI", "PEPE", "WLD", "XAU", "SILVER", "SPX500", "USOIL", "LTC", "BCH", "DOGE", "SHIB", "ICP", "FET", "FIL", "RNDR", "LDO", "ARB", "OP", "TIA", "APT", "IMX", "RUNE", "MKR", "GRT", "AAVE", "THETA", "EGLD", "FLOW", "FTM", "SAND", "MANA", "AXS", "GALA", "CHZ", "CRV", "DYDX", "ATOM", "STX", "FTT", "GMX"]
    
    while True:
        try:
            top_50 = []
            req = urllib.request.Request(f"{BASE_URL}/api/v1/contract/ticker", headers=BASIC_HEADERS)
            with urllib.request.urlopen(req, timeout=10, context=CTX) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("success"):
                    tickers = [t for t in data["data"] if t['symbol'].endswith('_USDT')]
                    sorted_pairs = sorted(tickers, key=lambda x: float(x.get('amount24', 0)), reverse=True)
                    top_50 = [t['symbol'].replace('_USDT', '') for t in sorted_pairs if t['symbol'].replace('_USDT', '') not in ["USDC", "BUSD", "TUSD", "FDUSD", "USDE"]][:50]
            
            if not top_50:
                top_50 = assets_fallback
                
            setups = []
            scan_time = datetime.now().strftime('%b-%d %H:%M')
            
            for asset in top_50:
                try:
                    url = f"https://contract.mexc.com/api/v1/contract/kline/{asset}_USDT?interval=Hour4&limit=100"
                    req = urllib.request.Request(url, headers=BASIC_HEADERS)
                    with urllib.request.urlopen(req, timeout=5, context=CTX) as resp:
                        res = json.loads(resp.read().decode("utf-8"))
                        if res.get("success") and res.get("data"):
                            d = res["data"]
                            closes = [float(x) for x in d["close"]]
                            highs = [float(x) for x in d["high"]]
                            lows = [float(x) for x in d["low"]]
                            
                            if len(closes) >= 30:
                                current_price = closes[-1]
                                swing_high, swing_low, eq, ratio = detect_swing_structure_dashboard(highs, lows, closes, 30)
                                
                                zone = "DISCOUNT" if ratio <= 0.50 else "PREMIUM"
                                if 0.45 <= ratio <= 0.55:
                                    zone = "EQUILIBRIUM"
                                    
                                fvg_status = "No Gap"
                                for i in range(len(closes) - 3, len(closes) - 6, -1):
                                    if highs[i-2] < lows[i]: # Bullish FVG
                                        mitigated = False
                                        for j in range(i+1, len(closes)):
                                            if closes[j] < highs[i-2]:
                                                mitigated = True
                                                break
                                        if not mitigated:
                                            fvg_status = "Bullish FVG (Unmitigated)"
                                            break
                                    elif lows[i-2] > highs[i]: # Bearish FVG
                                        mitigated = False
                                        for j in range(i+1, len(closes)):
                                            if closes[j] > lows[i-2]:
                                                mitigated = True
                                                break
                                        if not mitigated:
                                            fvg_status = "Bearish FVG (Unmitigated)"
                                            break
                                
                                ema50 = calculate_ema(closes, 50)
                                trend = "BULLISH" if current_price > ema50[-1] else "BEARISH"
                                
                                setups.append({
                                    "asset": asset,
                                    "trend": trend,
                                    "swing_high": swing_high,
                                    "swing_low": swing_low,
                                    "equilibrium": eq,
                                    "current_price": current_price,
                                    "ratio": ratio,
                                    "zone": zone,
                                    "fvg_status": fvg_status
                                })
                except: pass
                time.sleep(0.05)
                
            GLOBAL_MARKET_DATA["structure_radar"] = sorted(setups, key=lambda x: x["asset"])
            GLOBAL_MARKET_DATA["structure_status"] = f"Scan Complete ({scan_time})"
            
        except Exception as e:
            GLOBAL_MARKET_DATA["structure_status"] = f"Scan Error: {str(e)[:30]}"
            
        time.sleep(300)

# Ignite the background workers
threading.Thread(target=fetch_macro_data, daemon=True).start()
threading.Thread(target=fetch_vwap_radar_data, daemon=True).start()
threading.Thread(target=fetch_mexc_movers_data, daemon=True).start()
threading.Thread(target=fetch_structure_radar_data, daemon=True).start()

def read_json(filename):
    path = os.path.join(BASE_DIR, filename)
    if os.path.exists(path):
        try:
            with open(path, 'r') as f: return json.load(f)
        except: pass
    return {}

HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Saurabh's Institutional Terminal</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body { background-color: #05080f; color: #94a3b8; font-family: 'Plus Jakarta Sans', 'Google Sans', -apple-system, BlinkMacSystemFont, sans-serif; font-size: 0.85rem; }
        .panel { background-color: #0b1120; border: 1px solid #1e293b; border-radius: 6px; padding: 1rem; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.5); }
        .win { color: #4ade80; }
        .loss { color: #f87171; }
        .title-bar { font-size: 0.75rem; font-weight: bold; text-transform: uppercase; letter-spacing: 0.05em; border-bottom: 1px solid #1e293b; padding-bottom: 0.4rem; margin-bottom: 0.75rem; color: #e2e8f0; }
        th, td { padding: 6px 8px; border-bottom: 1px solid #1e293b; }
        th { color: #64748b; font-weight: normal; text-transform: uppercase; font-size: 0.7rem; text-align: left;}
        ::-webkit-scrollbar { width: 5px; }
        ::-webkit-scrollbar-track { background: #05080f; }
        ::-webkit-scrollbar-thumb { background: #1e293b; border-radius: 3px; }
        .splitter-h { width: 6px; cursor: col-resize; background-color: #05080f; transition: background-color 0.15s; flex-shrink: 0; align-self: stretch; z-index: 10; }
        .splitter-h:hover, .splitter-h.active { background-color: #0ea5e9; }
        .splitter-v { height: 6px; cursor: row-resize; background-color: #05080f; transition: background-color 0.15s; flex-shrink: 0; width: 100%; z-index: 10; }
        .splitter-v:hover, .splitter-v.active { background-color: #0ea5e9; }

        /* Font size scaling override (+2 points/px) */
        body { font-size: 0.97rem !important; }
        .title-bar { font-size: 0.875rem !important; }
        th { font-size: 0.825rem !important; }
        .text-\[9px\] { font-size: 11px !important; }
        .text-\[10px\] { font-size: 12px !important; }
        .text-\[11px\] { font-size: 13px !important; }
        .text-xs { font-size: 14px !important; }
        .text-sm { font-size: 16px !important; }
        .text-2xl { font-size: 26px !important; }
    </style>
</head>
<body class="p-3 h-screen flex flex-col overflow-hidden">
    
    <div class="grid grid-cols-3 items-center mb-3 px-2">
        <div></div>
        <div class="text-center flex flex-col items-center">
            <h1 class="text-2xl font-bold text-sky-500 tracking-widest flex items-center justify-center gap-3">
                <div class="w-2.5 h-2.5 rounded-full bg-green-400 animate-pulse shadow-[0_0_8px_#4ade80]"></div>
                SAURABH'S INSTITUTIONAL TERMINAL
            </h1>
            <div class="text-[10px] text-slate-500 tracking-wider mt-1">QUANTITATIVE MACRO & EXECUTION FRAMEWORK</div>
        </div>
        <div class="text-xs text-right font-bold">
            <div class="text-slate-400">ENGINE SYNC: <span id="sync-time" class="text-sky-400">--:--:--</span></div>
            <div class="text-slate-400 mt-1">MACRO CACHE: <span id="market-sync" class="text-sky-400">--:--:--</span></div>
        </div>
    </div>

    <div class="flex flex-grow overflow-hidden select-none" style="height: calc(100vh - 80px);">
        
        <div class="flex flex-col overflow-hidden" style="width: 25%; flex-shrink: 0;" id="col-left">
            <div class="panel flex flex-col">
                <div class="title-bar text-sky-400 flex justify-between"><span>🌐 Market Pulse</span><span class="text-[9px] text-slate-500">CoinGecko</span></div>
                <div class="space-y-2 text-[11px]">
                    <div class="flex justify-between items-center bg-gray-900/50 p-1.5 rounded">
                        <span class="text-slate-400">Global MCap:</span>
                        <div class="text-right">
                            <span id="m-mcap" class="text-white font-bold text-xs">--</span>
                            <div id="m-mcap-chg" class="text-[9px] font-bold"></div>
                        </div>
                    </div>
                    <div class="flex justify-between items-center bg-gray-900/50 p-1.5 rounded">
                        <span class="text-slate-400">TOTAL2 (ex-BTC):</span>
                        <div class="text-right">
                            <span id="m-total2" class="text-white font-bold text-xs">--</span>
                            <div id="m-total2-chg" class="text-[9px] font-bold"></div>
                        </div>
                    </div>
                    <div class="flex justify-between items-center bg-gray-900/50 p-1.5 rounded">
                        <span class="text-slate-400">TOTAL3 (ex-BTC/ETH):</span>
                        <div class="text-right">
                            <span id="m-total3" class="text-white font-bold text-xs">--</span>
                            <div id="m-total3-chg" class="text-[9px] font-bold"></div>
                        </div>
                    </div>
                    <div class="flex justify-between items-center bg-gray-900/50 p-1.5 rounded">
                        <span class="text-slate-400">BTC Dominance:</span>
                        <div class="text-right">
                            <span id="m-btc-d" class="text-yellow-400 font-bold text-xs">--</span>
                            <div id="m-btc-d-chg" class="text-[9px] font-bold"></div>
                        </div>
                    </div>
                    <div class="grid grid-cols-2 gap-2 mt-1">
                        <div class="bg-gray-900/50 p-1.5 rounded">
                            <div class="text-[9px] text-slate-500 font-bold">24H VOLUME</div>
                            <div id="m-vol" class="text-white font-bold text-xs mt-0.5">--</div>
                        </div>
                        <div class="bg-gray-900/50 p-1.5 rounded">
                            <div class="text-[9px] text-slate-500 font-bold">FEAR & GREED</div>
                            <div id="m-fng" class="text-white font-bold text-xs mt-0.5">--</div>
                        </div>
                    </div>
                </div>
            </div>
            <div class="splitter-v"></div>
            <div class="panel flex flex-col overflow-hidden" style="flex-grow: 1; min-height: 80px; height: 35%;">
                <div class="title-bar text-yellow-400 flex justify-between"><span>📅 Econ Calendar</span><span class="text-[9px] text-slate-500" id="cal-source">Source</span></div>
                <div class="flex-grow overflow-y-auto">
                    <table class="w-full text-[10px]">
                        <tbody id="m-cal"><tr><td colspan="2" class="text-center italic py-2">Loading Calendar...</td></tr></tbody>
                    </table>
                </div>
            </div>
            <div class="splitter-v"></div>
            <div class="panel flex flex-col overflow-hidden" style="flex-grow: 1; min-height: 100px; height: 40%;">
                <div class="title-bar text-slate-300 flex justify-between"><span>📰 Newswire</span><span class="text-[9px] text-slate-500" id="news-source">Source</span></div>
                <div id="m-news" class="flex-grow overflow-y-auto space-y-2.5 text-[11px] text-slate-300 pr-1">
                    <div class="text-center italic py-2" id="news-status">Loading News...</div>
                </div>
            </div>
        </div>

        <div class="splitter-h"></div>

        <div class="flex flex-col overflow-hidden" style="width: 50%; flex-shrink: 0;" id="col-mid">
            <div class="panel flex justify-between items-center bg-[#0e1726]" style="flex-shrink: 0; min-height: 80px; height: 15%; box-shadow: 0 0 15px rgba(56, 189, 248, 0.05); border-color: #0ea5e9;">
                <div class="w-1/2 border-r border-gray-800 pr-4 text-center flex flex-col items-center justify-center">
                    <div class="flex items-center justify-center gap-2 mb-1 w-full">
                        <h3 class="text-green-400 text-[10px] font-bold tracking-widest">V1 (1H INTRADAY) REGIME</h3>
                        <span id="v1-eq" class="text-white font-bold text-xs bg-gray-900 px-1.5 py-0.5 rounded"></span>
                    </div>
                    <p id="v1-regime" class="text-sm font-bold text-white mb-1 text-center w-full">-</p>
                    <p id="v1-strat" class="text-[11px] text-slate-300 font-bold truncate text-center w-full">-</p>
                    <p id="v1-reason" class="text-[10px] text-sky-200 mt-1.5 leading-tight text-center w-full">-</p>
                </div>
                <div class="w-1/2 pl-4 text-center flex flex-col items-center justify-center">
                    <div class="flex items-center justify-center gap-2 mb-1 w-full">
                        <h3 class="text-purple-400 text-[10px] font-bold tracking-widest">V2 (4H SWING) REGIME</h3>
                        <span id="v2-eq" class="text-white font-bold text-xs bg-gray-900 px-1.5 py-0.5 rounded"></span>
                    </div>
                    <p id="v2-regime" class="text-sm font-bold text-white mb-1 text-center w-full">-</p>
                    <p id="v2-strat" class="text-[11px] text-slate-300 font-bold truncate text-center w-full">-</p>
                    <p id="v2-reason" class="text-[10px] text-sky-200 mt-1.5 leading-tight text-center w-full">-</p>
                </div>
            </div>
            <div class="splitter-v"></div>
            <div class="grid grid-cols-2 gap-3" style="flex-grow: 1; min-height: 120px; height: 45%;">
                <div class="panel flex flex-col overflow-hidden" style="border-top: 2px solid #4ade80; height: 100%;">
                    <h2 class="text-[11px] font-bold text-green-400 mb-2 border-b border-gray-800 pb-1">⚡ ACTIVE INTRADAY SIGNALS</h2>
                    <div id="v1-signals" class="flex-grow overflow-y-auto space-y-2 pr-1"></div>
                </div>
                <div class="panel flex flex-col overflow-hidden" style="border-top: 2px solid #c084fc; height: 100%;">
                    <h2 class="text-[11px] font-bold text-purple-400 mb-2 border-b border-gray-800 pb-1">🦅 ACTIVE SWING SIGNALS</h2>
                    <div id="v2-signals" class="flex-grow overflow-y-auto space-y-2 pr-1"></div>
                </div>
            </div>
            <div class="splitter-v"></div>
            <div class="panel flex flex-col overflow-hidden" style="flex-grow: 1; min-height: 120px; height: 30%; border-top: 2px solid #f59e0b;">
                <div class="title-bar text-amber-400 flex justify-between">
                    <span>🏛️ INSTITUTIONAL MARKET STRUCTURE SCANNER (4-HOUR)</span>
                    <span class="text-[9px] text-slate-500" id="structure-status">Scanning</span>
                </div>
                <div class="flex-grow overflow-y-auto pr-1">
                    <table class="w-full text-left border-collapse text-[10px]">
                        <thead>
                            <tr class="text-left border-b border-gray-800/50">
                                <th class="pb-1 text-slate-500">Asset</th>
                                <th class="pb-1 text-slate-500">4H Trend</th>
                                <th class="pb-1 text-slate-500">Current Price</th>
                                <th class="pb-1 text-slate-500">Swing Low</th>
                                <th class="pb-1 text-slate-500">Swing High</th>
                                <th class="pb-1 text-slate-500">Equilibrium</th>
                                <th class="pb-1 text-slate-500">Premium / Discount Zone</th>
                                <th class="pb-1 text-slate-500">Active Imbalances</th>
                            </tr>
                        </thead>
                        <tbody id="structure-table">
                            <tr><td colspan="8" class="text-center text-slate-600 py-6 italic">Initializing Institutional Market Structure Scanner...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
            <div class="splitter-v"></div>
            <div class="panel flex flex-col overflow-hidden" style="flex-grow: 1; min-height: 120px; height: 30%; border-top: 2px solid #38bdf8;">
                <div class="title-bar text-sky-400 flex justify-between">
                    <span>⚡ VWAP & AVWAP ALGO SIGNALS</span>
                    <span class="text-[9px] text-slate-500" id="vwap-status">Scanning</span>
                </div>
                <div class="flex-grow overflow-y-auto pr-1">
                    <table class="w-full text-left border-collapse text-[10px]">
                        <thead>
                            <tr class="text-left border-b border-gray-800/50">
                                <th class="pb-1 text-slate-500">Time</th>
                                <th class="pb-1 text-slate-500">Asset</th>
                                <th class="pb-1 text-slate-500">Type</th>
                                <th class="pb-1 text-slate-500">Limit Entry</th>
                                <th class="pb-1 text-slate-500">Target (TP)</th>
                                <th class="pb-1 text-slate-500">Stop (SL)</th>
                                <th class="pb-1 text-slate-500">Signal Trigger Rationale</th>
                            </tr>
                        </thead>
                        <tbody id="vwap-table">
                            <tr><td colspan="7" class="text-center text-slate-600 py-6 italic">Initializing VWAP/AVWAP Signals Swarm...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <div class="splitter-h"></div>

        <div class="flex flex-col overflow-hidden" style="width: 25%; flex-shrink: 0;" id="col-right">
            <div class="panel flex flex-col overflow-hidden" style="flex-shrink: 0; min-height: 100px; height: 20%;">
                <div class="title-bar text-rose-400 flex justify-between"><span>🔥 Top Movers</span><span class="text-[9px] text-slate-500">Binance</span></div>
                <div class="flex-grow overflow-y-auto pr-1">
                    <div class="grid grid-cols-2 gap-3 text-[10px]">
                        <div><div class="text-green-400 font-bold border-b border-gray-800 pb-1 mb-1.5 tracking-wider">GAINERS</div><div id="m-gain" class="space-y-1"></div></div>
                        <div><div class="text-rose-400 font-bold border-b border-gray-800 pb-1 mb-1.5 tracking-wider">LOSERS</div><div id="m-lose" class="space-y-1"></div></div>
                    </div>
                </div>
            </div>
            <div class="splitter-v"></div>
            <div class="panel bg-[#0e1726] flex flex-col overflow-hidden" style="flex-shrink: 0; min-height: 90px; height: 18%;">
                <div class="title-bar text-slate-300 flex justify-between items-center">
                    <span>🎲 Derivatives & Flow</span>
                    <button onclick="refreshBox('derivatives', this)" class="text-[10px] text-sky-400 hover:text-sky-300 transition-colors font-semibold px-1 rounded hover:bg-gray-800/50 cursor-pointer">🔄 Refresh</button>
                </div>
                <div class="flex-grow overflow-y-auto space-y-2 text-[11px] pr-1">
                    <div class="flex justify-between items-center bg-gray-900/50 p-1.5 rounded"><span class="text-slate-400">BTC Funding:</span><span id="m-fund" class="text-white font-bold">--</span></div>
                    <div class="flex justify-between items-center bg-gray-900/50 p-1.5 rounded"><span class="text-slate-400">Whale L/S Ratio:</span><span id="m-ls" class="text-sky-400 font-bold">--</span></div>
                    <div class="flex justify-between items-center bg-gray-900/50 p-1.5 rounded"><span class="text-slate-400">Open Interest:</span><span id="m-oi" class="text-white font-bold">--</span></div>
                </div>
            </div>
            <div class="splitter-v"></div>
            <div class="panel flex flex-col overflow-hidden" style="flex-shrink: 0; min-height: 110px; height: 20%;">
                <div class="title-bar text-slate-500 flex justify-between items-center">
                    <span>🩸 BTC Liquidations</span>
                    <div class="flex items-center gap-2">
                        <button onclick="refreshBox('liquidations', this)" class="text-[10px] text-sky-400 hover:text-sky-300 transition-colors font-semibold px-1 rounded hover:bg-gray-800/50 cursor-pointer">🔄 Refresh</button>
                        <span class="text-[9px] text-sky-400 font-bold">Coinalyze</span>
                    </div>
                </div>
                <div id="m-liq" class="flex-grow overflow-y-auto space-y-2 text-[11px] pr-1">
                    <div class="text-center py-2 text-slate-500 italic">Initializing Stream...</div>
                </div>
            </div>
            <div class="splitter-v"></div>
            <div class="panel flex flex-col overflow-hidden" style="flex-grow: 1; min-height: 120px; height: 22%;">
                <div class="title-bar text-sky-400 flex justify-between items-center">
                    <span>⚡ MEXC 15m/1h Movers</span>
                    <div class="flex items-center gap-2">
                        <button onclick="refreshBox('mexc_movers', this)" class="text-[10px] text-sky-400 hover:text-sky-300 transition-colors font-semibold px-1 rounded hover:bg-gray-800/50 cursor-pointer">🔄 Refresh</button>
                        <span class="text-[9px] text-slate-500" id="mexc-status">Starting...</span>
                    </div>
                </div>
                <div class="flex-grow overflow-y-auto pr-1">
                    <table class="w-full text-left border-collapse text-[10px]">
                        <thead>
                            <tr class="text-left border-b border-gray-800/50">
                                <th class="pb-1 text-slate-500">Asset</th>
                                <th class="pb-1 text-slate-500">Price</th>
                                <th class="pb-1 text-slate-500 text-right">15m</th>
                                <th class="pb-1 text-slate-500 text-right">1h</th>
                            </tr>
                        </thead>
                        <tbody id="mexc-movers-table">
                            <tr><td colspan="4" class="text-center text-slate-500 py-4 italic">Connecting...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
            <div class="splitter-v"></div>
            <div class="panel flex flex-col overflow-hidden" style="flex-grow: 1; min-height: 120px; height: 20%;">
                <div class="title-bar text-white flex justify-between"><span>📜 SHADOW LEDGER</span><span class="text-[9px] text-slate-500">Closed</span></div>
                <div class="flex-grow overflow-y-auto pr-1">
                    <table class="w-full text-left border-collapse text-[10px]">
                        <thead><tr><th class="pb-1 text-slate-500">Time</th><th class="pb-1 text-slate-500">Asset</th><th class="pb-1 text-slate-500">Side</th><th class="pb-1 text-slate-500">Res</th><th class="pb-1 text-slate-500 text-right">PnL</th></tr></thead>
                        <tbody id="history-table">
                            <tr><td colspan="5" class="text-center text-slate-600 py-6 italic">No closed trades yet. Waiting for Ledger resolution...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>

    <!-- Resolution Modal -->
    <div id="resolve-modal" class="fixed inset-0 bg-black/80 flex items-center justify-center hidden z-50">
        <div class="bg-[#0b1120] border border-[#1e293b] rounded-lg p-5 w-80 shadow-2xl relative text-slate-300">
            <h3 class="text-white font-bold text-sm mb-3 border-b border-gray-800 pb-1.5 flex justify-between">
                <span>MANUAL RESOLUTION</span>
                <span id="modal-asset-side" class="text-sky-400 font-mono">BTC - LONG</span>
            </h3>
            
            <input type="hidden" id="modal-trade-id">
            <input type="hidden" id="modal-engine">
            
            <div class="mb-4">
                <label class="block text-[10px] text-slate-400 font-bold uppercase tracking-wider mb-1">Resolution Mode</label>
                <select id="modal-action-select" onchange="toggleModalPriceInput()" class="w-full bg-gray-900 border border-gray-800 rounded px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-sky-500">
                    <option value="CLOSE">Closed Earlier (Custom Price)</option>
                    <option value="IGNORED">Completely Ignored</option>
                </select>
            </div>
            
            <div class="mb-4" id="modal-price-container">
                <label class="block text-[10px] text-slate-400 font-bold uppercase tracking-wider mb-1">Custom Close Price (Entry: <span id="modal-entry-val">0.00</span>)</label>
                <input type="number" step="any" id="modal-close-price" placeholder="Enter exit price" class="w-full bg-gray-900 border border-gray-800 rounded px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-sky-500 font-mono">
            </div>
            
            <div class="flex justify-end gap-2 mt-5">
                <button onclick="closeResolveModal()" class="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-xs text-slate-300 font-bold rounded transition-colors cursor-pointer">Cancel</button>
                <button id="modal-submit-btn" onclick="submitManualResolution()" class="px-3 py-1.5 bg-sky-600 hover:bg-sky-500 text-xs text-white font-bold rounded transition-colors flex items-center gap-1 cursor-pointer">
                    <span>Resolve Trade</span>
                </button>
            </div>
        </div>
    </div>

    <script>
        function fmt(n) { return "$" + parseFloat(n).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2}); }
        function pFmt(n) { return n >= 0.1 ? n.toFixed(4) : parseFloat(n).toFixed(8).replace(/0+$/, '').replace(/\.$/, ''); }
        function fmtLiq(val) {
            if (val >= 1000000) return "$" + (val / 1000000).toFixed(2) + "M";
            if (val >= 1000) return "$" + (val / 1000).toFixed(0) + "K";
            return "$" + val.toFixed(0);
        }

        function formatSignalTime(ts) {
            if (!ts) return "--:--:--";
            const date = new Date(ts * 1000);
            const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
            const month = months[date.getMonth()];
            const day = String(date.getDate()).padStart(2, '0');
            const hms = [date.getHours(), date.getMinutes(), date.getSeconds()].map(x => String(x).padStart(2, '0')).join(':');
            return `${month}-${day} ${hms}`;
        }

        async function refreshBox(target, btn) {
            if (btn.disabled) return;
            const originalText = btn.innerText;
            btn.innerText = "⏳ ...";
            btn.disabled = true;
            try {
                const res = await fetch(`/api/refresh?target=${target}`);
                const data = await res.json();
                if (data.success) {
                    btn.innerText = "✅ Done";
                    await fetchUI();
                } else {
                    btn.innerText = "❌ Fail";
                }
            } catch (err) {
                btn.innerText = "❌ Error";
            }
            setTimeout(() => {
                btn.innerText = originalText;
                btn.disabled = false;
            }, 1500);
        }

        function openResolveModal(tradeId, engine, asset, side, entry) {
            document.getElementById('modal-trade-id').value = tradeId;
            document.getElementById('modal-engine').value = engine;
            document.getElementById('modal-asset-side').innerText = `${asset} - ${side}`;
            document.getElementById('modal-entry-val').innerText = pFmt(entry);
            document.getElementById('modal-close-price').value = entry;
            document.getElementById('modal-action-select').value = "CLOSE";
            toggleModalPriceInput();
            document.getElementById('resolve-modal').classList.remove('hidden');
        }

        function closeResolveModal() {
            document.getElementById('resolve-modal').classList.add('hidden');
        }

        function toggleModalPriceInput() {
            const val = document.getElementById('modal-action-select').value;
            const container = document.getElementById('modal-price-container');
            if (val === 'IGNORED') {
                container.style.display = 'none';
            } else {
                container.style.display = 'block';
            }
        }

        async function submitManualResolution() {
            const tradeId = document.getElementById('modal-trade-id').value;
            const engine = document.getElementById('modal-engine').value;
            const action = document.getElementById('modal-action-select').value;
            const closePriceVal = document.getElementById('modal-close-price').value;
            
            const submitBtn = document.getElementById('modal-submit-btn');
            if (submitBtn.disabled) return;
            submitBtn.disabled = true;
            submitBtn.innerText = "Resolving...";
            
            try {
                const payload = {
                    trade_id: tradeId,
                    engine: engine,
                    result: action === 'IGNORED' ? 'IGNORED' : 'CLOSE',
                    close_price: action === 'IGNORED' ? null : parseFloat(closePriceVal)
                };
                
                const res = await fetch('/api/resolve-trade', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                
                const data = await res.json();
                if (data.success) {
                    closeResolveModal();
                    await fetchUI();
                } else {
                    alert("Failed to resolve trade: " + (data.error || "unknown error"));
                }
            } catch (err) {
                alert("Error calling resolve API: " + err.message);
            } finally {
                submitBtn.disabled = false;
                submitBtn.innerText = "Resolve Trade";
            }
        }

        function renderChg(chg4h, chg24h) {
            if (chg4h === undefined || chg24h === undefined || chg4h === null || chg24h === null) return `<span class="text-slate-600">--</span>`;
            const f4 = chg4h >= 0 ? `<span class="win">+${chg4h.toFixed(1)}%</span>` : `<span class="loss">${chg4h.toFixed(1)}%</span>`;
            const f24 = chg24h >= 0 ? `<span class="win">+${chg24h.toFixed(1)}%</span>` : `<span class="loss">${chg24h.toFixed(1)}%</span>`;
            return `<span class="text-slate-500 text-[8px] font-mono">4H:</span> ${f4} <span class="text-slate-500 text-[8px] ml-1 font-mono">24H:</span> ${f24}`;
        }

        async function fetchUI() {
            try {
                const now = new Date();
                const timeStr = [now.getHours(), now.getMinutes(), now.getSeconds()].map(x => String(x).padStart(2, '0')).join(':');
                document.getElementById('sync-time').innerText = timeStr;
                
                const res = await fetch('/api/data?nocache=' + new Date().getTime());
                const data = await res.json();
                
                const m = data.macro;
                document.getElementById('market-sync').innerText = m.last_updated || "--:--:--";
                
                if (m.pulse && m.pulse.mcap !== "Loading...") {
                    document.getElementById('m-mcap').innerText = m.pulse.mcap;
                    document.getElementById('m-total2').innerText = m.pulse.total2 || "--";
                    document.getElementById('m-total3').innerText = m.pulse.total3 || "--";
                    document.getElementById('m-btc-d').innerText = m.pulse.btc_d;
                    document.getElementById('m-vol').innerText = m.pulse.vol;

                    const chg = m.pulse.changes || {};
                    document.getElementById('m-mcap-chg').innerHTML = renderChg(chg.mcap_4h, chg.mcap_24h);
                    document.getElementById('m-total2-chg').innerHTML = renderChg(chg.total2_4h, chg.total2_24h);
                    document.getElementById('m-total3-chg').innerHTML = renderChg(chg.total3_4h, chg.total3_24h);
                    document.getElementById('m-btc-d-chg').innerHTML = renderChg(chg.btc_d_4h, chg.btc_d_24h);

                    let f = String(m.pulse.fng).split(" ")[0]; 
                    let fColor = parseInt(f) > 60 ? 'text-green-400' : (parseInt(f) < 40 ? 'text-red-400' : 'text-yellow-400');
                    document.getElementById('m-fng').innerHTML = `<span class="${fColor}">${m.pulse.fng}</span>`;
                    document.getElementById('m-fund').innerText = m.derivatives.funding;
                    document.getElementById('m-ls').innerText = m.derivatives.ls_ratio;
                    document.getElementById('m-oi').innerText = m.derivatives.oi;
                }

                if (m.calendar && m.calendar.length > 0) {
                    document.getElementById('cal-source').innerText = m.calendar_status.includes("ForexFactory") ? "ForexFactory" : "TwelveData";
                    document.getElementById('m-cal').innerHTML = m.calendar.map(c => `
                        <tr class="border-b border-gray-800/50 last:border-0 hover:bg-gray-800/30">
                            <td class="text-white truncate max-w-[120px] pb-1.5"><span class="${c.impact === 'High' ? 'text-red-400' : 'text-yellow-400'} font-bold mr-1">[${c.currency}]</span>${c.title}</td>
                            <td class="text-slate-400 text-right whitespace-nowrap pb-1.5">${c.time}</td>
                        </tr>
                    `).join('');
                } else {
                     document.getElementById('m-cal').innerHTML = `<tr><td colspan="2" class="text-center py-2 ${m.calendar_status.includes('Error') || m.calendar_status.includes('HTTP') ? 'text-yellow-500 bg-yellow-900/20' : 'text-slate-500'} rounded font-bold text-[10px] break-words">${m.calendar_status}</td></tr>`;
                }

                if (m.movers && m.movers.gainers.length > 0) {
                    document.getElementById('m-gain').innerHTML = m.movers.gainers.map(g => `<div class="flex justify-between items-center bg-gray-900/50 px-1.5 py-1 rounded"><span class="font-bold text-white">${g.sym}</span><span class="win">+${g.pct.toFixed(1)}%</span></div>`).join('');
                    document.getElementById('m-lose').innerHTML = m.movers.losers.map(l => `<div class="flex justify-between items-center bg-gray-900/50 px-1.5 py-1 rounded"><span class="font-bold text-white">${l.sym}</span><span class="loss">${l.pct.toFixed(1)}%</span></div>`).join('');
                }

                if (m.news && m.news.length > 0) {
                    document.getElementById('news-source').innerText = m.news_status.includes("API") ? "NewsAPI" : (m.news_status.includes("CryptoCompare") ? "CryptoCompare" : "CoinDesk");
                    
                    const getRelativeTime = (ts) => {
                        if (!ts) return '';
                        const diff = Math.floor(Date.now() / 1000) - ts;
                        if (diff < 60) return 'just now';
                        if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
                        if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
                        return Math.floor(diff / 86400) + 'd ago';
                    };

                    document.getElementById('m-news').innerHTML = m.news.map(n => {
                        const relTime = getRelativeTime(n.time);
                        return `
                        <div class="border-l-2 border-sky-900 pl-2 hover:bg-gray-800/50 rounded transition-colors">
                            <a href="${n.url || '#'}" target="_blank" class="hover:text-white transition-colors block text-[11px] font-bold ${n.title.includes('Error') || n.title.includes('unavailable') ? 'text-yellow-500' : ''}">${n.title}</a>
                            <div class="text-[9px] text-sky-500/70 mt-0.5 uppercase tracking-wider flex justify-between">
                                <span>${n.source}</span>
                                <span>${relTime}</span>
                            </div>
                        </div>
                        `;
                    }).join('');
                } else {
                    document.getElementById('m-news').innerHTML = `<div class="text-center py-2 font-bold text-yellow-500 bg-yellow-900/20 rounded text-[10px] break-words">${m.news_status}</div>`;
                }

                if (m.liquidations.data) {
                    const liq = m.liquidations.data;
                    const renderBar = (label, lVal, sVal) => {
                        const total = lVal + sVal;
                        const lPct = total > 0 ? (lVal / total) * 100 : 50;
                        const sPct = total > 0 ? (sVal / total) * 100 : 50;
                        return `
                            <div class="mb-2.5">
                                <div class="text-[10px] font-bold text-slate-400 mb-1">${label}</div>
                                <div class="w-full bg-gray-800 h-1.5 rounded flex overflow-hidden">
                                    <div style="width: ${lPct}%" class="bg-red-500 h-full"></div>
                                    <div style="width: ${sPct}%" class="bg-emerald-500 h-full"></div>
                                </div>
                                <div class="flex justify-between text-[9px] mt-0.5 font-bold">
                                    <span class="text-red-400">${fmtLiq(lVal)} longs</span>
                                    <span class="text-emerald-400">${fmtLiq(sVal)} shorts</span>
                                </div>
                            </div>
                        `;
                    };
                    document.getElementById('m-liq').innerHTML = `
                        <div class="mt-1.5">
                            ${renderBar('1H', liq['1h_l_raw'], liq['1h_s_raw'])}
                            ${renderBar('4H', liq['4h_l_raw'], liq['4h_s_raw'])}
                            ${renderBar('24H', liq['24h_l_raw'], liq['24h_s_raw'])}
                        </div>`;
                } else {
                     document.getElementById('m-liq').innerHTML = `<div class="text-center py-2 ${m.liquidations.status.includes('Error') || m.liquidations.status.includes('Missing') || m.liquidations.status.includes('HTTP') ? 'text-yellow-500 bg-yellow-900/20' : 'text-slate-500'} rounded font-bold px-1 text-[10px] break-words">${m.liquidations.status}</div>`;
                }

                // MEXC Movers Update
                if (m.mexc_movers) {
                    document.getElementById('mexc-status').innerText = m.mexc_movers.status || "Live";
                    const movers = m.mexc_movers.movers || [];
                    if (movers.length > 0) {
                        document.getElementById('mexc-movers-table').innerHTML = movers.map(v => {
                            const show_15m = v.has_15m ? `${v.chg_15m >= 0 ? '+' : ''}${v.chg_15m.toFixed(1)}%` : '--';
                            const show_1h = v.has_1h ? `${v.chg_1h >= 0 ? '+' : ''}${v.chg_1h.toFixed(1)}%` : '--';
                            
                            const class_15m = v.has_15m ? (v.chg_15m >= 0 ? 'win' : 'loss') : 'text-slate-600';
                            const class_1h = v.has_1h ? (v.chg_1h >= 0 ? 'win' : 'loss') : 'text-slate-600';
                            
                            return `
                                <tr class="hover:bg-gray-800/30 border-b border-gray-800/30 last:border-0">
                                    <td class="font-bold text-white py-1">${v.symbol}</td>
                                    <td class="text-slate-300 py-1 font-mono">${v.price_str}</td>
                                    <td class="${class_15m} font-bold py-1 text-right font-mono">${show_15m}</td>
                                    <td class="${class_1h} font-bold py-1 text-right font-mono">${show_1h}</td>
                                </tr>
                            `;
                        }).join('');
                    } else {
                        document.getElementById('mexc-movers-table').innerHTML = `
                            <tr><td colspan="4" class="text-center text-slate-500 py-4 italic">No major movers (≥5%) found.</td></tr>
                        `;
                    }
                }

                // MARKET STRUCTURE RADAR UPDATE
                if (m.structure_radar && m.structure_radar.length > 0) {
                    document.getElementById('structure-status').innerText = m.structure_status || "Complete";
                    document.getElementById('structure-table').innerHTML = m.structure_radar.map(s => {
                        const rPct = s.ratio * 100;
                        let barColor = "bg-red-500"; 
                        let textColor = "text-red-400 font-bold";
                        if (s.zone === "DISCOUNT") {
                            barColor = "bg-green-500";
                            textColor = "text-green-400 font-bold";
                        } else if (s.zone === "EQUILIBRIUM") {
                            barColor = "bg-gray-500";
                            textColor = "text-gray-400 font-bold";
                        }
                        const trendColor = s.trend === "BULLISH" ? "text-green-400 font-bold" : "text-red-400 font-bold";
                        const fvgColor = s.fvg_status.includes("Bullish") ? "text-green-400" : (s.fvg_status.includes("Bearish") ? "text-red-400" : "text-slate-500");
                        
                        return `
                        <tr class="hover:bg-gray-800/30 border-b border-gray-800/30 last:border-0">
                            <td class="font-bold text-white py-1.5">${s.asset}</td>
                            <td class="${trendColor} py-1.5">${s.trend}</td>
                            <td class="text-white font-mono py-1.5">${pFmt(s.current_price)}</td>
                            <td class="text-slate-400 font-mono py-1.5">${pFmt(s.swing_low)}</td>
                            <td class="text-slate-400 font-mono py-1.5">${pFmt(s.swing_high)}</td>
                            <td class="text-slate-400 font-mono py-1.5">${pFmt(s.equilibrium)}</td>
                            <td class="py-1.5 pr-4">
                                <div class="flex items-center gap-2">
                                    <div class="w-20 bg-gray-900 h-2 rounded flex overflow-hidden border border-gray-800">
                                        <div style="width: ${rPct}%" class="${barColor} h-full"></div>
                                    </div>
                                    <span class="${textColor} text-[9px] whitespace-nowrap">${s.zone} (${rPct.toFixed(0)}%)</span>
                                </div>
                            </td>
                            <td class="${fvgColor} py-1.5">${s.fvg_status}</td>
                        </tr>
                        `;
                    }).join('');
                } else {
                    document.getElementById('structure-status').innerText = m.structure_status || "Scanning...";
                    document.getElementById('structure-table').innerHTML = `<tr><td colspan="8" class="text-center py-6 text-slate-600 italic">Initializing Market Structure Scanner. Scanning top 50 perpetual assets...</td></tr>`;
                }

                // VWAP RADAR UPDATE
                // VWAP RADAR ALGO SIGNALS UPDATE
                if (m.vwap_radar && m.vwap_radar.length > 0) {
                    document.getElementById('vwap-status').innerText = m.vwap_status || "Complete";
                    document.getElementById('vwap-table').innerHTML = m.vwap_radar.map(v => `
                        <tr class="hover:bg-gray-800/30 border-b border-gray-800/30 last:border-0">
                            <td class="text-slate-400 py-1">${v.time}</td>
                            <td class="font-bold text-white py-1">${v.asset}</td>
                            <td class="py-1"><span class="${v.side === 'LONG' ? 'win' : 'loss'} font-bold text-[9px] px-1 py-0.5 rounded bg-gray-900/40">${v.type}</span></td>
                            <td class="win font-bold py-1">${v.entry}</td>
                            <td class="win py-1 text-green-400/85">${v.tp}</td>
                            <td class="loss py-1 text-rose-400/85">${v.sl}</td>
                            <td class="text-slate-300 py-1 truncate max-w-[280px]" title="${v.reason}">${v.reason}</td>
                        </tr>
                    `).join('');
                } else {
                    document.getElementById('vwap-status').innerText = m.vwap_status || "Scanning...";
                    document.getElementById('vwap-table').innerHTML = `<tr><td colspan="7" class="text-center py-6 text-slate-600 italic">No active VWAP/AVWAP limit setups. Scanning top 100 perpetual assets...</td></tr>`;
                }

                // ENGINES & SIGNALS
                document.getElementById('v1-regime').innerText = data.v1_cfg?.market_regime || "Waiting...";
                document.getElementById('v1-strat').innerText = `${data.v1_cfg?.active_strategy || "-"} (${data.v1_cfg?.directional_bias || "-"}) | Risk: ${data.v1_cfg?.risk_per_trade_pct || 0}%`;
                document.getElementById('v1-reason').innerText = `"${data.v1_cfg?.reasoning_summary || "..."}"`;
                
                document.getElementById('v2-regime').innerText = data.v2_cfg?.market_regime || "Waiting...";
                document.getElementById('v2-strat').innerText = `${data.v2_cfg?.active_strategy || "-"} (${data.v2_cfg?.directional_bias || "-"}) | Risk: ${data.v2_cfg?.risk_per_trade_pct || 0}%`;
                document.getElementById('v2-reason').innerText = `"${data.v2_cfg?.reasoning_summary || "..."}"`;

                document.getElementById('v1-eq').innerText = fmt(data.v1_st?.current_equity || 50000);
                document.getElementById('v2-eq').innerText = fmt(data.v2_st?.current_equity || 50000);

                const buildSigs = (trades, engine) => {
                    if (!trades || !trades.length) return `<div class="text-slate-600 italic text-center py-4 text-xs bg-[#0e1726] rounded border border-gray-800">Radar sweeping... No setups.</div>`;
                    let sorted = [...trades].sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));

                    return sorted.map(t => {
                        let timeStr = t.timestamp ? formatSignalTime(t.timestamp) : "--:--:--";
                        return `
                        <div class="p-2 bg-[#0e1726] rounded border border-gray-800 flex justify-between items-center relative hover:bg-gray-800 transition-colors">
                            <div>
                                <div class="flex items-center gap-1.5 mb-0.5">
                                    <span class="text-[9px] font-bold ${t.side === 'LONG' ? 'bg-green-900/40 text-green-400' : 'bg-red-900/40 text-red-400'} px-1 py-0.5 rounded tracking-wider">${t.side}</span>
                                    <span class="text-white font-bold text-xs">${t.asset}</span>
                                    <button onclick="openResolveModal('${t.id}', '${engine}', '${t.asset}', '${t.side}', ${t.entry})" class="ml-2 text-[9px] text-rose-400 hover:text-rose-300 hover:bg-rose-950/50 px-1 py-0.5 rounded font-bold border border-rose-900/40 cursor-pointer">❌ Close</button>
                                </div>
                                <div class="text-[10px] text-slate-400">Ent: <span class="text-slate-200">${pFmt(t.entry)}</span> | R: ${fmt(t.risk_amt)}</div>
                            </div>
                            <div class="text-right text-[10px]">
                                <div class="text-[9px] text-sky-400/80 mb-1 font-mono tracking-widest bg-sky-900/20 px-1 py-0.5 rounded inline-block">⏱ ${timeStr}</div>
                                <div class="text-slate-400 mb-[1px]">TP: <span class="win font-bold">${pFmt(t.tp)}</span></div>
                                <div class="text-slate-400">SL: <span class="loss font-bold">${pFmt(t.sl)}</span></div>
                            </div>
                        </div>
                        `;
                    }).join('');
                };
                
                document.getElementById('v1-signals').innerHTML = buildSigs(data.v1_st?.active_trades, 'v1');
                document.getElementById('v2-signals').innerHTML = buildSigs(data.v2_st?.active_trades, 'v2');

                let history = [];
                if(data.v1_st?.trade_history) history.push(...data.v1_st.trade_history.map(t => ({...t, eng: 'V1 (1H)', color: 'text-green-400'})));
                if(data.v2_st?.trade_history) history.push(...data.v2_st.trade_history.map(t => ({...t, eng: 'V2 (4H)', color: 'text-purple-400'})));
                
                history.sort((a,b) => {
                    let d1 = new Date((b.close_time || "").replace(' ', 'T')).getTime() || 0;
                    let d2 = new Date((a.close_time || "").replace(' ', 'T')).getTime() || 0;
                    return d1 - d2;
                });

                const tbody = document.getElementById('history-table');
                if(!history.length) {
                    tbody.innerHTML = '<tr><td colspan="5" class="text-center text-slate-600 py-6 italic">No closed trades yet. Waiting for Ledger resolution...</td></tr>';
                } else {
                    tbody.innerHTML = history.slice(0, 30).map(t => {
                        let shortTime = t.close_time.substring(5, 16); 
                        return `
                        <tr class="hover:bg-gray-800 transition-colors border-b border-gray-800/30 last:border-0">
                            <td class="text-slate-400 whitespace-nowrap pb-1">${shortTime}</td>
                            <td class="font-bold text-white pb-1">${t.asset}</td>
                            <td class="${t.side === 'LONG' ? 'win' : 'loss'} font-bold pb-1">${t.side}</td>
                            <td class="${t.result === 'WIN' ? 'win' : 'loss'} font-bold pb-1">${t.result}</td>
                            <td class="${t.pnl > 0 ? 'win' : 'loss'} font-bold text-right pb-1 tabular-nums">${t.pnl > 0 ? '+' : ''}${fmt(t.pnl)}</td>
                        </tr>
                        `;
                    }).join('');
                }

            } catch (err) {}
        }

        fetchUI();
        setInterval(fetchUI, 3000);

        // --- SPLITTER RESIZING DRAG CONTROLLER ---
        // Horizontal Resizer (Columns)
        document.querySelectorAll('.splitter-h').forEach(splitter => {
            splitter.addEventListener('mousedown', function(e) {
                e.preventDefault();
                const leftCol = splitter.previousElementSibling;
                const rightCol = splitter.nextElementSibling;
                
                const leftWidth = leftCol.getBoundingClientRect().width;
                const rightWidth = rightCol.getBoundingClientRect().width;
                const startX = e.clientX;
                const containerWidth = splitter.parentElement.getBoundingClientRect().width;
                
                splitter.classList.add('active');

                function onMouseMove(e) {
                    const deltaX = e.clientX - startX;
                    const newLeftWidthPct = ((leftWidth + deltaX) / containerWidth) * 100;
                    const newRightWidthPct = ((rightWidth - deltaX) / containerWidth) * 100;
                    
                    if (newLeftWidthPct > 15 && newRightWidthPct > 15) {
                        leftCol.style.width = newLeftWidthPct + '%';
                        rightCol.style.width = newRightWidthPct + '%';
                    }
                }

                function onMouseUp() {
                    splitter.classList.remove('active');
                    document.removeEventListener('mousemove', onMouseMove);
                    document.removeEventListener('mouseup', onMouseUp);
                }

                document.addEventListener('mousemove', onMouseMove);
                document.addEventListener('mouseup', onMouseUp);
            });
        });

        // Vertical Resizer (Panels)
        document.querySelectorAll('.splitter-v').forEach(splitter => {
            splitter.addEventListener('mousedown', function(e) {
                e.preventDefault();
                const topPanel = splitter.previousElementSibling;
                const bottomPanel = splitter.nextElementSibling;
                
                const topHeight = topPanel.getBoundingClientRect().height;
                const bottomHeight = bottomPanel.getBoundingClientRect().height;
                const startY = e.clientY;
                const columnHeight = splitter.parentElement.getBoundingClientRect().height;
                
                splitter.classList.add('active');
                
                topPanel.style.flexGrow = '0';
                topPanel.style.flexShrink = '0';
                bottomPanel.style.flexGrow = '0';
                bottomPanel.style.flexShrink = '0';

                function onMouseMove(e) {
                    const deltaY = e.clientY - startY;
                    const newTopHeightPct = ((topHeight + deltaY) / columnHeight) * 100;
                    const newBottomHeightPct = ((bottomHeight - deltaY) / columnHeight) * 100;
                    
                    if (newTopHeightPct > 4 && newBottomHeightPct > 4) {
                        topPanel.style.height = newTopHeightPct + '%';
                        bottomPanel.style.height = newBottomHeightPct + '%';
                    }
                }

                function onMouseUp() {
                    splitter.classList.remove('active');
                    document.removeEventListener('mousemove', onMouseMove);
                    document.removeEventListener('mouseup', onMouseUp);
                }

                document.addEventListener('mousemove', onMouseMove);
                document.addEventListener('mouseup', onMouseUp);
            });
        });
    </script>
</body>
</html>
"""

class ServerHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
            self.end_headers()
            self.wfile.write(HTML.encode('utf-8'))
            
        elif self.path.startswith('/api/data'):
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
            self.end_headers()
            
            data = {
                "macro": GLOBAL_MARKET_DATA,
                "v1_cfg": read_json("trader_v1_config.json"),
                "v2_cfg": read_json("trader_v2_config.json"),
                "v1_st": read_json("prop_state.json"),
                "v2_st": read_json("prop_state_v2.json")
            }
            self.wfile.write(json.dumps(data).encode('utf-8'))
            
        elif self.path.startswith('/api/refresh'):
            import urllib.parse
            parsed_url = urllib.parse.urlparse(self.path)
            query_params = urllib.parse.parse_qs(parsed_url.query)
            target = query_params.get('target', [None])[0]
            
            success = False
            error_msg = ""
            if target == 'derivatives':
                try:
                    update_derivatives_data()
                    success = True
                except Exception as e: error_msg = str(e)
            elif target == 'liquidations':
                try:
                    update_liquidations_data()
                    success = True
                except Exception as e: error_msg = str(e)
            elif target == 'mexc_movers':
                try:
                    update_mexc_movers_data()
                    success = True
                except Exception as e: error_msg = str(e)
            else:
                error_msg = "Invalid target"
                
            if success:
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "target": target}).encode('utf-8'))
            else:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": error_msg}).encode('utf-8'))
            
        else:
            self.send_response(404)
            self.end_headers()
            
    def do_POST(self):
        if self.path == '/api/resolve-trade':
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            try:
                params = json.loads(post_data.decode('utf-8'))
                trade_id = params.get('trade_id')
                engine = params.get('engine') # 'v1' or 'v2'
                result_type = params.get('result') # 'CLOSE' or 'IGNORED'
                close_price = params.get('close_price')
                
                filename = "prop_state.json" if engine == 'v1' else "prop_state_v2.json"
                state = read_json(filename)
                
                active_trades = state.get("active_trades", [])
                target_trade = None
                remaining_trades = []
                for t in active_trades:
                    if t.get("id") == trade_id:
                        target_trade = t
                    else:
                        remaining_trades.append(t)
                
                if not target_trade:
                    self.send_response(404)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(b'{"error": "Trade not found"}')
                    return
                
                pnl = 0.0
                final_result = "IGNORED"
                
                if result_type == "IGNORED":
                    final_result = "IGNORED"
                    pnl = 0.0
                else:
                    close_price = float(close_price)
                    entry = float(target_trade["entry"])
                    sl = float(target_trade["sl"])
                    risk_amt = float(target_trade["risk_amt"])
                    
                    if target_trade["side"] == "LONG":
                        pnl = risk_amt * (close_price - entry) / abs(entry - sl)
                    else:
                        pnl = risk_amt * (entry - close_price) / abs(entry - sl)
                    
                    final_result = "WIN" if pnl >= 0 else "LOSS"
                
                equity = float(state.get("current_equity", 50000.0))
                equity += pnl
                state["current_equity"] = equity
                
                hwm = float(state.get("high_water_mark", 50000.0))
                if equity > hwm:
                    state["high_water_mark"] = equity
                
                state["active_trades"] = remaining_trades
                
                if "trade_history" not in state:
                    state["trade_history"] = []
                
                history_entry = target_trade.copy()
                history_entry["pnl"] = pnl
                history_entry["result"] = final_result
                history_entry["close_time"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                if close_price is not None:
                    history_entry["close_price"] = close_price
                
                state["trade_history"].insert(0, history_entry)
                state["trade_history"] = state["trade_history"][:50]
                
                write_json(filename, state)
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "new_equity": equity, "pnl": pnl, "result": final_result}).encode('utf-8'))
                return
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
                return
        else:
            self.send_response(404)
            self.end_headers()
            
    def log_message(self, format, *args): pass 

class MyHTTPServer(HTTPServer):
    allow_reuse_address = True

if __name__ == '__main__':
    port = 8081
    print("\n" + "="*60)
    print(f"👉 V10 TERMINAL (PERFECT SYNC) LIVE AT: http://localhost:{port}")
    print("="*60 + "\n")
    try: MyHTTPServer(('0.0.0.0', port), ServerHandler).serve_forever()
    except KeyboardInterrupt: pass