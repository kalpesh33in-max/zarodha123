import pandas as pd
from datetime import datetime

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

option_history = {}

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


# ================= EXPIRY FIX =================
def get_monthly_expiry(df):
    today = pd.Timestamp.now().normalize()
    expiries = sorted(df['expiry'].unique())
    future_expiries = [e for e in expiries if e >= today]

    return future_expiries[0] if future_expiries else expiries[-1]


# ================= OPTIONS =================
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
    filtered = fut[fut['expiry'] == expiry]

    if filtered.empty:
        return None

    return "NFO:" + filtered.iloc[0]['tradingsymbol']


def get_bank_futures():
    return [get_active_future(n) for n in BANK_NAMES if get_active_future(n)]


# ================= ALERT LOGIC =================
def process_option_logic(name, opt_df, option_quotes, alerts, fut_price):

    lot_size = LOT_SIZES[name]

    for _, row in opt_df.iterrows():

        token = str(int(row['instrument_token']))
        if token not in option_quotes:
            continue

        q = option_quotes[token]

        curr_oi = q.get('oi', 0)
        price = q.get('last_price', 0)

        prev = option_history.get(token, 0)
        change = curr_oi - prev
        option_history[token] = curr_oi

        lots = int(abs(change) / lot_size)

        # FILTER
        if lots < 300:
            continue

        if row['instrument_type'] == "CE":
            action = "CALL BUY 🔵" if change > 0 else "CALL WRITER ✍️"
        else:
            action = "PUT BUY 🔴" if change > 0 else "PUT WRITER ✍️"

        arrow = "▲" if price >= q.get("ohlc", {}).get("open", price) else "▼"

        alert = f"""🚀 BLAST 🚀
🚨 {action}
Symbol: {row['tradingsymbol']}
━━━━━━━━━━━━━━━
LOTS: {lots}
PRICE: {price:.2f} ({arrow})
FUTURE PRICE: {fut_price:.2f}
━━━━━━━━━━━━━━━
EXISTING OI: {prev:,}
OI CHANGE  : {change:+,}
NEW OI     : {curr_oi:,}
TIME: {datetime.now().strftime('%H:%M:%S')}
"""
        alerts.append(alert)


# ================= MAIN =================
def calculate_heatmap(kite):

    fut_symbols = get_bank_futures()
    symbols = fut_symbols + [INDEX_SYMBOL]

    data = kite.quote(symbols)

    report = "📊 BANK MOVEMENT (FUTURES)\n\n"
    score = 0
    alerts = []

    short = {
        "HDFCBANK":"HDBFU",
        "ICICIBANK":"ICIBFU",
        "SBIN":"SBINFU",
        "AXISBANK":"AXISFU",
        "BANKNIFTY":"BANKNIFTY"
    }

    for sym in fut_symbols:

        if sym not in data:
            continue

        d = data[sym]

        ltp = d["last_price"]
        open_p = d["ohlc"]["open"]

        change = ((ltp-open_p)/open_p)*100
        name = next(n for n in BANK_NAMES if n in sym)

        score += change * BANK_WEIGHTS[name]

        opt_df = get_relevant_options(name, ltp)
        tokens = opt_df['instrument_token'].tolist()
        option_quotes = kite.quote(tokens)

        process_option_logic(name, opt_df, option_quotes, alerts, ltp)

        # MAX OI
        max_call=max_put=chg_call=chg_put=0
        max_call_oi=max_put_oi=chg_call_oi=chg_put_oi=0
        total_call=total_put=0

        for _,row in opt_df.iterrows():

            t=str(int(row['instrument_token']))
            if t not in option_quotes:
                continue

            oi=option_quotes[t].get("oi",0)
            prev=option_history.get(t,0)
            diff=oi-prev

            if row['instrument_type']=="CE":
                total_call+=oi
                if oi>max_call_oi:
                    max_call=row['strike']; max_call_oi=oi
                if diff>chg_call_oi:
                    chg_call=row['strike']; chg_call_oi=diff
            else:
                total_put+=oi
                if oi>max_put_oi:
                    max_put=row['strike']; max_put_oi=oi
                if diff>chg_put_oi:
                    chg_put=row['strike']; chg_put_oi=diff

        pcr = total_put/total_call if total_call else 1

        arrow="⬆️" if change>0 else "⬇️"
        icon="🛡️" if pcr>1.3 else "🧱" if pcr<0.7 else ""

        report += f"{short[name]}={ltp:.1f}{arrow}{icon} , PCR-{pcr:.1f}\n"
        report += f"   - MAX_OI: {max_put}P/{max_call}C | CHG_OI: {chg_put}P/{chg_call}C\n\n"

    report += f"⚖️ SENTIMENT SCORE: {score:.2f}\n"
    report += "🚀 STRONG BULLISH" if score>30 else "📉 STRONG BEARISH" if score<-30 else "⚖️ SIDEWAYS"

    report += "\n\n🔔 LATEST ALERTS:\n"
    for a in alerts[:5]:
        report += f"• {a.splitlines()[1]}\n"

    return score, report, [], alerts, []
