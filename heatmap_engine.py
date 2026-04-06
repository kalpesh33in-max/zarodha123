import pandas as pd
from datetime import datetime, timedelta

BANK_WEIGHTS = {
    "HDFCBANK": 19.7,
    "ICICIBANK": 16.1,
    "SBIN": 10.7,
    "AXISBANK": 9.9,
    "KOTAKBANK": 9.2
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
active_watches = {}

_options_df = None


# -------------------------------
# LOAD OPTION DATA
# -------------------------------
def load_options_data():
    global _options_df
    if _options_df is None:
        df = pd.read_csv("instruments.csv")
        df = df[df['segment'] == 'NFO-OPT']
        df['expiry'] = pd.to_datetime(df['expiry'])
        _options_df = df
    return _options_df


# -------------------------------
# MONTHLY EXPIRY ONLY
# -------------------------------
def get_relevant_options(name, ltp):

    df = load_options_data()
    options = df[df['name'] == name]

    expiries = sorted(options['expiry'].unique())

    # ✅ MONTHLY EXPIRY (LAST)
    monthly_expiry = expiries[-1]

    options = options[options['expiry'] == monthly_expiry]

    strikes = sorted(options['strike'].unique())

    atm = min(strikes, key=lambda x: abs(x - ltp))
    idx = strikes.index(atm)

    rng = 15 if name == "BANKNIFTY" else 10

    strikes = strikes[max(0, idx-rng):idx+rng]

    return options[options['strike'].isin(strikes)]


# -------------------------------
# ALERT LOGIC
# -------------------------------
def classify_action(symbol, oi_change, price_change):

    is_call = symbol.endswith("CE")

    if oi_change > 0:
        if price_change >= 0:
            return "CALL BUY 🔵" if is_call else "PUT BUY 🔴"
        else:
            return "CALL WRITER ✍️" if is_call else "PUT WRITER ✍️"
    else:
        if price_change >= 0:
            return "SHORT COVERING ⤴️"
        else:
            return "LONG UNWINDING ⤵️"


def strength(lots):
    if lots >= 400: return "🚀 BLAST 🚀"
    elif lots >= 300: return "🌟 AWESOME"
    elif lots >= 200: return "✅ VERY GOOD"
    else: return "⚡ GOOD"


# -------------------------------
# MAIN FUNCTION
# -------------------------------
def calculate_heatmap(kite):

    symbols = [f"NFO:{x}FUT" for x in BANK_NAMES] + [INDEX_SYMBOL]

    data = kite.quote(symbols)

    score = 0
    report = "📊 *BANK MOVEMENT (FUTURES)*\n\n"

    bn_alerts = []
    stock_alerts = []
    velocity_alerts = []

    # ---------------- FUTURE LOOP ----------------
    for s in symbols:

        if s not in data:
            continue

        d = data[s]

        ltp = d["last_price"]
        open_p = d["ohlc"]["open"]
        oi = d.get("oi", 0)

        change = ((ltp - open_p) / open_p) * 100 if open_p else 0

        name = "BANKNIFTY" if s == INDEX_SYMBOL else next((x for x in BANK_NAMES if x in s), "")

        score += (change/100) * BANK_WEIGHTS.get(name, 0) * 100

        prev_oi = last_oi_store.get(name, oi)
        oi_change = oi - prev_oi
        last_oi_store[name] = oi

        lots = int(abs(oi_change) / LOT_SIZES.get(name, 1))

        oi_icon = "⬆️" if oi_change >= 0 else "⬇️"

        report += f"{name}={ltp} , COP%={change:+.2f}% , OI{oi_icon}={lots}LOT\n"

        # FUTURE BURST ALERT
        if lots >= 100:

            action = "FUTURE BUY 📈" if change > 0 else "FUTURE SELL 📉"

            velocity_alerts.append(
                f"{strength(lots)}\n🚨 {action}\nSymbol: {s}\n━━━━━━━━━━━━━━━\n"
                f"LOTS: {lots}\nPRICE: {ltp:.2f}\n━━━━━━━━━━━━━━━\n"
                f"OI CHANGE: {oi_change:+}\nTIME: {datetime.now().strftime('%H:%M:%S')}"
            )

    # ---------------- OPTION ALERTS ----------------
    bn_price = data[INDEX_SYMBOL]["last_price"]

    opt_df = get_relevant_options("BANKNIFTY", bn_price)

    tokens = opt_df['instrument_token'].tolist()

    quotes = kite.quote(tokens)

    for _, row in opt_df.iterrows():

        t = str(int(row['instrument_token']))

        if t not in quotes:
            continue

        q = quotes[t]

        oi = q.get("oi", 0)
        price = q.get("last_price", 0)

        prev = option_history.get(t, {"oi": oi, "price": price})

        oi_change = oi - prev["oi"]
        price_change = price - prev["price"]

        option_history[t] = {"oi": oi, "price": price}

        lots = int(abs(oi_change) / LOT_SIZES["BANKNIFTY"])

        if lots >= 100:

            symbol = row['tradingsymbol']

            action = classify_action(symbol, oi_change, price_change)

            bn_alerts.append(
                f"{strength(lots)}\n🚨 {action}\nSymbol: {symbol}\n━━━━━━━━━━━━━━━\n"
                f"LOTS: {lots}\nPRICE: {price:.2f}\nFUTURE PRICE: {bn_price:.2f}\n"
                f"━━━━━━━━━━━━━━━\nEXISTING OI: {prev['oi']:,}\n"
                f"OI CHANGE  : {oi_change:+,}\nNEW OI     : {oi:,}\n"
                f"TIME: {datetime.now().strftime('%H:%M:%S')}"
            )

    # ---------------- FINAL ----------------
    report += f"\n⚖️ *SENTIMENT SCORE*: {score:.2f}\n"

    if score > 30:
        report += "🚀 *STRONG BULLISH*"
    elif score < -30:
        report += "📉 *STRONG BEARISH*"
    else:
        report += "⚖️ *SIDEWAYS*"

    return score, report, bn_alerts, stock_alerts, velocity_alerts
