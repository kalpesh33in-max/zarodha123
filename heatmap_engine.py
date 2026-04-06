import pandas as pd
from datetime import datetime, timedelta
import os

BANK_WEIGHTS = {
    "HDFCBANK": 19.7, "ICICIBANK": 16.1, "SBIN": 10.7, "AXISBANK": 9.9,
    "KOTAKBANK": 9.2, "FEDERALBNK": 5.6, "INDUSINDBK": 4.7, "BANKBARODA": 4.5,
    "AUBANK": 4.0, "CANBK": 3.9, "PNB": 3.5, "IDFCFIRSTB": 3.2, "YESBANK": 2.5, "UNIONBANK": 2.5
}

LOT_SIZES = {
    "HDFCBANK": 550, "ICICIBANK": 700, "SBIN": 750, "AXISBANK": 625,
    "KOTAKBANK": 2000, "FEDERALBNK": 5000, "INDUSINDBK": 500, "BANKBARODA": 4850,
    "AUBANK": 1000, "CANBK": 2250, "PNB": 4000, "IDFCFIRSTB": 7500, "YESBANK": 8000,
    "UNIONBANK": 5000, "BANKNIFTY": 30
}

BANK_NAMES = list(BANK_WEIGHTS.keys())
INDEX_SYMBOL = "NSE:NIFTY BANK"

last_oi_store = {}
option_history = {} 
active_watches = {} 
price_velocity_store = {} 

_options_df = None
_futures_df = None

def load_options_data(kite=None):
    global _options_df
    if _options_df is None:
        if not os.path.exists("instruments.csv") and kite:
            print("instruments.csv missing. Downloading...")
            inst = kite.instruments("NFO")
            pd.DataFrame(inst).to_csv("instruments.csv", index=False)
        
        try:
            df = pd.read_csv("instruments.csv")
            _options_df = df[df['segment'].isin(['NFO-OPT'])].copy()
            _options_df['expiry'] = pd.to_datetime(_options_df['expiry'])
        except Exception as e:
            print(f"Error loading Options: {e}")
    return _options_df

def load_futures_data(kite=None):
    global _futures_df
    if _futures_df is None:
        try:
            df = pd.read_csv("instruments.csv")
            _futures_df = df[df['segment'].str.contains('-FUT', na=False)].copy()
            _futures_df['expiry'] = pd.to_datetime(_futures_df['expiry'])
        except Exception as e:
            print(f"Error loading Futures: {e}")
    return _futures_df

def get_active_future(name, segment, exchange, kite=None):
    df = load_futures_data(kite)
    if df is None or df.empty: return None
    
    now = datetime.now().date()
    futures = df[(df['name'] == name) & (df['segment'] == segment)].copy()
    # Filter for expiries that are today or in the future
    futures = futures[futures['expiry'].dt.date >= now]
    
    if futures.empty: return None
    
    nearest_expiry = futures['expiry'].min()
    active_contract = futures[futures['expiry'] == nearest_expiry]
    
    if not active_contract.empty:
        return f"{exchange}:" + active_contract.iloc[0]['tradingsymbol']
    return None

def get_bank_futures(kite):
    symbols = []
    for name in BANK_NAMES:
        sym = get_active_future(name, 'NFO-FUT', 'NFO', kite)
        if sym:
            symbols.append(sym)
    return symbols

def get_relevant_options(underlying_name, ltp, kite=None):
    df = load_options_data(kite)
    if df is None or df.empty: return pd.DataFrame()
    
    now = datetime.now().date()
    options = df[(df['name'] == underlying_name) & (df['expiry'].dt.date >= now)].copy()
    if options.empty: return pd.DataFrame()
    
    expiries = sorted(options['expiry'].unique())
    
    # Auto-detect nearest monthly expiry
    if underlying_name == "BANKNIFTY":
        fut_df = load_futures_data(kite)
        bn_fut = fut_df[(fut_df['name'] == "BANKNIFTY") & (fut_df['expiry'].dt.date >= now)]
        active_expiry = bn_fut['expiry'].min() if not bn_fut.empty else expiries[0]
    else:
        active_expiry = expiries[0]

    current_expiry_options = options[options['expiry'] == active_expiry]
    strikes = sorted(current_expiry_options['strike'].unique())
    if not strikes: return pd.DataFrame()
    
    atm_strike = min(strikes, key=lambda x: abs(x - ltp))
    idx = strikes.index(atm_strike)
    
    range_size = 15 if underlying_name == "BANKNIFTY" else 10
    min_idx, max_idx = max(0, idx - range_size), min(len(strikes) - 1, idx + range_size)
    relevant_strikes = strikes[min_idx : max_idx+1]
    
    return current_expiry_options[current_expiry_options['strike'].isin(relevant_strikes)]

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

