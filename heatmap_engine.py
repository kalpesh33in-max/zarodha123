import pandas as pd
from datetime import datetime, timedelta

# ================= CONFIG =================

BANK_WEIGHTS = {
    "HDFCBANK": 19.7,
    "ICICIBANK": 16.1,
    "SBIN": 10.7,
    "AXISBANK": 9.9,
    "KOTAKBANK": 9.2,
}

LOT_SIZES = {
    "HDFCBANK": 550,
    "ICICIBANK": 700,
    "SBIN": 750,
    "AXISBANK": 625,
    "KOTAKBANK": 2000,
    "BANKNIFTY": 30
}

BANK_NAMES = list(BANK_WEIGHTS.keys())
INDEX_SYMBOL = "NSE:NIFTY BANK"

last_oi_store = {}
option_history = {}
accum_history = {}
price_velocity_store = {}

_options_df = None
_futures_df = None


# ================= LOAD DATA =================

def load_options_data():
    global _options_df
    if _options_df is None:
        df = pd.read_csv("instruments.csv")
        _options_df = df[df['segment'].isin(['NFO-OPT'])].copy()
        _options_df['expiry'] = pd.to_datetime(_options_df['expiry'], dayfirst=True)
    return _options_df


def load_futures_data():
    global _futures_df
    if _futures_df is None:
        df = pd.read_csv("instruments.csv")
        _futures_df = df[df['segment'].str.contains('-FUT', na=False)].copy()
        _futures_df['expiry'] = pd.to_datetime(_futures_df['expiry'], dayfirst=True)
    return _futures_df


def get_active_future(name):
    df = load_futures_data()
    futures = df[df['name'] == name]
    nearest_expiry = futures['expiry'].min()
    return "NFO:" + futures[futures['expiry'] == nearest_expiry].iloc[0]['tradingsymbol']


def get_bank_futures(kite):
    return [get_active_future(name) for name in BANK_NAMES]


def get_relevant_options(name, ltp):
    df = load_options_data()
    options = df[df['name'] == name]

    expiry = sorted(options['expiry'].unique())[0]
    options = options[options['expiry'] == expiry]

    strikes = sorted(options['strike'].unique())
    atm = min(strikes, key=lambda x: abs(x - ltp))

    idx = strikes.index(atm)
    rng = 15 if name == "BANKNIFTY" else 10

    selected = strikes[max(0, idx - rng): idx + rng]

    return options[options['strike'].isin(selected)]


# ================= EXISTING LOGIC (UNCHANGED) =================

def process_quiet_accumulation(name, ltp, oi, alerts):
    if name not in accum_history:
        accum_history[name] = []
    history = accum_history[name]
    history.append((ltp, oi))
    if len(history) > 20:
        history.pop(0)

    if len(history) == 20:
        oi_change = oi - history[0][1]
        prices = [x[0] for x in history]
        if oi_change > 5000 and max(prices) - min(prices) < 0.2:
            alerts.append(f"🤫 {name} Whale Entering...")


def classify_action(symbol, oi_change, price_change):
    is_call = symbol.endswith("CE")
    if oi_change > 0:
        if price_change >= 0:
            return "CALL BUY 🔵" if is_call else "PUT BUY 🔴"
        else:
            return "CALL WRITER ✍️" if is_call else "PUT WRITER ✍️"
    else:
        return "SHORT COVERING"


def process_option_logic(name, underlying_data, option_quotes, alerts):
    opt_df, u_ltp = underlying_data
    lot_size = LOT_SIZES.get(name, 1)

    for _, row in opt_df.iterrows():
        token = str(int(row['instrument_token']))
        if token not in option_quotes:
            continue

        curr_oi = option_quotes[token].get('oi', 0)
        curr_price = option_quotes[token].get('last_price', 0)

        prev = option_history.get(token, 0)
        change = curr_oi - prev
        option_history[token] = curr_oi

        lots = int(abs(change) / lot_size)

        if lots >= 100:
            alerts.append(
                f"🚀 BLAST 🚀\n🚨 {classify_action(row['tradingsymbol'], change, curr_price)}\n"
                f"Symbol: {row['tradingsymbol']}\nLOTS: {lots}"
            )

    return 1.0


# ================= MAIN =================

def calculate_heatmap(kite):

    fut_symbols = get_bank_futures(kite)
    symbols = fut_symbols + [INDEX_SYMBOL]

    data = kite.quote(symbols)

    score = 0
    report = "📊 *BANK MOVEMENT (FUTURES)*\n\n"

    short = {
        "HDFCBANK": "HDBFU",
        "ICICIBANK": "ICIBFU",
        "SBIN": "SBINFU",
        "AXISBANK": "AXISFU",
        "BANKNIFTY": "BANKNIFTY"
    }

    bn_alerts = []
    stock_alerts = []
    velocity_alerts = []
    accumulation_alerts = []

    bank_signals = {}

    for sym in fut_symbols:

        d = data[sym]
        ltp = d["last_price"]
        open_p = d["ohlc"]["open"]
        oi = d.get("oi", 0)

        change = ((ltp - open_p) / open_p) * 100
        name = next(n for n in BANK_NAMES if n in sym)

        score += change * BANK_WEIGHTS[name]
        bank_signals[name] = "BUY" if change > 0.3 else "SELL"

        process_quiet_accumulation(short[name], ltp, oi, accumulation_alerts)

        opt_df = get_relevant_options(name, ltp)
        tokens = opt_df['instrument_token'].tolist()
        option_quotes = kite.quote(tokens)

        # KEEP ORIGINAL ALERT LOGIC
        process_option_logic(name, (opt_df, ltp), option_quotes, stock_alerts)

        # NEW: MAX + CHANGE OI
        max_call = max_put = chg_call = chg_put = 0
        max_call_oi = max_put_oi = chg_call_oi = chg_put_oi = 0
        total_call = total_put = 0

        for _, row in opt_df.iterrows():
            t = str(int(row['instrument_token']))
            if t not in option_quotes:
                continue

            oi_val = option_quotes[t].get("oi", 0)
            prev = option_history.get(t, 0)
            diff = oi_val - prev

            if row['instrument_type'] == "CE":
                total_call += oi_val
                if oi_val > max_call_oi:
                    max_call_oi = oi_val
                    max_call = row['strike']
                if diff > chg_call_oi:
                    chg_call_oi = diff
                    chg_call = row['strike']
            else:
                total_put += oi_val
                if oi_val > max_put_oi:
                    max_put_oi = oi_val
                    max_put = row['strike']
                if diff > chg_put_oi:
                    chg_put_oi = diff
                    chg_put = row['strike']

        pcr = total_put / total_call if total_call else 1

        arrow = "⬆️" if change > 0 else "⬇️"
        icon = "🛡️" if pcr > 1.3 else "🧱" if pcr < 0.7 else ""

        report += f"{short[name]}={ltp:.1f}{arrow}{icon} , PCR-{pcr:.1f}\n"
        report += f"    - MAX_OI: {max_put}P/{max_call}C | CHG_OI: {chg_put}P/{chg_call}C\n\n"

    report += f"⚖️ *SENTIMENT SCORE: {score:.2f}*\n"
    report += "🚀 STRONG BUY" if score > 30 else "📉 STRONG SELL" if score < -30 else "⚖️ SIDEWAYS"

    report += "\n\n🔔 *LATEST ALERTS:*"
    for alert in (bn_alerts + stock_alerts + velocity_alerts)[:5]:
        report += f"\n• {alert.splitlines()[1] if '\\n' in alert else alert}"

    return score, report, bn_alerts, stock_alerts, velocity_alerts
