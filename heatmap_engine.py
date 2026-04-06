import pandas as pd
from datetime import datetime, timedelta
import time

# ================= CONFIG (STAYS THE SAME) =================
BANK_WEIGHTS = {
    "HDFCBANK": 19.7, "ICICIBANK": 16.1, "SBIN": 10.7, "AXISBANK": 9.9,
    "KOTAKBANK": 9.2, "FEDERALBNK": 5.6, "INDUSINDBK": 4.7, "BANKBARODA": 4.5,
    "AUBANK": 4.0, "CANBK": 3.9, "PNB": 3.5, "IDFCFIRSTB": 3.2, "YESBANK": 2.5, "UNIONBANK": 2.5
}

LOT_SIZES = {
    "HDFCBANK": 550, "ICICIBANK": 700, "SBIN": 750, "AXISBANK": 625, "KOTAKBANK": 2000,
    "FEDERALBNK": 5000, "INDUSINDBK": 500, "BANKBARODA": 4850, "AUBANK": 1000,
    "CANBK": 2250, "PNB": 4000, "IDFCFIRSTB": 7500, "YESBANK": 8000, "UNIONBANK": 5000, "BANKNIFTY": 30
}

BANK_NAMES = list(BANK_WEIGHTS.keys())
active_watches = {}
option_history = {}
_options_df = None
_futures_df = None

# ================= FIXED LOADERS FOR APRIL =================
def load_options():
    global _options_df
    df = pd.read_csv("instruments.csv")
    df['expiry'] = pd.to_datetime(df['expiry'], dayfirst=True)
    # Only pick contracts that haven't expired (April onwards)
    _options_df = df[(df['segment'] == "NFO-OPT") & (df['expiry'] >= pd.Timestamp.now().normalize())]
    return _options_df

def load_futures():
    global _futures_df
    df = pd.read_csv("instruments.csv")
    df['expiry'] = pd.to_datetime(df['expiry'], dayfirst=True)
    # Only pick contracts that haven't expired (April onwards)
    _futures_df = df[(df['segment'].str.contains("FUT")) & (df['expiry'] >= pd.Timestamp.now().normalize())]
    return _futures_df

def get_expiry(df):
    if df.empty: return None
    return df['expiry'].min() # Returns the nearest active monthly/weekly expiry

def get_future(name):
    df = load_futures()
    f = df[df['name'] == name]
    exp = get_expiry(f)
    if not exp: return None
    return "NFO:" + f[f['expiry'] == exp].iloc[0]['tradingsymbol']

def get_strength_label(lots):
    if lots >= 500: return "🔥 BLAST ALERT"
    if lots >= 300: return "🌟 AWESOME"
    return "✅ GOOD"

def classify_action(symbol, oi_chg, p_chg):
    is_ce = symbol.endswith("CE")
    if oi_chg > 0:
        return f"{'CALL' if is_ce else 'PUT'} BUY 🔵" if p_chg >= 0 else f"{'CALL' if is_ce else 'PUT'} WRITER ✍️"
    return f"SHORT COVERING 🔥" if p_chg >= 0 else f"LONG UNWINDING ⚠️"

# ================= CALCULATION LOGIC =================
def calculate_heatmap(kite):
    bn_alerts, stock_alerts, velocity_alerts = [], [], []
    report_lines = ["📊 *BANKNIFTY HEATMAP*"]
    total_score = 0
    now = datetime.now()

    for name in BANK_NAMES + ["BANKNIFTY"]:
        try:
            fut = get_future(name)
            if not fut: continue

            quotes = kite.quote([fut])
            if fut not in quotes or not quotes[fut]: continue
            
            d = quotes[fut]
            ltp = d["last_price"]
            change = ((ltp - d['ohlc']['close']) / d['ohlc']['close']) * 100
            
            weight = BANK_WEIGHTS.get(name, 10)
            total_score += (change * (weight / 100))
            
            icon = "🟢" if change > 0 else "🔴"
            report_lines.append(f"{icon} *{name}*: `{ltp:.2f}` ({change:+.2f}%)")

            # Option Burst Logic
            atm = round(ltp / 100) * 100
            opt_df = load_options()
            targets = opt_df[(opt_df['name'] == (name if name != "BANKNIFTY" else "BANKNIFTY")) & (opt_df['strike'] == atm)]
            
            for _, row in targets.iterrows():
                token = str(int(row['instrument_token']))
                q_opt = kite.quote([token])
                if token not in q_opt or not q_opt[token]: continue
                
                curr_oi = q_opt[token]['oi']
                curr_p = q_opt[token]['last_price']
                
                t_key = f"{token}_burst"
                if t_key not in active_watches:
                    active_watches[t_key] = {"oi": curr_oi, "p": curr_p, "time": time.time()}
                else:
                    w = active_watches[t_key]
                    if time.time() - w["time"] >= 60:
                        oi_diff = curr_oi - w["oi"]
                        lots = int(abs(oi_diff) / LOT_SIZES.get(name, 1))
                        
                        if lots >= 150:
                            msg = f"{get_strength_label(lots)}\n🚨 {classify_action(row['tradingsymbol'], oi_diff, curr_p - w['p'])}\nSymbol: `{row['tradingsymbol']}`\nLOTS: `{lots}`\nPRICE: `{curr_p}`"
                            if name == "BANKNIFTY": bn_alerts.append(msg)
                            else: stock_alerts.append(msg)
                        active_watches[t_key] = {"oi": curr_oi, "p": curr_p, "time": time.time()}

        except Exception as e:
            continue

    return total_score, "\n".join(report_lines), bn_alerts, stock_alerts, velocity_alerts
