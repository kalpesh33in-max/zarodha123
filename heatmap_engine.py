import pandas as pd
from datetime import datetime, timedelta

BANK_WEIGHTS = {
    "HDFCBANK": 19.7,
    "ICICIBANK": 16.1,
    "SBIN": 10.7,
    "AXISBANK": 9.9,
    "KOTAKBANK": 9.2,
    "FEDERALBNK": 5.6,
    "INDUSINDBK": 4.7,
    "BANKBARODA": 4.5,
    "AUBANK": 4.0,
    "CANBK": 3.9,
    "PNB": 3.5,
    "IDFCFIRSTB": 3.2,
    "YESBANK": 2.5,
    "UNIONBANK": 2.5
}

LOT_SIZES = {
    "HDFCBANK": 550,
    "ICICIBANK": 700,
    "SBIN": 750,
    "AXISBANK": 625,
    "KOTAKBANK": 2000,
    "FEDERALBNK": 5000,
    "INDUSINDBK": 500,
    "BANKBARODA": 4850,
    "AUBANK": 1000,
    "CANBK": 2250,
    "PNB": 4000,
    "IDFCFIRSTB": 7500,
    "YESBANK": 8000,
    "UNIONBANK": 5000,
    "BANKNIFTY": 30
}

BANK_NAMES = list(BANK_WEIGHTS.keys())
INDEX_SYMBOL = "NSE:NIFTY BANK"

last_oi_store = {}
option_history = {}
active_watches = {}

_options_df = None
_futures_df = None


# --------------------------------------------------------
# LOAD DATA
# --------------------------------------------------------

def load_options_data():
    global _options_df

    if _options_df is None:
        df = pd.read_csv("instruments.csv")
        _options_df = df[df['segment'] == "NFO-OPT"].copy()
        _options_df['expiry'] = pd.to_datetime(_options_df['expiry'], dayfirst=True)

    return _options_df


def load_futures_data():
    global _futures_df

    if _futures_df is None:
        df = pd.read_csv("instruments.csv")
        _futures_df = df[df['segment'].str.contains("-FUT")].copy()
        _futures_df['expiry'] = pd.to_datetime(_futures_df['expiry'], dayfirst=True)

    return _futures_df


# --------------------------------------------------------
# FUTURE SYMBOL FINDER
# --------------------------------------------------------

def get_active_future(name, segment, exchange):

    df = load_futures_data()

    futures = df[(df["name"] == name) & (df["segment"] == segment)]

    if futures.empty:
        return None

    nearest = futures["expiry"].min()

    active = futures[futures["expiry"] == nearest]

    return f"{exchange}:{active.iloc[0]['tradingsymbol']}"


def get_bank_futures(kite):

    symbols = []

    for name in BANK_NAMES:

        sym = get_active_future(name, "NFO-FUT", "NFO")

        if sym:
            symbols.append(sym)

    return symbols


# --------------------------------------------------------
# ALERT LABELS
# --------------------------------------------------------

def get_strength_label(lots):

    if lots >= 400:
        return "🚀 BLAST 🚀"
    elif lots >= 300:
        return "☀️ AWESOME"
    elif lots >= 200:
        return "✅ VERY GOOD"
    elif lots >= 100:
        return "⚡ GOOD"
    else:
        return ""


# --------------------------------------------------------
# ACTION CLASSIFIER
# --------------------------------------------------------

def classify_action(symbol, oi_change, price_change):

    if "FUT" in symbol:

        if oi_change > 0:
            return "FUTURE BUY (LONG) 📈" if price_change >= 0 else "FUTURE SELL (SHORT) 📉"
        else:
            return "SHORT COVERING ↗️" if price_change >= 0 else "LONG UNWINDING ↘️"

    is_call = symbol.endswith("CE")

    if oi_change > 0:

        if price_change >= 0:
            return "CALL BUY 🔵" if is_call else "PUT BUY 🔴"
        else:
            return "CALL WRITER ✍️" if is_call else "PUT WRITER ✍️"

    else:

        if price_change >= 0:
            return "SHORT COVERING (CE) ⤴️" if is_call else "SHORT COVERING (PE) ⤴️"
        else:
            return "LONG UNWINDING (CE) ⤵️" if is_call else "LONG UNWINDING (PE) ⤵️"


