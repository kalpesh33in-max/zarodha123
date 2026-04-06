import pandas as pd
import time
from datetime import datetime

# ================= CONFIG =================
BANK_NAMES = ["HDFCBANK","ICICIBANK","SBIN","AXISBANK","KOTAKBANK"]
LOT_SIZES = {
    "BANKNIFTY": 30, "HDFCBANK": 550, "ICICIBANK": 700, 
    "SBIN": 750, "AXISBANK": 625, "KOTAKBANK": 2000
}

option_history, accumulator, timer_store, last_oi_store = {}, {}, {}, {}
_options_df, _futures_df = None, None

def load_data():
    global _options_df, _futures_df
    if _options_df is None:
        df = pd.read_csv("instruments.csv")
        df['expiry'] = pd.to_datetime(df['expiry'], dayfirst=True)
        _options_df = df[df['segment']=="NFO-OPT"]
        _futures_df = df[df['segment'].str.contains("FUT")]

def get_expiry(df):
    today = pd.Timestamp.now()
    df = df[df['expiry'].dt.month == today.month]
    return sorted(df['expiry'].unique())[0]

def get_future(name):
    load_data()
    f = _futures_df[_futures_df['name'].str.contains(name, na=False)]
    exp = get_expiry(f)
    return "NFO:" + f[f['expiry']==exp].iloc[0]['tradingsymbol']

def calculate_heatmap(kite):
    """Returns: score, report, bn_alerts, stock_alerts, velocity_alerts"""
    bn_alerts, stock_alerts, velocity_alerts = [], [], []
    total_score = 0
    report_lines = ["📊 *MARKET SNAPSHOT*"]

    try:
        for name in BANK_NAMES + ["BANKNIFTY"]:
            fut = get_future(name)
            data = kite.quote([fut])[fut]
            ltp = data["last_price"]
            
            # Simple sentiment score logic
            change_pct = ((ltp - data['ohlc']['close']) / data['ohlc']['close']) * 100
            total_score += change_pct
            report_lines.append(f"{name}: {ltp:.2f} ({change_pct:+.2f}%)")
            
            # Additional logic for alerts can be added here
            
    except Exception as e:
        print(f"Heatmap Calculation Error: {e}", flush=True)

    # Return exactly 5 values as expected by scanner.py
    return total_score, "\n".join(report_lines), bn_alerts, stock_alerts, velocity_alerts
