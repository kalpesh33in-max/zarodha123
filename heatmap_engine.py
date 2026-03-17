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

def load_options_data():
    global _options_df
    if _options_df is None:
        try:
            df = pd.read_csv("instruments.csv")
            _options_df = df[df['segment'].isin(['NFO-OPT'])].copy()
            _options_df['expiry'] = pd.to_datetime(_options_df['expiry'], dayfirst=True)
        except Exception as e:
            print(f"Error loading Options: {e}")
    return _options_df

def load_futures_data():
    global _futures_df
    if _futures_df is None:
        try:
            df = pd.read_csv("instruments.csv")
            _futures_df = df[df['segment'].str.contains('-FUT', na=False)].copy()
            _futures_df['expiry'] = pd.to_datetime(_futures_df['expiry'], dayfirst=True)
        except Exception as e:
            print(f"Error loading Futures: {e}")
    return _futures_df

def get_active_future(name, segment, exchange):
    df = load_futures_data()
    if df is None or df.empty: return None
    futures = df[(df['name'] == name) & (df['segment'] == segment)]
    if futures.empty: return None
    nearest_expiry = futures['expiry'].min()
    active_contract = futures[futures['expiry'] == nearest_expiry]
    if not active_contract.empty:
        return f"{exchange}:" + active_contract.iloc[0]['tradingsymbol']
    return None

def get_bank_futures(kite):
    symbols = []
    for name in BANK_NAMES:
        sym = get_active_future(name, 'NFO-FUT', 'NFO')
        if sym:
            symbols.append(sym)
        else:
            now = datetime.now()
            month_str = now.strftime("%b").upper()
            year_str = now.strftime("%y")
            symbols.append(f"NFO:{name}{year_str}{month_str}FUT")
    return symbols

def get_strength_label(lots):
    if lots >= 400: return "🚀 BLAST 🚀"
    elif lots >= 300: return "🌟 AWESOME"
    elif lots >= 200: return "✅ VERY GOOD"
    else: return "⚡ GOOD"

def classify_action(symbol, oi_change, price_change):
    if any(x in symbol for x in ["-FUT", "FUT", "-I"]):
        if oi_change > 0:
            return "FUTURE BUY (LONG) 📈" if price_change >= 0 else "FUTURE SELL (SHORT) 📉"
        else:
            return "SHORT COVERING ↗️" if price_change >= 0 else "LONG UNWINDING ↘️"

def calculate_heatmap(kite):
    fut_symbols = get_bank_futures(kite)
    all_symbols = fut_symbols + [INDEX_SYMBOL]

    data = kite.quote(all_symbols)

    score = 0
    report = "📊 *BANK MOVEMENT (FUTURES)*\n\n"

    bn_alerts = []
    stock_alerts = []
    velocity_alerts = []

    for s in fut_symbols:
        if s not in data: continue
        d = data[s]
        ltp = d["last_price"]
        oi = d.get("oi", 0)

        name = next((n for n in BANK_NAMES if n in s), "UNKNOWN")

        process_future_burst(s, name, ltp, oi, stock_alerts)

    return score, report, bn_alerts, stock_alerts, velocity_alerts

def process_future_burst(symbol, name, ltp, oi, alerts_list):

    lot_size = LOT_SIZES.get(name, 1)

    # ✅ ONLY CHANGE APPLIED HERE
    threshold = 500

    now = datetime.now()

    key = f"FUT_{symbol}"

    if key not in option_history:
        option_history[key] = []

    history = option_history[key]

    prev_oi = history[-1]['oi'] if history else 0
    prev_price = history[-1]['price'] if history else 0

    if prev_oi > 0:
        tick_lots = int(abs(oi - prev_oi) / lot_size)

        if tick_lots >= threshold and key not in active_watches:
            active_watches[key] = {
                "start_oi": prev_oi,
                "start_price": prev_price,
                "end_time": now + timedelta(minutes=1),
                "symbol": symbol,
                "name": name
            }

    if key in active_watches:
        watch = active_watches[key]

        if now >= watch["end_time"]:

            final_oi_chg = oi - watch["start_oi"]
            final_price_chg = ltp - watch["start_price"]
            final_lots = int(abs(final_oi_chg) / lot_size)

            if final_lots >= threshold:

                strength = get_strength_label(final_lots)
                action = classify_action(watch['symbol'], final_oi_chg, final_price_chg)
                price_icon = "▲" if final_price_chg >= 0 else "▼"

                alerts_list.append(
                    f"{strength}\n🚨 {action}\nSymbol: {watch['symbol']}\n━━━━━━━━━━━━━━━\nLOTS: {final_lots}\n"
                    f"PRICE: {ltp:.2f} ({price_icon})\nFUTURE PRICE: {ltp:.2f}\n"
                    f"━━━━━━━━━━━━━━━\nEXISTING OI: {watch['start_oi']:,}\nOI CHANGE  : {final_oi_chg:+,d}\nNEW OI     : {oi:,}\nTIME: {now.strftime('%H:%M:%S')}"
                )

            del active_watches[key]

    history.append({'time': now, 'oi': oi, 'price': ltp})
    if len(history) > 20:
        history.pop(0)