def calculate_heatmap(kite):
    fut_symbols = get_bank_futures(kite)
    all_symbols = fut_symbols + [INDEX_SYMBOL]
    
    try:
        data = kite.quote(all_symbols)
    except Exception as e:
        return 0, f"Error: {e}", [], [], []

    score = 0
    report = "📊 *BANK MOVEMENT (FUTURES)*\n\n"
    all_option_tokens = []
    underlying_option_map = {} 
    
    TOP_SIX = BANK_NAMES[:6]
    for name in TOP_SIX + ["BANKNIFTY"]:
        underlying_ltp = 0
        if name == "BANKNIFTY":
            underlying_ltp = data.get(INDEX_SYMBOL, {}).get("last_price", 0)
        else:
            for s in fut_symbols:
                if name in s:
                    underlying_ltp = data.get(s, {}).get("last_price", 0)
                    break
        
        if underlying_ltp > 0:
            relevant_options_df = get_relevant_options(name, underlying_ltp, kite)
            if not relevant_options_df.empty:
                underlying_option_map[name] = (relevant_options_df, underlying_ltp)
                all_option_tokens.extend(relevant_options_df['instrument_token'].tolist())

    option_quotes = {}
    if all_option_tokens:
        try:
            for i in range(0, len(all_option_tokens), 400):
                batch = all_option_tokens[i:i+400]
                option_quotes.update(kite.quote(batch))
        except Exception as e:
            print(f"Bulk Option Quote Error: {e}")

    bn_alerts, stock_alerts, velocity_alerts = [], [], []
    short_names = {"HDFCBANK": "HDBFU", "ICICIBANK": "ICIBFU", "SBIN": "SBINFU", "AXISBANK": "AXISFU", "KOTAKBANK": "KOTFU", "BANKNIFTY": "BANKNIFTY"}
    bank_signals = {}

    for s in fut_symbols:
        if s not in data: continue
        d = data[s]
        ltp, open_p, oi = d["last_price"], d["ohlc"]["open"], d.get("oi", 0)
        change = ((ltp - open_p) / open_p) * 100 if open_p > 0 else 0
        name = next((n for n in BANK_NAMES if n in s), "UNKNOWN")
        
        weighted = (change / 100) * BANK_WEIGHTS.get(name, 0)
        score += weighted * 100
        bank_signals[name] = "BUY" if change > 0.3 else "SELL" if change < -0.3 else "NEUTRAL"

        process_future_burst(s, name, ltp, oi, velocity_alerts, threshold=300)

        prev_oi = last_oi_store.get(name, oi)
        oi_increase_lots = int((oi - prev_oi) / LOT_SIZES.get(name, 1))
        last_oi_store[name] = oi

        pcr = 1.0
        if name in underlying_option_map:
            alert_list = stock_alerts if name in TOP_SIX else []
            pcr = process_option_logic(name, underlying_option_map[name], option_quotes, alert_list)

        oi_str = f"{oi/1000000:.1f}M" if oi >= 1000000 else f"{oi/1000:.0f}K"
        oi_icon = "⬆️" if oi_increase_lots >= 0 else "⬇️"
        report += f"{short_names.get(name, name)}={ltp} , COP%={change:+.2f}% , TOI: {oi_str},OI{oi_icon}={abs(oi_increase_lots)}LOT,PCR-{pcr:.1f}\n"

    if INDEX_SYMBOL in data:
        idx_d = data[INDEX_SYMBOL]
        ltp, open_p = idx_d["last_price"], idx_d["ohlc"]["open"]
        change = ((ltp - open_p) / open_p) * 100 if open_p > 0 else 0
        
        bn_fut_sym = get_active_future("BANKNIFTY", "NFO-FUT", "NFO", kite)
        if bn_fut_sym in data:
            f_d = data[bn_fut_sym]
            process_future_burst(bn_fut_sym, "BANKNIFTY", f_d["last_price"], f_d.get("oi", 0), velocity_alerts, threshold=300)

        pcr = process_option_logic("BANKNIFTY", underlying_option_map.get("BANKNIFTY", (pd.DataFrame(), ltp)), option_quotes, bn_alerts)
        report += f"\nBANKNIFTY={ltp} , COP%={change:+.2f}% , PCR-{pcr:.2f}\n"

    suggestion = "🚀 STRONG BUY" if score > 30 else "✅ BUY" if score > 15 else "🔥 STRONG SELL" if score < -30 else "❌ SELL" if score < -15 else "⚖️ NEUTRAL"
    report += f"\n⚖️ *SENTIMENT SCORE: {score:.2f}*\n💡 SUGGESTION: *{suggestion}*"
    
    return score, report, bn_alerts, stock_alerts, velocity_alerts

