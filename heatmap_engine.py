import pandas as pd
from datetime import datetime, timedelta

# ================= CONFIG =================

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

# State Tracking
last_oi_store = {}
last_strike_store = {} # {name: {'mc': x, 'mp': y, 'cc': x, 'cp': y}}
option_history = {} 
active_watches = {} 
accum_history = {}
price_velocity_store = {}
global_recent_alerts = [] 

_options_df = None
_futures_df = None


# ================= HELPERS =================

def add_global_alert(msg, name=None):
    global global_recent_alerts
    # Only allow alerts for Top 4 Banks and Bank Nifty
    ALLOWED_FOR_LATEST = ["HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "BANKNIFTY", "HDBFU", "ICIBFU", "SBINFU", "AXISFU"]
    
    if name:
        if not any(x in name for x in ALLOWED_FOR_LATEST):
            return

    if not global_recent_alerts or msg != global_recent_alerts[-1]:
        global_recent_alerts.append(msg)
    if len(global_recent_alerts) > 10:
        global_recent_alerts.pop(0)

def load_options_data():
    global _options_df
    if _options_df is None:
        try:
            df = pd.read_csv("instruments.csv")
            _options_df = df[df['segment'].isin(['NFO-OPT'])].copy()
            _options_df['expiry'] = pd.to_datetime(_options_df['expiry'], dayfirst=True)
        except Exception as e: print(f"Error loading Options: {e}")
    return _options_df

def load_futures_data():
    global _futures_df
    if _futures_df is None:
        try:
            df = pd.read_csv("instruments.csv")
            _futures_df = df[df['segment'].str.contains('-FUT', na=False)].copy()
            _futures_df['expiry'] = pd.to_datetime(_futures_df['expiry'], dayfirst=True)
        except Exception as e: print(f"Error loading Futures: {e}")
    return _futures_df

def get_active_future(name):
    df = load_futures_data()
    if df is None or df.empty: return None
    futures = df[df['name'] == name]
    if futures.empty: return None
    nearest_expiry = futures['expiry'].min()
    return "NFO:" + futures[futures['expiry'] == nearest_expiry].iloc[0]['tradingsymbol']

def get_bank_futures(kite):
    symbols = []
    # Include Top 4 + Bank Nifty
    for name in ["HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "BANKNIFTY"]:
        sym = get_active_future(name)
        if sym: symbols.append(sym)
    return symbols

def get_relevant_options(name, ltp):
    df = load_options_data()
    if df is None or df.empty: return pd.DataFrame()
    
    stock_ref = df[df['name'] == 'HDFCBANK']
    if stock_ref.empty:
        options = df[df['name'] == name]
        if options.empty: return pd.DataFrame()
        expiry = sorted(options['expiry'].unique())[0]
    else:
        expiry = sorted(stock_ref['expiry'].unique())[0]
    
    options = df[df['name'] == name]
    options = options[options['expiry'] == expiry]
    if options.empty: return pd.DataFrame()
    
    strikes = sorted(options['strike'].unique())
    atm = min(strikes, key=lambda x: abs(x - ltp))
    idx = strikes.index(atm)
    # Range: 25 for BANKNIFTY, 15 for Stocks
    rng = 25 if name == "BANKNIFTY" else 15
    selected = strikes[max(0, idx - rng): idx + rng + 1]
    return options[options['strike'].isin(selected)]

def get_strength_label(lots, name="BANKNIFTY"):
    if name == "BANKNIFTY":
        if lots >= 400: return "🚀 BLAST 🚀"
        elif lots >= 300: return "🌟 AWESOME"
        elif lots >= 200: return "✅ VERY GOOD"
        else: return "⚡ GOOD"
    else:
        if lots >= 150: return "🚀 BLAST 🚀"
        elif lots >= 100: return "🌟 AWESOME"
        elif lots >= 75: return "✅ VERY GOOD"
        else: return "⚡ GOOD"

def classify_action(symbol, oi_change, price_change):
    if any(x in symbol for x in ["-FUT", "FUT", "-I"]):
        if oi_change > 0: return "FUTURE BUY (LONG) 📈" if price_change >= 0 else "FUTURE SELL (SHORT) 📉"
        else: return "SHORT COVERING ↗️" if price_change >= 0 else "LONG UNWINDING ↘️"
    is_call = symbol.endswith("CE")
    if oi_change > 0:
        if price_change >= 0: return "CALL BUY 🔵" if is_call else "PUT BUY 🔴"
        else: return "CALL WRITER ✍️" if is_call else "PUT WRITER ✍️"
    else:
        if price_change >= 0: return "SHORT COVERING (CE) ⤴️" if is_call else "SHORT COVERING (PE) ⤴️"
        else: return "LONG UNWINDING (CE) ⤵️" if is_call else "LONG UNWINDING (PE) ⤵️"


# ================= DETECTION LOGIC =================

def process_future_burst(symbol, name, ltp, oi, alerts_list):
    if name not in ["HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "BANKNIFTY"]:
        return

    threshold = 100 if name == "BANKNIFTY" else 50
    lot_size = LOT_SIZES.get(name, 1)
    now = datetime.now()
    key = f"FUT_{symbol}"
    if key not in option_history: option_history[key] = []
    history = option_history[key]
    prev_oi = history[-1]['oi'] if history else 0
    prev_price = history[-1]['price'] if history else 0
    if prev_oi > 0:
        tick_lots = int(abs(oi - prev_oi) / lot_size)
        if tick_lots >= threshold and key not in active_watches:
            active_watches[key] = {"start_oi": prev_oi, "start_price": prev_price, "end_time": now + timedelta(minutes=1), "symbol": symbol, "name": name}
    if key in active_watches:
        watch = active_watches[key]
        if now >= watch["end_time"]:
            oi_chg = oi - watch["start_oi"]
            p_chg = ltp - watch["start_price"]
            final_lots = int(abs(oi_chg) / lot_size)
            if final_lots >= threshold:
                strength = get_strength_label(final_lots, watch['name'])
                action = classify_action(watch['symbol'], oi_chg, p_chg)
                p_icon = "▲" if p_chg >= 0 else "▼"
                alerts_list.append(f"{strength}\n🚨 {action}\nSymbol: {watch['symbol']}\n━━━━━━━━━━━━━━━\nLOTS: {final_lots}\nPRICE: {ltp:.2f} ({p_icon})\nFUTURE PRICE: {ltp:.2f}\n━━━━━━━━━━━━━━━\nEXISTING OI: {watch['start_oi']:,}\nOI CHANGE  : {oi_chg:+,d}\nNEW OI     : {oi:,}\nTIME: {now.strftime('%H:%M:%S')}")
                add_global_alert(f"⚠️ {watch['name']} FUTURE BURST: {final_lots} Lots added!", watch['name'])
            del active_watches[key]
    history.append({'time': now, 'oi': oi, 'price': ltp})
    if len(history) > 20: history.pop(0)

def process_option_logic(name, underlying_data, option_quotes, alerts_list):
    if name not in ["HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "BANKNIFTY"]:
        return 1.0, 0, 0, 0, 0

    threshold = 100 if name == "BANKNIFTY" else 50
    opt_df, u_ltp = underlying_data
    if opt_df.empty: return 1.0, 0, 0, 0, 0
    total_call = total_put = 0
    max_c_oi = max_p_oi = chg_c_oi = chg_p_oi = 0
    max_c = max_p = chg_c = chg_p = 0
    lot_size = LOT_SIZES.get(name, 1)
    now = datetime.now()
    for _, row in opt_df.iterrows():
        t_str = str(int(row['instrument_token']))
        if t_str not in option_quotes: continue
        q = option_quotes[t_str]
        curr_oi, ltp = q.get('oi', 0), q.get('last_price', 0)
        t_int = int(row['instrument_token'])
        if t_int not in option_history: option_history[t_int] = []
        history = option_history[t_int]
        prev_oi = history[-1]['oi'] if history else 0
        prev_price = history[-1]['price'] if history else 0
        oi_chg_tick = curr_oi - prev_oi
        if row['instrument_type'] == 'CE':
            total_call += curr_oi
            if curr_oi > max_c_oi: max_c_oi, max_c = curr_oi, row['strike']
            if oi_chg_tick > chg_c_oi: chg_c_oi, chg_c = oi_chg_tick, row['strike']
        else:
            total_put += curr_oi
            if curr_oi > max_p_oi: max_p_oi, max_p = curr_oi, row['strike']
            if oi_chg_tick > chg_p_oi: chg_p_oi, chg_p = oi_chg_tick, row['strike']
        if prev_oi > 0:
            tick_lots = int(abs(curr_oi - prev_oi) / lot_size)
            if tick_lots >= threshold and t_int not in active_watches:
                active_watches[t_int] = {"start_oi": prev_oi, "start_price": prev_price, "end_time": now + timedelta(minutes=1), "symbol": row['tradingsymbol'], "underlying": name}
        if t_int in active_watches:
            watch = active_watches[t_int]
            if now >= watch["end_time"]:
                oi_chg = curr_oi - watch["start_oi"]
                p_chg = ltp - watch["start_price"]
                final_lots = int(abs(oi_chg) / lot_size)
                if final_lots >= threshold:
                    strength = get_strength_label(final_lots, watch['underlying'])
                    action = classify_action(watch['symbol'], oi_chg, p_chg)
                    p_icon = "▲" if p_chg >= 0 else "▼"
                    alerts_list.append(f"{strength}\n🚨 {action}\nSymbol: {watch['symbol']}\n━━━━━━━━━━━━━━━\nLOTS: {final_lots}\nPRICE: {ltp:.2f} ({p_icon})\nFUTURE PRICE: {u_ltp:.2f}\n━━━━━━━━━━━━━━━\nEXISTING OI: {watch['start_oi']:,}\nOI CHANGE  : {oi_chg:+,d}\nNEW OI     : {curr_oi:,}\nTIME: {now.strftime('%H:%M:%S')}")
                    add_global_alert(f"⚠️ {name} OPTION BURST: {final_lots} Lots added!", name)
                del active_watches[t_int]
        history.append({'time': now, 'oi': curr_oi, 'price': ltp})
        if len(history) > 20: history.pop(0)

    # Track Shift Logic
    if name not in last_strike_store: last_strike_store[name] = {'mc': max_c, 'mp': max_p, 'cc': chg_c, 'cp': chg_p}
    prev = last_strike_store[name]
    
    if max_c > 0 and max_c != prev['mc'] and prev['mc'] != 0: 
        add_global_alert(f"🔄 {name} MAX CE SHIFT: {int(prev['mc'])} → {int(max_c)} 🧱", name)
    if max_p > 0 and max_p != prev['mp'] and prev['mp'] != 0: 
        add_global_alert(f"🔄 {name} MAX PE SHIFT: {int(prev['mp'])} → {int(max_p)} 🛡️", name)
    if chg_c > 0 and chg_c != prev['cc'] and prev['cc'] != 0: 
        add_global_alert(f"🔥 {name} CHG CE SHIFT: {int(prev['cc'])} → {int(chg_c)}", name)
    if chg_p > 0 and chg_p != prev['cp'] and prev['cp'] != 0: 
        add_global_alert(f"🔥 {name} CHG PE SHIFT: {int(prev['cp'])} → {int(chg_p)}", name)
    
    if max_c > 0: last_strike_store[name]['mc'] = max_c
    if max_p > 0: last_strike_store[name]['mp'] = max_p
    if chg_c > 0: last_strike_store[name]['cc'] = chg_c
    if chg_p > 0: last_strike_store[name]['cp'] = chg_p

    return (total_put / total_call if total_call > 0 else 1.0), max_c, max_p, chg_c, chg_p


# ================= MAIN =================

def calculate_heatmap(kite):
    fut_symbols = get_bank_futures(kite)
    symbols = fut_symbols + [INDEX_SYMBOL]
    try: data = kite.quote(symbols)
    except Exception as e: return 0, f"Error: {e}", [], [], []
    score = 0
    report = "📊 *BANK MOVEMENT (FUTURES)*\n\n"
    short = {"HDFCBANK": "HDBFU", "ICICIBANK": "ICIBFU", "SBIN": "SBINFU", "AXISBANK": "AXISFU", "BANKNIFTY": "BANKNIFTY"}
    REPORT_BANKS = ["HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK"]
    bn_alerts = []; stock_alerts = []; bank_signals = {}
    
    all_opt_tokens = []; underlying_map = {}
    for name in ["HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "BANKNIFTY"]:
        u_ltp = data.get(INDEX_SYMBOL if name=="BANKNIFTY" else next((s for s in fut_symbols if name in s), ""), {}).get("last_price", 0)
        if u_ltp > 0:
            df = get_relevant_options(name, u_ltp)
            if not df.empty: underlying_map[name] = (df, u_ltp); all_opt_tokens.extend(df['instrument_token'].tolist())
    
    opt_quotes = {}
    for i in range(0, len(all_opt_tokens), 400): opt_quotes.update(kite.quote(all_opt_tokens[i:i+400]))

    for name in BANK_NAMES:
        sym = next((s for s in fut_symbols if name in s), None)
        if not sym or sym not in data: continue
        d = data[sym]; ltp, open_p, oi = d["last_price"], d["ohlc"]["open"], d.get("oi", 0)
        change = ((ltp - open_p) / open_p) * 100 if open_p > 0 else 0
        score += change * BANK_WEIGHTS.get(name, 0)
        bank_signals[name] = "BUY" if change > 0.3 else "SELL" if change < -0.3 else "NEUTRAL"
        
        if name in ["HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK"]:
            process_future_burst(sym, name, ltp, oi, stock_alerts)
        
        pcr = 1.0; max_c = max_p = chg_c = chg_p = 0
        if name in underlying_map: pcr, max_c, max_p, chg_c, chg_p = process_option_logic(name, underlying_map[name], opt_quotes, stock_alerts)
        
        if name in REPORT_BANKS:
            arrow = "⬆️" if change > 0 else "⬇️"; icon = "🛡️" if pcr > 1.3 else "🧱" if pcr < 0.7 else ""
            report += f"{short[name]}={ltp:.1f}{arrow}{icon} , PCR-{pcr:.1f}\n    - MAX_OI: {int(max_p)}P/{int(max_c)}C | CHG_OI: {int(chg_p)}P/{int(chg_c)}C\n\n"

    if INDEX_SYMBOL in data:
        bn = data[INDEX_SYMBOL]; ltp, open_p = bn["last_price"], bn["ohlc"]["open"]; change = ((ltp - open_p) / open_p) * 100 if open_p > 0 else 0
        pcr, max_c, max_p, chg_c, chg_p = process_option_logic("BANKNIFTY", underlying_map["BANKNIFTY"], opt_quotes, bn_alerts)
        
        bn_fut_sym = next((s for s in fut_symbols if "BANKNIFTY" in s), None)
        if bn_fut_sym and bn_fut_sym in data:
            fd = data[bn_fut_sym]
            process_future_burst(bn_fut_sym, "BANKNIFTY", fd["last_price"], fd.get("oi",0), bn_alerts)

        arrow = "⬆️" if change > 0 else "⬇️"; icon = "🛡️" if pcr > 1.3 else "🧱" if pcr < 0.7 else ""
        report += f"BANKNIFTY={ltp:.1f}{arrow}{icon} , PCR-{pcr:.2f}\n    - MAX_OI: {int(max_p)}P/{int(max_c)}C | CHG_OI: {int(chg_p)}P/{int(chg_c)}C\n\n"

    report += "🧠 *ADVANCED INSIGHTS*"
    h, i = bank_signals.get("HDFCBANK"), bank_signals.get("ICICIBANK")
    report += "\n✅ *INDEX SYNC:* Top Banks Aligned" if h == i else f"\n⚠️ *TUG-OF-WAR:* HDFC({h}) vs ICICI({i})"
    report += f"\n\n⚖️ *SENTIMENT SCORE: {score:.2f}*"
    if abs(score) > 30 and h == i: report += "\n🌟🌟🌟 *3-STAR SIGNAL ACTIVE* 🌟🌟🌟"
    report += f"\n{'🚀' if score > 30 else '📉' if score < -30 else '⚖️'} *STATUS: {'STRONG BULLISH' if score > 30 else 'STRONG BEARISH' if score < -30 else 'SIDEWAYS'}*"
    
    report += "\n\n🔔 *LATEST ALERTS:*"
    for alert in global_recent_alerts[-5:]: report += f"\n• {alert}"
    return score, report, bn_alerts, stock_alerts, []