# --------------------------------------------------------
# FUTURE BURST DETECTOR
# --------------------------------------------------------

def process_future_burst(symbol, name, ltp, oi, alerts_list):

    lot_size = LOT_SIZES.get(name, 1)
    threshold = 100
    now = datetime.now()

    key = f"FUT_{symbol}"

    if key not in option_history:
        option_history[key] = []

    history = option_history[key]

    prev_oi = history[-1]["oi"] if history else 0
    prev_price = history[-1]["price"] if history else 0

    if prev_oi > 0:

        lots = int(abs(oi - prev_oi) / lot_size)

        if lots >= threshold:

            strength = get_strength_label(lots)

            action = classify_action(symbol, oi - prev_oi, ltp - prev_price)

            alerts_list.append(
                f"{strength}\n"
                f"{action}\n"
                f"Symbol: {symbol}\n"
                f"━━━━━━━━━━━━━━━\n"
                f"LOTS: {lots}\n"
                f"PRICE: {ltp}\n"
                f"OI CHANGE: {oi - prev_oi}\n"
                f"NEW OI: {oi}"
            )

    history.append({"time": now, "oi": oi, "price": ltp})

    if len(history) > 20:
        history.pop(0)


# --------------------------------------------------------
# OPTION BURST DETECTOR
# --------------------------------------------------------

def process_option_logic(name, underlying_data, option_quotes, alerts):

    opt_df, future_price = underlying_data

    if opt_df.empty:
        return 1

    total_call = 0
    total_put = 0

    for _, row in opt_df.iterrows():

        token = str(int(row["instrument_token"]))

        if token not in option_quotes:
            continue

        q = option_quotes[token]

        oi = q.get("oi", 0)
        price = q.get("last_price", 0)

        if row["instrument_type"] == "CE":
            total_call += oi
        else:
            total_put += oi

        if token not in option_history:
            option_history[token] = []

        history = option_history[token]

        prev_oi = history[-1]["oi"] if history else 0
        prev_price = history[-1]["price"] if history else 0

        if prev_oi > 0:

            lots = int(abs(oi - prev_oi) / LOT_SIZES.get(name, 1))

            if lots >= 100:

                strength = get_strength_label(lots)

                action = classify_action(row["tradingsymbol"], oi - prev_oi, price - prev_price)

                alerts.append(
                    f"{strength}\n"
                    f"{action}\n"
                    f"Symbol: {row['tradingsymbol']}\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"LOTS: {lots}\n"
                    f"PRICE: {price}\n"
                    f"FUTURE: {future_price}\n"
                    f"OI CHANGE: {oi - prev_oi}\n"
                    f"NEW OI: {oi}"
                )

        history.append({"time": datetime.now(), "oi": oi, "price": price})

        if len(history) > 20:
            history.pop(0)

    return total_put / total_call if total_call else 1


# --------------------------------------------------------
# MAIN HEATMAP FUNCTION (REQUIRED BY scanner.py)
# --------------------------------------------------------

def calculate_heatmap(kite):

    futures = get_bank_futures(kite)

    data = kite.quote(futures + [INDEX_SYMBOL])

    score = 0

    report = "📊 BANK MOVEMENT (FUTURES)\n\n"

    bn_alerts = []
    stock_alerts = []

    for s in futures:

        d = data[s]

        ltp = d["last_price"]
        open_p = d["ohlc"]["open"]
        oi = d.get("oi", 0)

        change = ((ltp - open_p) / open_p) * 100 if open_p else 0

        name = next((x for x in BANK_NAMES if x in s), "UNKNOWN")

        weighted = (change / 100) * BANK_WEIGHTS.get(name, 0)

        score += weighted * 100

        process_future_burst(s, name, ltp, oi, stock_alerts)

        report += f"{name}={ltp} , COP%={change:.2f}% , OI={oi}\n"

    report += f"\n⚖️ SENTIMENT SCORE: {score:.2f}"

    return score, report, bn_alerts, stock_alerts
