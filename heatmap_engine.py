import pandas as pd
import time
from datetime import datetime

# ================= CONFIG =================
BANK_NAMES = ["HDFCBANK","ICICIBANK","SBIN","AXISBANK","KOTAKBANK"]
LOT_SIZES = {
    "BANKNIFTY": 30, "HDFCBANK": 550, "ICICIBANK": 700, 
    "SBIN": 750, "AXISBANK": 625, "KOTAKBANK": 2000
}

option_history = {}
accumulator = {}
timer_store = {}
last_oi_store = {}
_options_df = None
_futures_df = None

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
    return opt.iloc[max(0,idx-10):idx+10]

def format_alert(symbol, lots, price, fut, prev, change, curr, inst, opt_type):
    tag = "🌟 AWESOME" if lots >= 350 else "✅ VERY GOOD" if lots >= 200 else "⚡ GOOD"
    actions = {"BUY": f"{opt_type} BUY", "WRITER": f"{opt_type} WRITER ✍️", "SC": f"SHORT COVERING ({opt_type}) 🔥", "UW": f"LONG UNWINDING ({opt_type}) ⚠️"}
    action = actions.get(inst, "TRADING")
    arrow = "▲" if change >= 0 else "▼"
    return f"{tag}\n🚨 {action}\nSymbol: {symbol}\nLOTS: {lots}\nPRICE: {price:.2f} ({arrow})\nFUTURE: {fut:.2f}\nOI CHG: {change:+,}\nTIME: {datetime.now().strftime('%H:%M:%S')}"

def calculate_heatmap(kite):
    """Core Logic: Returns (score, report, bn_alerts, stock_alerts, velocity_alerts)"""
    bn_alerts = []
    stock_alerts = []
    velocity_alerts = [] # Placeholder for future logic
    total_score = 0
    report_lines = ["📊 *MARKET SNAPSHOT*"]

    for name in BANK_NAMES + ["BANKNIFTY"]:
        try:
            fut_symbol = get_future(name)
            data = kite.quote([fut_symbol])[fut_symbol]
            ltp = data["last_price"]
            
            # Simplified score logic for example
            change_pct = ((ltp - data['ohlc']['close']) / data['ohlc']['close']) * 100
            total_score += change_pct
            report_lines.append(f"{name}: {ltp:.2f} ({change_pct:+.2f}%)")

            opt_df = get_options(name, ltp)
            tokens = opt_df['instrument_token'].tolist()
            quotes = kite.quote(tokens)

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

                if now - timer_store[key] >= 60:
                    data_acc = accumulator[key]
                    lot = LOT_SIZES["BANKNIFTY"] if "BANKNIFTY" in key else LOT_SIZES.get(name, 1)
                    lots = int(abs(data_acc["oi"]) / lot)
                    
                    if lots > (50 if "BANKNIFTY" in key else 100):
                        price_change = data_acc["end"] - data_acc["start"]
                        inst = "BUY" if data_acc["oi"] > 0 and price_change > 0 else "WRITER"
                        alert = format_alert(key, lots, price, ltp, prev, data_acc["oi"], oi, inst, row["instrument_type"])
                        
                        if "BANKNIFTY" in key: bn_alerts.append(alert)
                        else: stock_alerts.append(alert)
                    
                    accumulator[key] = {"oi":0,"start":price,"end":price}
                    timer_store[key] = now
        except Exception as e:
            print(f"Error processing {name}: {e}")

    return total_score, "\n".join(report_lines), bn_alerts, stock_alerts, velocity_alerts
