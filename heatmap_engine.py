# ================= IMPORTS =================
import pandas as pd
from datetime import datetime, timedelta

# ================= CONFIG =================
BANK_WEIGHTS = {
    "HDFCBANK": 19.7,
    "ICICIBANK": 16.1,
    "SBIN": 10.7,
    "AXISBANK": 9.9,
}

LOT_SIZES = {
    "HDFCBANK": 550,
    "ICICIBANK": 700,
    "SBIN": 750,
    "AXISBANK": 625,
    "BANKNIFTY": 30
}

BANK_NAMES = list(BANK_WEIGHTS.keys())
INDEX_SYMBOL = "NSE:NIFTY BANK"

last_oi_store = {}
option_history = {}
active_watches = {}
accum_history = {}

_options_df = None
_futures_df = None

# ================= LOAD =================
def load_options_data():
    global _options_df
    if _options_df is None:
        df = pd.read_csv("instruments.csv")
        df['expiry'] = pd.to_datetime(df['expiry'], dayfirst=True)
        _options_df = df[df['segment'] == "NFO-OPT"]
    return _options_df

def load_futures_data():
    global _futures_df
    if _futures_df is None:
        df = pd.read_csv("instruments.csv")
        df['expiry'] = pd.to_datetime(df['expiry'], dayfirst=True)
        _futures_df = df[df['segment'].str.contains("FUT")]
    return _futures_df

# ================= MONTHLY ONLY =================
def get_monthly_expiry(df):
    return sorted(df['expiry'].unique())[0]

def get_relevant_options(name, ltp):
    df = load_options_data()
    options = df[df['name'] == name]

    expiry = get_monthly_expiry(options)
    options = options[options['expiry'] == expiry]

    strikes = sorted(options['strike'].unique())
    atm = min(strikes, key=lambda x: abs(x - ltp))

    idx = strikes.index(atm)
    rng = 15 if name == "BANKNIFTY" else 10

    return options.iloc[max(0, idx-rng): idx+rng]

# ================= FUTURES =================
def get_active_future(name):
    df = load_futures_data()
    fut = df[df['name'] == name]
    expiry = get_monthly_expiry(fut)
    return "NFO:" + fut[fut['expiry']==expiry].iloc[0]['tradingsymbol']

def get_bank_futures():
    return [get_active_future(n) for n in BANK_NAMES]

# ================= BURST =================
def process_option_logic(name, underlying_data, option_quotes, alerts):
    opt_df, u_ltp = underlying_data
    lot_size = LOT_SIZES[name]

    for _, row in opt_df.iterrows():
        token = str(int(row['instrument_token']))
        if token not in option_quotes:
            continue

        curr_oi = option_quotes[token].get('oi', 0)
        price = option_quotes[token].get('last_price', 0)

        prev = option_history.get(token, 0)
        change = curr_oi - prev
        option_history[token] = curr_oi

        lots = int(abs(change) / lot_size)

        if lots >= 100:
            action = "CALL BUY 🔵" if row['instrument_type']=="CE" else "PUT BUY 🔴"
            alerts.append(
                f"🚀 BLAST 🚀\n🚨 {action}\nSymbol: {row['tradingsymbol']}\nLOTS: {lots}"
            )

    return 1.0

# ================= MAIN =================
def calculate_heatmap(kite):

    fut_symbols = get_bank_futures()
    symbols = fut_symbols + [INDEX_SYMBOL]

    data = kite.quote(symbols)

    report = "📊 *BANK MOVEMENT (FUTURES)*\n\n"
    score = 0

    short = {
        "HDFCBANK":"HDBFU",
        "ICICIBANK":"ICIBFU",
        "SBIN":"SBINFU",
        "AXISBANK":"AXISFU",
        "BANKNIFTY":"BANKNIFTY"
    }

    stock_alerts = []

    for sym in fut_symbols:
        d = data[sym]
        ltp = d["last_price"]
        open_p = d["ohlc"]["open"]

        change = ((ltp-open_p)/open_p)*100
        name = next(n for n in BANK_NAMES if n in sym)

        score += change * BANK_WEIGHTS[name]

        opt_df = get_relevant_options(name, ltp)
        tokens = opt_df['instrument_token'].tolist()
        option_quotes = kite.quote(tokens)

        process_option_logic(name,(opt_df,ltp),option_quotes,stock_alerts)

        # ===== MAX OI =====
        max_call=max_put=chg_call=chg_put=0
        max_call_oi=max_put_oi=chg_call_oi=chg_put_oi=0
        total_call=total_put=0

        for _,row in opt_df.iterrows():
            t=str(int(row['instrument_token']))
            if t not in option_quotes: continue

            oi=option_quotes[t].get("oi",0)
            prev=option_history.get(t,0)
            diff=oi-prev

            if row['instrument_type']=="CE":
                total_call+=oi
                if oi>max_call_oi: max_call, max_call_oi=row['strike'],oi
                if diff>chg_call_oi: chg_call, chg_call_oi=row['strike'],diff
            else:
                total_put+=oi
                if oi>max_put_oi: max_put, max_put_oi=row['strike'],oi
                if diff>chg_put_oi: chg_put, chg_put_oi=row['strike'],diff

        pcr = total_put/total_call if total_call else 1

        arrow="⬆️" if change>0 else "⬇️"
        icon="🛡️" if pcr>1.3 else "🧱" if pcr<0.7 else ""

        report+=f"{short[name]}={ltp:.1f}{arrow}{icon} , PCR-{pcr:.1f}\n"
        report+=f"    - MAX_OI: {max_put}P/{max_call}C | CHG_OI: {chg_put}P/{chg_call}C\n\n"

    report+=f"⚖️ *SENTIMENT SCORE: {score:.2f}*\n"
    report+= "🚀 STRONG BUY" if score>30 else "📉 STRONG SELL" if score<-30 else "⚖️ SIDEWAYS"

    report+="\n\n🔔 *LATEST ALERTS:*"
    for a in stock_alerts[:5]:
        report+=f"\n• {a.splitlines()[1]}"

    return score, report, [], stock_alerts, []
