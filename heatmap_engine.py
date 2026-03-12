import pandas as pd
from datetime import datetime, timedelta

BANK_WEIGHTS = {
    "HDFCBANK": 29.5,
    "ICICIBANK": 23.4,
    "SBIN": 10.8,
    "AXISBANK": 9.3,
    "KOTAKBANK": 8.0
}

LOT_SIZES = {
    "HDFCBANK": 550,
    "ICICIBANK": 700,
    "SBIN": 750,
    "AXISBANK": 625,
    "KOTAKBANK": 2000,
    "BANKNIFTY": 30,
    "CRUDEOIL": 100
}

BANK_NAMES = ["HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "KOTAKBANK"]
INDEX_SYMBOL = "NSE:NIFTY BANK"
TEST_SYMBOL = "MCX:CRUDEOIL26MARFUT"

# Store previous OI to calculate OI INCREASE
last_oi_store = {}
# Specifically for ITM/ATM Option alerts
option_history = {} # {token: [list of (time, oi, price)]}
active_watches = {} # {token: {start_oi, start_price, end_time}}

# Cache options and futures data
_options_df = None
_futures_df = None

def load_options_data():
    global _options_df
    if _options_df is None:
        try:
            df = pd.read_csv("instruments.csv")
            # Include both NFO (Stocks/Index) and MCX (Commodities)
            _options_df = df[df['segment'].isin(['NFO-OPT', 'MCX-OPT'])].copy()
            _options_df['expiry'] = pd.to_datetime(_options_df['expiry'], dayfirst=True)
        except Exception as e:
            print(f"Error loading Options from instruments.csv: {e}")
    return _options_df

def load_futures_data():
    global _futures_df
    if _futures_df is None:
        try:
            df = pd.read_csv("instruments.csv")
            _futures_df = df[df['segment'].str.contains('-FUT', na=False)].copy()
            _futures_df['expiry'] = pd.to_datetime(_futures_df['expiry'], dayfirst=True)
        except Exception as e:
            print(f"Error loading futures from instruments.csv: {e}")
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

def get_relevant_options(underlying_name, ltp):
    """Finds ITM/ATM/OTM strikes from instruments.csv for a given underlying monthly expiry."""
    df = load_options_data()
    if df is None or df.empty: return pd.DataFrame()
    options = df[df['name'] == underlying_name]
    if options.empty: return pd.DataFrame()
    
    # Logic for Monthly Expiry:
    expiries = sorted(options['expiry'].unique())
    
    if underlying_name == "BANKNIFTY":
        fut_df = load_futures_data()
        bn_fut = fut_df[fut_df['name'] == "BANKNIFTY"]
        monthly_expiry = bn_fut['expiry'].min() if not bn_fut.empty else expiries[0]
    elif underlying_name == "CRUDEOIL":
        # Crude options have specific expiries, usually the nearest one
        monthly_expiry = expiries[0]
    else:
        # Stocks only have monthly
        monthly_expiry = expiries[0]

    current_expiry_options = options[options['expiry'] == monthly_expiry]
    
    strikes = sorted(current_expiry_options['strike'].unique())
    if not strikes: return pd.DataFrame()
    
    atm_strike = min(strikes, key=lambda x: abs(x - ltp))
    idx = strikes.index(atm_strike)
    
    # NEW: Dynamic ranges (±15 for BANKNIFTY, ±10 for others)
    range_size = 15 if underlying_name == "BANKNIFTY" else 10
    min_idx, max_idx = max(0, idx - range_size), min(len(strikes) - 1, idx + range_size)
    relevant_strikes = strikes[min_idx : max_idx+1]
    
    return current_expiry_options[current_expiry_options['strike'].isin(relevant_strikes)]

def get_strength_label(lots):
    if lots >= 400: return "🚀 BLAST 🚀"
    elif lots >= 300: return "☀️ AWESOME"
    elif lots >= 200: return "✅ VERY GOOD"
    elif lots >= 100: return "⚡ GOOD"
    else: return ""

def classify_action(symbol, oi_change, price_change):
    # Futures logic (Detects -FUT, -I for GDFL style, or MCX Futures)
    if any(x in symbol for x in ["-FUT", "FUT", "-I"]):
        if oi_change > 0:
            return "FUTURE BUY (LONG) 📈" if price_change >= 0 else "FUTURE SELL (SHORT) 📉"
        else:
            return "SHORT COVERING ↗️" if price_change >= 0 else "LONG UNWINDING ↘️"
    
    # Options logic (Checks for CE/PE at the end of the symbol)
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
    
    # Dynamically fetch Crude Oil
    crude_symbol = get_active_future("CRUDEOIL", "MCX-FUT", "MCX")
    if not crude_symbol: crude_symbol = TEST_SYMBOL
        
    all_symbols = fut_symbols + [INDEX_SYMBOL, crude_symbol]
    
    try:
        data = kite.quote(all_symbols)
    except Exception as e:
        return 0, f"Error: {e}"

    score = 0
    report = "📊 *COMMODITY TEST (CRUDE OIL)* 🛢\n"
    
    crude_ltp = 0
    if crude_symbol in data:
        crude_d = data[crude_symbol]
        crude_ltp, open_p, oi = crude_d["last_price"], crude_d["ohlc"]["open"], crude_d.get("oi", 0)
        change = ((crude_ltp - open_p) / open_p) * 100 if open_p > 0 else 0
        report += f"CRUDEOIL={crude_ltp} , COP%={change:+.2f}% , TOI: {oi/1000:.0f}K\n\n"

    report += "📊 BANK MOVEMENT (FUTURES)\n\n"
    
    # Pre-calculate what options we need for EVERYTHING
    all_option_tokens = []
    underlying_option_map = {} # {name: df_of_options}
    
    # Gather tokens for all banks + Bank Nifty + Crude Oil
    for name in BANK_NAMES + ["BANKNIFTY", "CRUDEOIL"]:
        underlying_ltp = 0
        if name == "BANKNIFTY":
            underlying_ltp = data.get(INDEX_SYMBOL, {}).get("last_price", 0)
        elif name == "CRUDEOIL":
            underlying_ltp = crude_ltp
        else:
            for s in fut_symbols:
                if name in s:
                    underlying_ltp = data.get(s, {}).get("last_price", 0)
                    break
        
        if underlying_ltp > 0:
            relevant_options_df = get_relevant_options(name, underlying_ltp)
            if not relevant_options_df.empty:
                underlying_option_map[name] = (relevant_options_df, underlying_ltp)
                all_option_tokens.extend(relevant_options_df['instrument_token'].tolist())

    # FETCH ALL OPTION DATA IN ONE GO
    option_quotes = {}
    if all_option_tokens:
        try:
            # Chunking because kite.quote has a limit (usually 500)
            for i in range(0, len(all_option_tokens), 400):
                batch = all_option_tokens[i:i+400]
                option_quotes.update(kite.quote(batch))
        except Exception as e:
            print(f"Bulk Option Quote Error: {e}")

    bn_alerts = []
    stock_alerts = []
    crude_alerts = []
    short_names = {"HDFCBANK": "HDBFU", "ICICIBANK": "ICIBFU", "SBIN": "SBINFU", "AXISBANK": "AXISFU", "KOTAKBANK": "KOTFU", "BANKNIFTY": "BANKNIFTY", "CRUDEOIL": "CRUDEOIL"}

    # Process Banks
    for s in fut_symbols:
        if s not in data: continue
        d = data[s]
        ltp, open_p, oi = d["last_price"], d["ohlc"]["open"], d.get("oi", 0)
        change = ((ltp - open_p) / open_p) * 100 if open_p > 0 else 0
        name = next((n for n in BANK_NAMES if n in s), "UNKNOWN")
        
        weighted = (change / 100) * BANK_WEIGHTS.get(name, 0)
        score += weighted * 100

        # High-Speed Future Burst Logic (for Stocks)
        process_future_burst(s, name, ltp, oi, stock_alerts)

        oi_increase_lots = 0
        if name in last_oi_store:
            oi_increase_lots = int((oi - last_oi_store[name]) / LOT_SIZES.get(name, 1))
        last_oi_store[name] = oi

        # Process PCR and Options for Banks
        pcr = 1.0
        if name in underlying_option_map:
            pcr = process_option_logic(name, underlying_option_map[name], option_quotes, stock_alerts)

        oi_str = f"{oi/1000000:.1f}M" if oi >= 1000000 else f"{oi/1000:.0f}K"
        oi_icon = "⬆️" if oi_increase_lots >= 0 else "⬇️"
        report += f"{short_names.get(name, name)}={ltp} , COP%={change:+.2f}% , TOI: {oi_str},OI{oi_icon}={abs(oi_increase_lots)}LOT,PCR-{pcr:.1f}\n\n"

    # Process Bank Nifty Index
    if INDEX_SYMBOL in data:
        idx_d = data[INDEX_SYMBOL]
        ltp, open_p, oi = idx_d["last_price"], idx_d["ohlc"]["open"], idx_d.get("oi", 0)
        change = ((ltp - open_p) / open_p) * 100 if open_p > 0 else 0
        
        # High-Speed Future Burst Logic (for Bank Nifty)
        # Note: We look for the active future symbol for BN alerts
        bn_fut_sym = get_active_future("BANKNIFTY", "NFO-FUT", "NFO")
        if bn_fut_sym in data:
            f_d = data[bn_fut_sym]
            process_future_burst(bn_fut_sym, "BANKNIFTY", f_d["last_price"], f_d.get("oi", 0), bn_alerts)

        idx_oi_increase_lots = int((oi - last_oi_store.get("BANKNIFTY", oi)) / LOT_SIZES["BANKNIFTY"])
        last_oi_store["BANKNIFTY"] = oi
        pcr = process_option_logic("BANKNIFTY", underlying_option_map.get("BANKNIFTY", (pd.DataFrame(), ltp)), option_quotes, bn_alerts)
        oi_str = f"{oi/1000000:.1f}M" if oi >= 1000000 else f"{oi/1000:.0f}K"
        oi_icon = "⬆️" if idx_oi_increase_lots >= 0 else "⬇️"
        report += f"BANKNIFTY={ltp} , COP%={change:+.2f}% , TOI: {oi_str},OI{oi_icon}={abs(idx_oi_increase_lots)}LOT,PCR-{pcr:.2f}\n\n"

    # Process Crude Oil Futures Burst
    if crude_symbol in data:
        f_d = data[crude_symbol]
        process_future_burst(crude_symbol, "CRUDEOIL", f_d["last_price"], f_d.get("oi", 0), crude_alerts)

    # Process Crude Oil Options
    if "CRUDEOIL" in underlying_option_map:
        process_option_logic("CRUDEOIL", underlying_option_map["CRUDEOIL"], option_quotes, crude_alerts)

    report += f"⚖️ SENTIMENT SCORE: {score:.2f}\n"
    suggestion = "🚀 STRONG BUY" if score > 30 else "✅ BUY" if score > 15 else "🔥 STRONG SELL" if score < -30 else "❌ SELL" if score < -15 else "⚖️ NEUTRAL"
    report += f"💡 SUGGESTION: *{suggestion}*\n"
    
    # CRUDE OIL alerts stay in the general report as per your request
    if crude_alerts:
        report += "\n--- CRUDE ALERTS ---\n" + "\n".join(crude_alerts[:3])

    return score, report, bn_alerts, stock_alerts

def process_future_burst(symbol, name, ltp, oi, alerts_list):
    """Detects 100-lot Bursts for Futures using the 2-minute Watch logic."""
    lot_size = LOT_SIZES.get(name, 1)
    now = datetime.now()
    
    # We use instrument tokens or symbols as keys for history
    key = f"FUT_{symbol}"
    if key not in option_history:
        option_history[key] = []
    
    history = option_history[key]
    prev_oi = history[-1]['oi'] if history else 0
    prev_price = history[-1]['price'] if history else 0

    # 1. Trigger Watch (100+ Lots)
    if prev_oi > 0:
        tick_lots = int(abs(oi - prev_oi) / lot_size)
        if tick_lots >= 100 and key not in active_watches:
            active_watches[key] = {
                "start_oi": prev_oi,
                "start_price": prev_price,
                "end_time": now + timedelta(minutes=2),
                "symbol": symbol,
                "name": name
            }

    # 2. Confirm Watch
    if key in active_watches:
        watch = active_watches[key]
        if now >= watch["end_time"]:
            final_oi_chg = oi - watch["start_oi"]
            final_price_chg = ltp - watch["start_price"]
            final_lots = int(abs(final_oi_chg) / lot_size)
            
            if final_lots >= 100:
                strength = get_strength_label(final_lots)
                action = classify_action(watch['symbol'], final_oi_chg, final_price_chg)
                price_icon = "▲" if final_price_chg >= 0 else "▼"
                alerts_list.append(
                    f"{strength}\n"
                    f"🚨 {action}\n"
                    f"Symbol: {watch['symbol']}\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"LOTS: {final_lots}\n"
                    f"PRICE: {ltp:.2f} ({price_icon})\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"OI CHANGE: {final_oi_chg:+,}\n"
                    f"NEW OI: {oi:,}\n"
                )
            del active_watches[key]

    history.append({'time': now, 'oi': oi, 'price': ltp})
    if len(history) > 20: history.pop(0)

def process_option_logic(name, underlying_data, option_quotes, itm_alerts_list):
    """Handles PCR and GDFL-style 100-lot Burst Alert Logic."""
    opt_df, u_ltp = underlying_data
    if opt_df.empty: return 1.0
    
    total_call_oi = total_put_oi = 0
    lot_size = LOT_SIZES.get(name, 1)
    now = datetime.now()
    
    for _, row in opt_df.iterrows():
        t_int = int(row['instrument_token'])
        t_str = str(t_int)
        if t_str not in option_quotes: continue
        
        q = option_quotes[t_str]
        curr_oi, curr_price = q.get('oi', 0), q.get('last_price', 0)
        
        # PCR Tracking
        if row['instrument_type'] == 'CE': total_call_oi += curr_oi
        else: total_put_oi += curr_oi
        
        # GDFL-STYLE BURST LOGIC (100-Lot Trigger)
        if t_int not in option_history:
            option_history[t_int] = []
        
        history = option_history[t_int]
        prev_oi = history[-1]['oi'] if history else 0
        prev_price = history[-1]['price'] if history else 0
        
        # 1. Detect 100-Lot Burst to start a "Watch"
        if prev_oi > 0:
            tick_lots = int(abs(curr_oi - prev_oi) / lot_size)
            if tick_lots >= 100 and t_int not in active_watches:
                active_watches[t_int] = {
                    "start_oi": prev_oi,
                    "start_price": prev_price,
                    "end_time": now + timedelta(minutes=2),
                    "symbol": row['tradingsymbol'],
                    "underlying": name
                }

        # 2. Check Active Watches for Confirmation
        if t_int in active_watches:
            watch = active_watches[t_int]
            if now >= watch["end_time"]:
                final_oi_chg = curr_oi - watch["start_oi"]
                final_price_chg = curr_price - watch["start_price"]
                final_lots = int(abs(final_oi_chg) / lot_size)
                
                if final_lots >= 100:
                    strength = get_strength_label(final_lots)
                    action = classify_action(watch['symbol'], final_oi_chg, final_price_chg)
                    price_icon = "▲" if final_price_chg >= 0 else "▼"
                    itm_alerts_list.append(
                        f"{strength}\n"
                        f"🚨 {action}\n"
                        f"Symbol: {watch['symbol']}\n"
                        f"━━━━━━━━━━━━━━━\n"
                        f"LOTS: {final_lots}\n"
                        f"PRICE: {curr_price:.2f} ({price_icon})\n"
                        f"FUTURE: {u_ltp:.2f}\n"
                        f"━━━━━━━━━━━━━━━\n"
                        f"OI CHANGE: {final_oi_chg:+,}\n"
                        f"NEW OI: {curr_oi:,}\n"
                    )
                del active_watches[t_int] # Clear watch

        # Update History
        history.append({'time': now, 'oi': curr_oi, 'price': curr_price})
        if len(history) > 20: history.pop(0) # Keep small window

    return total_put_oi / total_call_oi if total_call_oi > 0 else 1.0
