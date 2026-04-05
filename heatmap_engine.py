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
    "BANKNIFTY": 30
}

BANK_NAMES = ["HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "KOTAKBANK"]
INDEX_SYMBOL = "NSE:NIFTY BANK"

# Store previous OI to calculate OI INCREASE
last_oi_store = {}
# Specifically for ITM/ATM Option alerts (Stores history for one-hour health check)
option_history = {} # {token: [list of (time, oi, price)]}
option_morning_oi = {} # {token: morning_oi} for CHG_OI calculation

oi_strike_history = {} # {name: {'max_c': val, 'max_p': val, 'chg_c': val, 'chg_p': val}}

# Cache options and futures data
_options_df = None
_futures_df = None

def load_options_data():
    global _options_df
    if _options_df is None:
        try:
            df = pd.read_csv("instruments.csv")
            _options_df = df[df['segment'] == 'NFO-OPT'].copy()
            _options_df['expiry'] = pd.to_datetime(_options_df['expiry'], dayfirst=True)
        except Exception as e:
            print(f"Error loading NFO-OPT from instruments.csv: {e}")
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
    df = load_options_data()
    if df is None or df.empty: return pd.DataFrame()
    options = df[df['name'] == underlying_name]
    if options.empty: return pd.DataFrame()
    
    expiries = sorted(options['expiry'].unique())
    if underlying_name == "BANKNIFTY":
        fut_df = load_futures_data()
        bn_fut = fut_df[fut_df['name'] == "BANKNIFTY"]
        if not bn_fut.empty:
            monthly_expiry = bn_fut['expiry'].min()
        else:
            monthly_expiry = expiries[0]
    else:
        monthly_expiry = expiries[0]

    current_expiry_options = options[options['expiry'] == monthly_expiry]
    
    strikes = sorted(current_expiry_options['strike'].unique())
    if not strikes: return pd.DataFrame()
    
    atm_strike = min(strikes, key=lambda x: abs(x - ltp))
    idx = strikes.index(atm_strike)
    
    min_idx, max_idx = max(0, idx - 10), min(len(strikes) - 1, idx + 10)
    relevant_strikes = strikes[min_idx : max_idx+1]
    
    return current_expiry_options[current_expiry_options['strike'].isin(relevant_strikes)]

def get_strength_label(lots):
    if lots >= 400: return "🚀 BLAST 🚀"
    elif lots >= 300: return "☀️ AWESOME"
    elif lots >= 200: return "✅ VERY GOOD"
    elif lots >= 100: return "⚡ GOOD"
    else: return ""

def classify_action(symbol, oi_change, price_change):
    is_call = symbol.endswith("CE")
    if oi_change > 0:
        if price_change >= 0:
            return "CALL BUY 🔵" if is_call else "PUT BUY 🔴"
        else:
            return "CALL WRITER ✍️" if is_call else "PUT WRITER ✍️"
    else:
        if price_change >= 0:
            return "SHORT COVERING ⤴️" if is_call else "SHORT COVERING ⤴️"
        else:
            return "LONG UNWINDING ⤵️" if is_call else "LONG UNWINDING ⤵️"

def calculate_heatmap(kite):
    fut_symbols = get_bank_futures(kite)
    
    all_symbols = fut_symbols + [INDEX_SYMBOL]
    
    try:
        data = kite.quote(all_symbols)
    except Exception as e:
        return 0, f"Error: {e}"

    score = 0
    report = "📊 *BANK MOVEMENT (FUTURES)*\n\n"
    
    all_option_tokens = []
    underlying_option_map = {}
    
    for name in BANK_NAMES + ["BANKNIFTY"]:
        underlying_ltp = 0
        if name == "BANKNIFTY":
            underlying_ltp = data.get(INDEX_SYMBOL, {}).get("last_price", 0)
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

    option_quotes = {}
    if all_option_tokens:
        try:
            option_quotes = kite.quote(all_option_tokens)
        except Exception as e:
            print(f"Bulk Option Quote Error: {e}")

    itm_alerts_list = []
    short_names = {"HDFCBANK": "HDBFU", "ICICIBANK": "ICIBFU", "SBIN": "SBINFU", "AXISBANK": "AXISFU", "KOTAKBANK": "KOTFU", "BANKNIFTY": "BANKNIFTY"}

    for s in fut_symbols:
        if s not in data: continue
        d = data[s]
        ltp, open_p, oi = d["last_price"], d["ohlc"]["open"], d.get("oi", 0)
        change = ((ltp - open_p) / open_p) * 100 if open_p > 0 else 0
        name = next((n for n in BANK_NAMES if n in s), "UNKNOWN")
        
        weighted = (change / 100) * BANK_WEIGHTS.get(name, 0)
        score += weighted * 100

        oi_increase_lots = 0
        if name in last_oi_store:
            oi_increase_lots = int((oi - last_oi_store[name]) / LOT_SIZES.get(name, 1))
            # Future Burst logic
            abs_fut_lots = abs(oi_increase_lots)
            if abs_fut_lots >= 100:
                direction = "added!" if oi_increase_lots > 0 else "unwound!"
                itm_alerts_list.append(f"⚠️ *{name} FUTURE BURST:* {abs_fut_lots} Lots {direction}")
        last_oi_store[name] = oi

        pcr = 1.0
        max_c_strike = max_p_strike = 0
        max_c_chg_strike = max_p_chg_strike = 0

        if name in underlying_option_map:
            opt_df, u_ltp = underlying_option_map[name]
            total_call_oi = total_put_oi = 0
            m_c_oi = m_p_oi = 0
            m_c_chg = m_p_chg = -float('inf')
            lot_size = LOT_SIZES.get(name, 1)
            now = datetime.now()
            
            for _, row in opt_df.iterrows():
                t_str = str(int(row['instrument_token']))
                if t_str not in option_quotes: continue
                
                q = option_quotes[t_str]
                curr_oi = q.get('oi', 0)
                curr_price = q.get('last_price', 0)
                t_int = int(t_str)

                # Track Morning OI for CHG_OI
                if t_int not in option_morning_oi:
                    option_morning_oi[t_int] = curr_oi
                curr_chg = curr_oi - option_morning_oi[t_int]
                
                if row['instrument_type'] == 'CE':
                    total_call_oi += curr_oi
                    if curr_oi > m_c_oi: m_c_oi, max_c_strike = curr_oi, row['strike']
                    if curr_chg > m_c_chg: m_c_chg, max_c_chg_strike = curr_chg, row['strike']
                else:
                    total_put_oi += curr_oi
                    if curr_oi > m_p_oi: m_p_oi, max_p_strike = curr_oi, row['strike']
                    if curr_chg > m_p_chg: m_p_chg, max_p_chg_strike = curr_chg, row['strike']
                
                if t_int not in option_history: option_history[t_int] = []
                history = option_history[t_int]
                history.append({'time': now, 'oi': curr_oi, 'price': curr_price})
                history[:] = [p for p in history if (now - p['time']).total_seconds() < 3600]

                if len(history) >= 2:
                    prev = history[-2]
                    price_change = curr_price - prev['price']
                    oi_change = curr_oi - prev['oi']
                    oi_change_lots = int(oi_change / lot_size)
                    abs_lots = abs(oi_change_lots)
                    
                    action = classify_action(row['tradingsymbol'], oi_change, price_change)
                    
                    should_alert = False
                    if "WRITER" in action or "SHORT COVERING" in action:
                        if abs_lots >= 100: should_alert = True
                    else:
                        if abs_lots >= 300: should_alert = True
                    
                    if should_alert:
                        strength = get_strength_label(abs_lots)
                        price_icon = "▲" if price_change >= 0 else "▼"
                        itm_alerts_list.append(
                            f"{strength}\n"
                            f"🚨 {action}\n"
                            f"Symbol: {row['tradingsymbol']}\n"
                            f"---------------------------------\n"
                            f"LOTS: {abs_lots}\n"
                            f"PRICE: {curr_price:.2f} ({price_icon})\n"
                            f"FUTURE PRICE: {u_ltp:.2f}\n"
                            f"---------------------------------\n"
                            f"EXISTING OI: {prev['oi']:,}\n"
                            f"OI CHANGE : {oi_change:+,}\n"
                            f"NEW OI    : {curr_oi:,}\n"
                        )

            # OI Shift Logic
            if name not in oi_strike_history:
                oi_strike_history[name] = {'max_c': max_c_strike, 'max_p': max_p_strike, 'chg_c': max_c_chg_strike, 'chg_p': max_p_chg_strike}
            else:
                hist = oi_strike_history[name]
                if max_c_strike != hist['max_c']:
                    itm_alerts_list.append(f"🔄 *{name} MAX CE SHIFT:* {hist['max_c']} → {max_c_strike} 🧱")
                    hist['max_c'] = max_c_strike
                if max_p_strike != hist['max_p']:
                    itm_alerts_list.append(f"🔄 *{name} MAX PE SHIFT:* {hist['max_p']} → {max_p_strike} 🛡️")
                    hist['max_p'] = max_p_strike
                if max_c_chg_strike != hist['chg_c']:
                    itm_alerts_list.append(f"🔥 *{name} CHG CE SHIFT:* {hist['chg_c']} → {max_c_chg_strike}")
                    hist['chg_c'] = max_c_chg_strike
                if max_p_chg_strike != hist['chg_p']:
                    itm_alerts_list.append(f"🔥 *{name} CHG PE SHIFT:* {hist['chg_p']} → {max_p_chg_strike}")
                    hist['chg_p'] = max_p_chg_strike

            pcr = total_put_oi / total_call_oi if total_call_oi > 0 else 1.0

        price_arrow = "⬆️" if change >= 0 else "⬇️"
        near_res = "🧱" if max_c_strike > 0 and abs(ltp - max_c_strike) / max_c_strike < 0.003 else ""
        near_sup = "🛡️" if max_p_strike > 0 and abs(ltp - max_p_strike) / max_p_strike < 0.003 else ""
        
        report += f"{short_names.get(name, name)}={ltp} {price_arrow} {near_res}{near_sup} , PCR-{pcr:.1f}\n"
        report += f"  - MAX_OI: {max_p_strike}P/{max_c_strike}C | CHG_OI: {max_p_chg_strike}P/{max_c_chg_strike}C\n\n"

    # Process Bank Nifty Index
    if INDEX_SYMBOL in data:
        idx_d = data[INDEX_SYMBOL]
        ltp, open_p, oi = idx_d["last_price"], idx_d["ohlc"]["open"], idx_d.get("oi", 0)
        change = ((ltp - open_p) / open_p) * 100 if open_p > 0 else 0
        
        idx_oi_increase_lots = 0
        if "BANKNIFTY" in last_oi_store:
            idx_oi_increase_lots = int((oi - last_oi_store["BANKNIFTY"]) / LOT_SIZES["BANKNIFTY"])
            abs_fut_lots = abs(idx_oi_increase_lots)
            if abs_fut_lots >= 100:
                direction = "added!" if idx_oi_increase_lots > 0 else "unwound!"
                itm_alerts_list.append(f"⚠️ *BANKNIFTY FUTURE BURST:* {abs_fut_lots} Lots {direction}")
        last_oi_store["BANKNIFTY"] = oi
        
        pcr = 1.0
        max_c_strike = max_p_strike = 0
        max_c_chg_strike = max_p_chg_strike = 0

        if "BANKNIFTY" in underlying_option_map:
            opt_df, u_ltp = underlying_option_map["BANKNIFTY"]
            total_call_oi = total_put_oi = 0
            m_c_oi = m_p_oi = 0
            m_c_chg = m_p_chg = -float('inf')
            lot_size = LOT_SIZES["BANKNIFTY"]
            now = datetime.now()

            for _, row in opt_df.iterrows():
                t_str = str(int(row['instrument_token']))
                if t_str not in option_quotes: continue
                
                q = option_quotes[t_str]
                curr_oi = q.get('oi', 0)
                curr_price = q.get('last_price', 0)
                t_int = int(t_str)

                # Track Morning OI for CHG_OI
                if t_int not in option_morning_oi:
                    option_morning_oi[t_int] = curr_oi
                curr_chg = curr_oi - option_morning_oi[t_int]

                if row['instrument_type'] == 'CE':
                    total_call_oi += curr_oi
                    if curr_oi > m_c_oi: m_c_oi, max_c_strike = curr_oi, row['strike']
                    if curr_chg > m_c_chg: m_c_chg, max_c_chg_strike = curr_chg, row['strike']
                else:
                    total_put_oi += curr_oi
                    if curr_oi > m_p_oi: m_p_oi, max_p_strike = curr_oi, row['strike']
                    if curr_chg > m_p_chg: m_p_chg, max_p_chg_strike = curr_chg, row['strike']
                
                if t_int not in option_history: option_history[t_int] = []
                history = option_history[t_int]
                history.append({'time': now, 'oi': curr_oi, 'price': curr_price})
                history[:] = [p for p in history if (now - p['time']).total_seconds() < 3600]

                if len(history) >= 2:
                    prev = history[-2]
                    price_change = curr_price - prev['price']
                    oi_change = curr_oi - prev['oi']
                    oi_change_lots = int(oi_change / lot_size)
                    abs_lots = abs(oi_change_lots)
                    
                    action = classify_action(row['tradingsymbol'], oi_change, price_change)
                    
                    should_alert = False
                    if "WRITER" in action or "SHORT COVERING" in action:
                        if abs_lots >= 100: should_alert = True
                    else:
                        if abs_lots >= 300: should_alert = True
                    
                    if should_alert:
                        strength = get_strength_label(abs_lots)
                        price_icon = "▲" if price_change >= 0 else "▼"
                        itm_alerts_list.append(
                            f"{strength}\n"
                            f"🚨 {action}\n"
                            f"Symbol: {row['tradingsymbol']}\n"
                            f"---------------------------------\n"
                            f"LOTS: {abs_lots}\n"
                            f"PRICE: {curr_price:.2f} ({price_icon})\n"
                            f"FUTURE PRICE: {u_ltp:.2f}\n"
                            f"---------------------------------\n"
                            f"EXISTING OI: {prev['oi']:,}\n"
                            f"OI CHANGE : {oi_change:+,}\n"
                            f"NEW OI    : {curr_oi:,}\n"
                        )

            # OI Shift Logic for BANKNIFTY
            if "BANKNIFTY" not in oi_strike_history:
                oi_strike_history["BANKNIFTY"] = {'max_c': max_c_strike, 'max_p': max_p_strike, 'chg_c': max_c_chg_strike, 'chg_p': max_p_chg_strike}
            else:
                hist = oi_strike_history["BANKNIFTY"]
                if max_c_strike != hist['max_c']:
                    itm_alerts_list.append(f"🔄 *BANKNIFTY MAX CE SHIFT:* {hist['max_c']} → {max_c_strike} 🧱")
                    hist['max_c'] = max_c_strike
                if max_p_strike != hist['max_p']:
                    itm_alerts_list.append(f"🔄 *BANKNIFTY MAX PE SHIFT:* {hist['max_p']} → {max_p_strike} 🛡️")
                    hist['max_p'] = max_p_strike
                if max_c_chg_strike != hist['chg_c']:
                    itm_alerts_list.append(f"🔥 *BANKNIFTY CHG CE SHIFT:* {hist['chg_c']} → {max_c_chg_strike}")
                    hist['chg_c'] = max_c_chg_strike
                if max_p_chg_strike != hist['chg_p']:
                    itm_alerts_list.append(f"🔥 *BANKNIFTY CHG PE SHIFT:* {hist['chg_p']} → {max_p_chg_strike}")
                    hist['chg_p'] = max_p_chg_strike

            pcr = total_put_oi / total_call_oi if total_call_oi > 0 else 1.0

        price_arrow = "⬆️" if change >= 0 else "⬇️"
        near_res = "🧱" if max_c_strike > 0 and abs(ltp - max_c_strike) / max_c_strike < 0.003 else ""
        near_sup = "🛡️" if max_p_strike > 0 and abs(ltp - max_p_strike) / max_p_strike < 0.003 else ""
        
        report += f"BANKNIFTY={ltp} {price_arrow} {near_res}{near_sup} , PCR-{pcr:.2f}\n"
        report += f"  - MAX_OI: {max_p_strike}P/{max_c_strike}C | CHG_OI: {max_p_chg_strike}P/{max_c_chg_strike}C\n"

    report += f"\n⚖️ SENTIMENT SCORE: {score:.2f}\n"
    
    if score > 30: status_line = "🚀 STATUS: STRONG BULLISH"
    elif score < -30: status_line = "📉 STATUS: STRONG BEARISH"
    else: status_line = "⚖️ STATUS: SIDEWAYS"
    report += f"{status_line}\n"
    
    if itm_alerts_list:
        report += "\n🔔 *LATEST ALERTS:*\n"
        for alert in itm_alerts_list:
            if "🚨" not in alert: # For shift/burst compact alerts
                report += f"• {alert}\n"
            else: # Detailed option flow block
                report += f"\n{alert}\n"

    return score, report
