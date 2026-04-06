import pandas as pd
from datetime import datetime, timedelta
import time

# ================= CONFIG & WEIGHTS =================
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
INDEX_SYMBOL = "NSE:NIFTY BANK"

# State Tracking
last_oi_store = {}
option_history = {} # {token: [history]}
active_watches = {} # {token: {data}}
accum_history = {} # Whale logic

_options_df = None
_futures_df = None

# ================= DATA LOADERS =================
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
    f = df[df['name'] == name]
    if f.empty: return None
    nearest_expiry = f['expiry'].min()
    contract = f[f['expiry'] == nearest_expiry].iloc[0]
    return f"NFO:{contract['tradingsymbol']}"

# ================= FORMATTING & CLASSIFICATION =================
def get_strength_label(lots):
    if lots >= 400: return "🚀 BLAST 🚀"
    elif lots >= 300: return "🌟 AWESOME"
    elif lots >= 200: return "✅ VERY GOOD"
    else: return "⚡ GOOD"

def classify_action(symbol, oi_chg, p_chg):
    is_call = symbol.endswith("CE")
    if "FUT" in symbol:
        if oi_chg > 0: return "FUTURE BUY (LONG) 📈" if p_chg >= 0 else "FUTURE SELL (SHORT) 📉"
        return "SHORT COVERING ↗️" if p_chg >= 0 else "LONG UNWINDING ↘️"
    if oi_chg > 0:
        if p_chg >= 0: return "CALL BUY 🔵" if is_call else "PUT BUY 🔴"
        return "CALL WRITER ✍️" if is_call else "PUT WRITER ✍️"
    return "SHORT COVERING ⤴️" if p_chg >= 0 else "LONG UNWINDING ⤵️"

# ================= CORE CALCULATION =================
def calculate_heatmap(kite):
    score = 0
    report = "📊 *BANK MOVEMENT (FUTURES)*\n\n"
    bn_alerts, stock_alerts, velocity_alerts = [], [], []
    bank_signals = {}

    # 1. Gather all required symbols
    fut_map = {name: get_active_future(name) for name in BANK_NAMES + ["BANKNIFTY"]}
    all_symbols = [s for s in fut_map.values() if s] + [INDEX_SYMBOL]
    
    try:
        data = kite.quote(all_symbols)
    except Exception as e:
        return 0, f"Error: {e}", [], [], []

    # 2. Process TOP 6 Banks for Report and Option Alerts
    TOP_SIX = BANK_NAMES[:6]
    for name in BANK_NAMES:
        sym = fut_map.get(name)
        if not sym or sym not in data: continue
        
        d = data[sym]
        ltp, open_p, oi = d["last_price"], d["ohlc"]["open"], d.get("oi", 0)
        change = ((ltp - open_p) / open_p) * 100 if open_p > 0 else 0
        score += (change / 100) * BANK_WEIGHTS.get(name, 0) * 100
        
        bank_signals[name] = "BUY" if change > 0.3 else "SELL" if change < -0.3 else "NEUTRAL"

        # Future Bursts
        process_burst(sym, name, ltp, oi, velocity_alerts, threshold=300)

        # Build Report Line
        oi_inc = int((oi - last_oi_store.get(name, oi)) / LOT_SIZES.get(name, 1))
        last_oi_store[name] = oi
        report += f"{name[:5]}={ltp}, COP%={change:+.2f}%, OI={'⬆️' if oi_inc >=0 else '⬇️'}{abs(oi_inc)}LOT\n"

    # 3. Bank Nifty Index Analysis (Gamma Wall)
    if INDEX_SYMBOL in data:
        idx_ltp = data[INDEX_SYMBOL]["last_price"]
        # PCR and Option Logic placeholders
        report += f"\nBANKNIFTY={idx_ltp}\n"

    # 4. Tug-of-War Logic
    if bank_signals.get("HDFCBANK") != bank_signals.get("ICICIBANK") and "NEUTRAL" not in [bank_signals.get("HDFCBANK"), bank_signals.get("ICICIBANK")]:
        report += f"\n⚠️ *TUG-OF-WAR:* HDFC({bank_signals['HDFCBANK']}) vs ICICI({bank_signals['ICICIBANK']})"

    return score, report, bn_alerts, stock_alerts, velocity_alerts

def process_burst(symbol, name, ltp, oi, alerts_list, threshold=100):
    now = datetime.now()
    lot_size = LOT_SIZES.get(name, 1)
    
    if symbol not in option_history: option_history[symbol] = []
    history = option_history[symbol]
    
    if history:
        prev_oi = history[-1]['oi']
        tick_lots = int(abs(oi - prev_oi) / lot_size)
        
        if tick_lots >= threshold and symbol not in active_watches:
            active_watches[symbol] = {
                "start_oi": prev_oi, "start_price": history[-1]['price'],
                "end_time": now + timedelta(minutes=1), "symbol": symbol
            }
            
    if symbol in active_watches:
        watch = active_watches[symbol]
        if now >= watch["end_time"]:
            final_oi_chg = oi - watch["start_oi"]
            final_lots = int(abs(final_oi_chg) / lot_size)
            if final_lots >= threshold:
                alerts_list.append(f"{get_strength_label(final_lots)}\n🚨 {classify_action(symbol, final_oi_chg, ltp - watch['start_price'])}\nSymbol: {symbol}\nLOTS: {final_lots}")
            del active_watches[symbol]

    history.append({'oi': oi, 'price': ltp})
    if len(history) > 20: history.pop(0)