def process_future_burst(symbol, name, ltp, oi, alerts_list, threshold=100):
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
            final_oi_chg, final_price_chg = oi - watch["start_oi"], ltp - watch["start_price"]
            final_lots = int(abs(final_oi_chg) / lot_size)
            if final_lots >= threshold:
                strength = get_strength_label(final_lots)
                action = classify_action(watch['symbol'], final_oi_chg, final_price_chg)
                price_icon = "▲" if final_price_chg >= 0 else "▼"
                alerts_list.append(f"{strength}\n🚨 {action}\nSymbol: {watch['symbol']}\nLOTS: {final_lots}\nPRICE: {ltp:.2f} ({price_icon})")
            del active_watches[key]
    history.append({'time': now, 'oi': oi, 'price': ltp})
    if len(history) > 20: history.pop(0)

def process_option_logic(name, underlying_data, option_quotes, itm_alerts_list):
    opt_df, u_ltp = underlying_data
    if opt_df.empty: return 1.0
    total_call_oi = total_put_oi = 0
    lot_size, threshold, now = LOT_SIZES.get(name, 1), 100, datetime.now()

    for _, row in opt_df.iterrows():
        t_int = int(row['instrument_token'])
        t_str = str(t_int)
        if t_str not in option_quotes: continue
        q = option_quotes[t_str]
        curr_oi, curr_price = q.get('oi', 0), q.get('last_price', 0)
        if row['instrument_type'] == 'CE': total_call_oi += curr_oi
        else: total_put_oi += curr_oi

        if t_int not in option_history: option_history[t_int] = []
        history = option_history[t_int]
        prev_oi, prev_price = history[-1]['oi'] if history else 0, history[-1]['price'] if history else 0

        if prev_oi > 0:
            tick_lots = int(abs(curr_oi - prev_oi) / lot_size)
            if tick_lots >= threshold and t_int not in active_watches:
                active_watches[t_int] = {"start_oi": prev_oi, "start_price": prev_price, "end_time": now + timedelta(minutes=1), "symbol": row['tradingsymbol'], "underlying": name}

        if t_int in active_watches:
            watch = active_watches[t_int]
            if now >= watch["end_time"]:
                final_oi_chg, final_price_chg = curr_oi - watch["start_oi"], curr_price - watch["start_price"]
                final_lots = int(abs(final_oi_chg) / lot_size)
                if final_lots >= threshold:
                    strength = get_strength_label(final_lots)
                    action = classify_action(watch['symbol'], final_oi_chg, final_price_chg)
                    itm_alerts_list.append(f"{strength}\n🚨 {action}\nSymbol: {watch['symbol']}\nLOTS: {final_lots}\nPRICE: {curr_price:.2f}")
                del active_watches[t_int]
        history.append({'time': now, 'oi': curr_oi, 'price': curr_price})
        if len(history) > 20: history.pop(0)

    return total_put_oi / total_call_oi if total_call_oi > 0 else 1.0
