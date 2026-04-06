import pandas as pd
import time
from datetime import datetime

# ================= CONFIG =================
BANK_NAMES = ["HDFCBANK","ICICIBANK","SBIN","AXISBANK","KOTAKBANK"]

LOT_SIZES = {
    "BANKNIFTY": 30,
    "HDFCBANK": 550,
    "ICICIBANK": 700,
    "SBIN": 750,
    "AXISBANK": 625,
    "KOTAKBANK": 2000
}

option_history = {}
accumulator = {}
timer_store = {}
last_oi_store = {}

_options_df = None
_futures_df = None

# ================= LOAD =================
def load_options():
    global _options_df
    if _options_df is None:
        df = pd.read_csv("instruments.csv")
        df['expiry'] = pd.to_datetime(df['expiry'], dayfirst=True)
        _options_df = df[df['segment']=="NFO-OPT"]
    return _options_df

def load_futures():
    global _futures_df
    if _futures_df is None:
        df = pd.read_csv("instruments.csv")
        df['expiry'] = pd.to_datetime(df['expiry'], dayfirst=True)
        _futures_df = df[df['segment'].str.contains("FUT")]
    return _futures_df

def get_expiry(df):
    today = pd.Timestamp.now()
    df = df[df['expiry'].dt.month == today.month]
    exps = sorted(df['expiry'].unique())
    return exps[0]

def get_future(name):
    df = load_futures()
    f = df[df['name'].str.contains(name, na=False)]
    exp = get_expiry(f)
    return "NFO:" + f[f['expiry']==exp].iloc[0]['tradingsymbol']

def get_options(name, ltp):
    df = load_options()
    opt = df[df['name'].str.contains("BANKNIFTY", na=False)] if name == "BANKNIFTY" else df[df['name'] == name]
    exp = get_expiry(opt)
    opt = opt[opt['expiry']==exp]
    strikes = sorted(opt['strike'].unique())
    atm = min(strikes, key=lambda x: abs(x-ltp))
    idx = strikes.index(atm)
    return opt.iloc[max(0,idx-10):idx+ATM+10]

# ================= FORMATTING (Aligned to Screenshots) =================

def format_alert(symbol, lots, price, fut, prev, change, curr, inst, opt_type):
    """Formats Burst Alerts for CE/PE"""
    # Tag logic based on strength
    if lots >= 500: tag = "🔥 BURST ALERT"
    elif lots >= 300: tag = "🌟 AWESOME"
    else: tag = "✅ GOOD"

    # Action Mapping
    if inst == "BUY":
        action = "CALL BUY 🔵" if opt_type == "CE" else "PUT BUY 🔴"
    elif inst == "WRITER":
        action = "CALL WRITER ✍️" if opt_type == "CE" else "PUT WRITER ✍️"
    elif inst == "SC":
        action = f"SHORT COVERING ({opt_type}) 🔥"
    else:
        action = f"LONG UNWINDING ({opt_type}) ⚠️"

    arrow = "▲" if change >= 0 else "▼"

    return (f"*{tag}*\n"
            f"🚨 *{action}*\n"
            f"Symbol: `{symbol}`\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📦 *LOTS*: `{lots}`\n"
            f"💰 *PRICE*: `{price:.2f}` ({arrow})\n"
            f"📈 *FUT*: `{fut:.2f}`\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📊 *OI CHG*: `{change:+,}`\n"
            f"⏰ *TIME*: {datetime.now().strftime('%H:%M:%S')}")

def format_future(symbol, lots, price, prev, change, curr):
    """Formats Future Burst Alerts"""
    tag = "🔥 FUTURE BURST" if lots >= 400 else "⚡ FUT ALERT"
    action = "FUTURE BUY 📈" if change > 0 else "FUTURE SELL 📉"
    arrow = "▲" if change > 0 else "▼"

    return (f"*{tag}*\n"
            f"🚨 *{action}*\n"
            f"Symbol: `{symbol}`\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📦 *LOTS*: `{lots}`\n"
            f"💰 *PRICE*: `{price:.2f}` ({arrow})\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📊 *OI CHG*: `{change:+,}`\n"
            f"⏰ *TIME*: {datetime.now().strftime('%H:%M:%S')}")

# ================= CORE LOGIC =================

def process_options(name, opt_df, quotes, alerts, fut_price):
    now = time.time()
    for _, row in opt_df.iterrows():
        token = str(int(row['instrument_token']))
        if token not in quotes: continue
        
        q = quotes[token]
        oi, price = q.get("oi", 0), q.get("last_price", 0)
        prev = option_history.get(token, 0)
        change = oi - prev
        option_history[token] = oi

        key = row['tradingsymbol']
        if key not in accumulator:
            accumulator[key] = {"oi":0,"start":price,"end":price}
            timer_store[key] = now

        accumulator[key]["oi"] += change
        accumulator[key]["end"] = price

        if now - timer_store[key] >= 60: # 1-minute window for bursts
            data = accumulator[key]
            lot = LOT_SIZES["BANKNIFTY"] if "BANKNIFTY" in key else LOT_SIZES[name]
            lots = int(abs(data["oi"]) / lot)

            if lots >= (50 if "BANKNIFTY" in key else 100):
                price_change = data["end"] - data["start"]
                if data["oi"] > 0 and price_change > 0: inst = "BUY"
                elif data["oi"] > 0 and price_change < 0: inst = "WRITER"
                elif data["oi"] < 0 and price_change > 0: inst = "SC"
                else: inst = "UW"

                alerts.append(format_alert(key, lots, data["end"], fut_price, prev, data["oi"], oi, inst, row["instrument_type"]))
            
            accumulator[key] = {"oi":0,"start":price,"end":price}
            timer_store[key] = now

def process_future(name, symbol, data, alerts):
    oi, price = data.get("oi", 0), data.get("last_price", 0)
    prev = last_oi_store.get(symbol, 0)
    change = oi - prev
    last_oi_store[symbol] = oi
    
    lot = LOT_SIZES["BANKNIFTY"] if "BANKNIFTY" in symbol else LOT_SIZES[name]
    lots = int(abs(change) / lot)

    if lots >= 100:
        alerts.append(format_future(symbol, lots, price, prev, change, oi))

# ================= MAIN =================

def calculate_heatmap(kite):
    """Generates the General Report and fetches Alerts"""
    alerts = []
    report_lines = ["📊 *BANKNIFTY HEATMAP*"]
    total_score = 0

    for name in BANK_NAMES + ["BANKNIFTY"]:
        try:
            fut = get_future(name)
            data = kite.quote([fut])[fut]
            ltp = data["last_price"]
            change_pct = ((ltp - data['ohlc']['close']) / data['ohlc']['close']) * 100
            total_score += change_pct

            # Formatting line for General Report
            icon = "🟢" if change_pct > 0 else "🔴"
            report_lines.append(f"{icon} *{name}*: `{ltp:.2f}` ({change_pct:+.2f}%)")

            opt_df = get_options(name, ltp)
            tokens = opt_df['instrument_token'].tolist()
            quotes = kite.quote(tokens)

            process_options(name, opt_df, quotes, alerts, ltp)
            process_future(name, fut, data, alerts)
        except:
            continue

    report = "\n".join(report_lines)
    # The scanner expects 5 return values: score, report, bn_alerts, stock_alerts, velocity_alerts
    # We split alerts by symbol for the scanner
    bn_al = [a for a in alerts if "BANKNIFTY" in a]
    st_al = [a for a in alerts if "BANKNIFTY" not in a]
    
    return total_score, report, bn_al, st_al, []
