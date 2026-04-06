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
price_velocity_store = {}

_options_df = None
_futures_df = None


# -------------------------------
# LOAD DATA (SAFE DATE FIX)
# -------------------------------
def load_options_data():
    global _options_df
    if _options_df is None:
        df = pd.read_csv("instruments.csv")
        _options_df = df[df['segment'] == 'NFO-OPT'].copy()
        _options_df['expiry'] = pd.to_datetime(
            _options_df['expiry'],
            dayfirst=True,
            errors='coerce'
        )
        _options_df = _options_df.dropna(subset=['expiry'])
    return _options_df


def load_futures_data():
    global _futures_df
    if _futures_df is None:
        df = pd.read_csv("instruments.csv")
        _futures_df = df[df['segment'].str.contains('-FUT', na=False)].copy()
        _futures_df['expiry'] = pd.to_datetime(
            _futures_df['expiry'],
            dayfirst=True,
            errors='coerce'
        )
        _futures_df = _futures_df.dropna(subset=['expiry'])
    return _futures_df


# -------------------------------
# 🔥 FIXED: MONTHLY EXPIRY ONLY
# -------------------------------
def get_relevant_options(underlying_name, ltp):

    df = load_options_data()
    if df is None or df.empty:
        return pd.DataFrame()

    options = df[df['name'] == underlying_name]
    if options.empty:
        return pd.DataFrame()

    expiries = sorted(options['expiry'].unique())

    # ✅ FIX: ALWAYS MONTHLY (LAST EXPIRY)
    monthly_expiry = expiries[-1]

    options = options[options['expiry'] == monthly_expiry]

    strikes = sorted(options['strike'].unique())
    if not strikes:
        return pd.DataFrame()

    atm = min(strikes, key=lambda x: abs(x - ltp))
    idx = strikes.index(atm)

    range_size = 15 if underlying_name == "BANKNIFTY" else 10
    strikes = strikes[max(0, idx-range_size):idx+range_size]

    return options[options['strike'].isin(strikes)]


# -------------------------------
# REMAINING LOGIC SAME (NO CHANGE)
# -------------------------------
def get_strength_label(lots):
    if lots >= 400: return "🚀 BLAST 🚀"
    elif lots >= 300: return "🌟 AWESOME"
    elif lots >= 200: return "✅ VERY GOOD"
    else: return "⚡ GOOD"


def classify_action(symbol, oi_change, price_change):
    if any(x in symbol for x in ["-FUT", "FUT"]):
        if oi_change > 0:
            return "FUTURE BUY 📈" if price_change >= 0 else "FUTURE SELL 📉"
        else:
            return "SHORT COVERING ⤴️" if price_change >= 0 else "LONG UNWINDING ⤵️"

    is_call = symbol.endswith("CE")
    if oi_change > 0:
        if price_change >= 0:
            return "CALL BUY 🔵" if is_call else "PUT BUY 🔴"
        else:
            return "CALL WRITER ✍️" if is_call else "PUT WRITER ✍️"
    else:
        if price_change >= 0:
            return "SHORT COVERING (CE)" if is_call else "SHORT COVERING (PE)"
        else:
            return "LONG UNWINDING (CE)" if is_call else "LONG UNWINDING (PE)"


# -------------------------------
# MAIN ENGINE
# -------------------------------
def calculate_heatmap(kite):

    try:
        data = kite.quote([INDEX_SYMBOL])
        bn_price = data[INDEX_SYMBOL]["last_price"]

        report = f"📊 BANKNIFTY={bn_price}\n"

        bn_alerts = []

        opt_df = get_relevant_options("BANKNIFTY", bn_price)

        tokens = opt_df['instrument_token'].tolist()
        quotes = kite.quote(tokens)

        for _, row in opt_df.iterrows():

            token = str(int(row['instrument_token']))

            if token not in quotes:
                continue

            q = quotes[token]

            oi = q.get("oi", 0)
            price = q.get("last_price", 0)

            prev = option_history.get(token, {"oi": oi, "price": price})

            oi_chg = oi - prev["oi"]
            price_chg = price - prev["price"]

            option_history[token] = {"oi": oi, "price": price}

            lots = int(abs(oi_chg) / LOT_SIZES["BANKNIFTY"])

            if lots >= 100:

                symbol = row['tradingsymbol']
                action = classify_action(symbol, oi_chg, price_chg)
                price_icon = "▲" if price_chg >= 0 else "▼"

                bn_alerts.append(
                    f"{get_strength_label(lots)}\n"
                    f"🚨 {action}\n"
                    f"Symbol: {symbol}\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"LOTS: {lots}\n"
                    f"PRICE: {price:.2f} ({price_icon})\n"
                    f"FUTURE PRICE: {bn_price:.2f}\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"EXISTING OI: {prev['oi']:,}\n"
                    f"OI CHANGE  : {oi_chg:+,}\n"
                    f"NEW OI     : {oi:,}\n"
                    f"TIME: {datetime.now().strftime('%H:%M:%S')}"
                )

        return 0, report, bn_alerts, [], []

    except Exception as e:
        return 0, f"Error: {e}", [], [], []
