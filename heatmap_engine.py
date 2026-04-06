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

    if name == "BANKNIFTY":
        opt = df[df['name'].str.contains("BANKNIFTY", na=False)]
    else:
        opt = df[df['name'] == name]

    exp = get_expiry(opt)
    opt = opt[opt['expiry']==exp]

    strikes = sorted(opt['strike'].unique())
    atm = min(strikes, key=lambda x: abs(x-ltp))
    idx = strikes.index(atm)

    return opt.iloc[max(0,idx-10):idx+10]


# ================= FORMAT =================
def format_alert(symbol, lots, price, fut, prev, change, curr, inst, opt_type):

    if lots >= 350:
        tag = "🌟 AWESOME"
    elif lots >= 200:
        tag = "✅ VERY GOOD"
    else:
        tag = "⚡ GOOD"

    if inst == "BUY":
        action = "CALL BUY 🔵" if opt_type == "CE" else "PUT BUY 🔴"
    elif inst == "WRITER":
        action = "CALL WRITER ✍️" if opt_type == "CE" else "PUT WRITER ✍️"
    elif inst == "SC":
        action = f"SHORT COVERING ({opt_type}) 🔥"
    else:
        action = f"LONG UNWINDING ({opt_type}) ⚠️"

    arrow = "▲" if change >= 0 else "▼"

    return f"""{tag}
🚨 {action}
Symbol: {symbol}
━━━━━━━━━━━━━━━
LOTS: {lots}
PRICE: {price:.2f} ({arrow})
FUTURE PRICE: {fut:.2f}
━━━━━━━━━━━━━━━
EXISTING OI: {prev:,}
OI CHANGE : {change:+,}
NEW OI : {curr:,}
TIME: {datetime.now().strftime('%H:%M:%S')}
"""


def format_future(symbol, lots, price, prev, change, curr):

    if lots >= 350:
        tag = "🌟 AWESOME"
    elif lots >= 200:
        tag = "✅ VERY GOOD"
    else:
        tag = "⚡ GOOD"

    action = "FUTURE BUY 📈" if change > 0 else "FUTURE SELL 📉"
    arrow = "▲" if change > 0 else "▼"

    return f"""{tag}
🚨 {action}
Symbol: {symbol}
━━━━━━━━━━━━━━━
LOTS: {lots}
PRICE: {price:.2f} ({arrow})
FUTURE PRICE: {price:.2f}
━━━━━━━━━━━━━━━
EXISTING OI: {prev:,}
OI CHANGE : {change:+,}
NEW OI : {curr:,}
TIME: {datetime.now().strftime('%H:%M:%S')}
"""


# ================= CORE =================
def process_options(name, opt_df, quotes, alerts, fut_price):

    now = time.time()

    for _, row in opt_df.iterrows():

        token = str(int(row['instrument_token']))
        if token not in quotes:
            continue

        q = quotes[token]

        oi = q.get("oi", 0)
        price = q.get("last_price", 0)

        prev = option_history.get(token, 0)
        change = oi - prev
        option_history[token] = oi

        key = row['tradingsymbol']

        if key not in accumulator:
            accumulator[key] = {"oi":0,"start":price,"end":price}
            timer_store[key] = now

        accumulator[key]["oi"] += change
        accumulator[key]["end"] = price

        if now - timer_store[key] < 60:
            continue

        data = accumulator[key]

        lot = LOT_SIZES["BANKNIFTY"] if "BANKNIFTY" in key else LOT_SIZES[name]
        lots = int(abs(data["oi"]) / lot)

        min_lots = 50 if "BANKNIFTY" in key else 100

        if lots < min_lots:
            accumulator[key] = {"oi":0,"start":price,"end":price}
            timer_store[key] = now
            continue

        price_change = data["end"] - data["start"]

        if data["oi"] > 0 and price_change > 0:
            inst = "BUY"
        elif data["oi"] > 0 and price_change < 0:
            inst = "WRITER"
        elif data["oi"] < 0 and price_change > 0:
            inst = "SC"
        else:
            inst = "UW"

        alert = format_alert(
            key, lots, data["end"], fut_price,
            prev, data["oi"], oi, inst, row["instrument_type"]
        )

        alerts.append(alert)

        accumulator[key] = {"oi":0,"start":price,"end":price}
        timer_store[key] = now


def process_future(name, symbol, data, alerts):

    oi = data.get("oi", 0)
    price = data.get("last_price", 0)

    prev = last_oi_store.get(symbol, 0)
    change = oi - prev
    last_oi_store[symbol] = oi

    lot = LOT_SIZES["BANKNIFTY"] if "BANKNIFTY" in symbol else LOT_SIZES[name]
    lots = int(abs(change) / lot)

    if lots < 100:
        return

    alert = format_future(symbol, lots, price, prev, change, oi)
    alerts.append(alert)


# ================= MAIN =================
def run_scanner(kite):

    alerts = []

    for name in BANK_NAMES + ["BANKNIFTY"]:

        fut = get_future(name)
        data = kite.quote([fut])[fut]

        ltp = data["last_price"]

        opt_df = get_options(name, ltp)
        tokens = opt_df['instrument_token'].tolist()

        quotes = kite.quote(tokens)

        process_options(name, opt_df, quotes, alerts, ltp)
        process_future(name, fut, data, alerts)

    return alerts


# 🔥 BACKWARD COMPATIBILITY (CRASH FIX)
def calculate_heatmap(kite):
    return run_scanner(kite)
