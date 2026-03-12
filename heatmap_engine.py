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
TEST_SYMBOL = "MCX:CRUDEOIL26MARFUT"

# Store previous OI to calculate OI INCREASE
last_oi_store = {}
# Specifically for ITM/ATM Option alerts (Stores history for one-hour health check)
option_history = {} # {token: [list of (time, oi, price)]}

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
    """Finds ITM/ATM/OTM strikes from instruments.csv for a given underlying."""
    df = load_options_data()
    if df is None or df.empty: return pd.DataFrame()
    options = df[df['name'] == underlying_name]
    if options.empty: return pd.DataFrame()
    
    nearest_expiry = options['expiry'].min()
    current_expiry_options = options[options['expiry'] == nearest_expiry]
    
    strikes = sorted(current_expiry_options['strike'].unique())
    if not strikes: return pd.DataFrame()
    
    atm_strike = min(strikes, key=lambda x: abs(x - ltp))
    idx = strikes.index(atm_strike)
    
    # We take 10 strikes above and below ATM
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
    # Futures logic
    if symbol.endswith("-FUT") or "FUT" in symbol:
        if oi_change > 0:
            return "FUTURE BUY (LONG) 📈" if price_change >= 0 else "FUTURE SELL (SHORT) 📉"
        else:
            return "SHORT COVERING ↗️" if price_change >= 0 else "LONG UNWINDING ↘️"
    
    # Options logic
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
    
    if crude_symbol in data:
        crude_d = data[crude_symbol]
        ltp, open_p, oi = crude_d["last_price"], crude_d["ohlc"]["open"], crude_d.get("oi", 0)
        change = ((ltp - open_p) / open_p) * 100 if open_p > 0 else 0
        report += f"CRUDEOIL={ltp} , COP%={change:+.2f}% , TOI: {oi/1000:.0f}K\n\n"

    report += "📊 BANK MOVEMENT (FUTURES)\n\n"
    
    # Pre-calculate what options we need for EVERYTHING
    all_option_tokens = []
    underlying_option_map = {} # {name: df_of_options}
    
    # Gather tokens for all banks + Bank Nifty
    for name in BANK_NAMES + ["BANKNIFTY"]:
        # Find LTP for this underlying
        underlying_ltp = 0
        if name == "BANKNIFTY":
            underlying_ltp = data.get(INDEX_SYMBOL, {}).get("last_price", 0)
        else:
            # Find the future symbol for this bank in our data
            for s in fut_symbols:
                if name in s:
                    underlying_ltp = data.get(s, {}).get("last_price", 0)
                    break
        
        if underlying_ltp > 0:
            relevant_options_df = get_relevant_options(name, underlying_ltp)
            if not relevant_options_df.empty:
                underlying_option_map[name] = (relevant_options_df, underlying_ltp)
                all_option_tokens.extend(relevant_options_df['instrument_token'].tolist())

    # FETCH ALL OPTION DATA IN ONE GO (CRITICAL FOR PERFORMANCE)
    option_quotes = {}
    if all_option_tokens:
        try:
            # Kite Connect quote allows up to 500 tokens. We likely have ~120 here.
            option_quotes = kite.quote(all_option_tokens)
        except Exception as e:
            print(f"Bulk Option Quote Error: {e}")

    itm_alerts_list = []
    short_names = {"HDFCBANK": "HDBFU", "ICICIBANK": "ICIBFU", "SBIN": "SBINFU", "AXISBANK": "AXISFU", "KOTAKBANK": "KOTFU", "BANKNIFTY": "BANKNIFTY"}

    # Process Banks
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
        last_oi_store[name] = oi

        # CALCULATE PCR AND ALERTS FROM PRE-FETCHED DATA
        pcr = 1.0
        if name in underlying_option_map:
            opt_df, u_ltp = underlying_option_map[name]
            total_call_oi = total_put_oi = 0
            lot_size = LOT_SIZES.get(name, 1)
            now = datetime.now()
            
            for _, row in opt_df.iterrows():
                t_str = str(int(row['instrument_token']))
                if t_str not in option_quotes: continue
                
                q = option_quotes[t_str]
                curr_oi = q.get('oi', 0)
                curr_price = q.get('last_price', 0)
                
                # Update PCR
                if row['instrument_type'] == 'CE': total_call_oi += curr_oi
                else: total_put_oi += curr_oi
                
                # OPTION ALERT LOGIC
                t_int = int(t_str)
                if t_int not in option_history: option_history[t_int] = []
                history = option_history[t_int]
                history.append({'time': now, 'oi': curr_oi, 'price': curr_price})
                history[:] = [p for p in history if (now - p['time']).total_seconds() < 3600]

                if len(history) >= 2:
                    # One-Hour Health & Spike Check
                    prev = history[-2]
                    price_change = curr_price - prev['price']
                    oi_change = curr_oi - prev['oi']
                    oi_change_lots = int(oi_change / lot_size)
                    abs_lots = abs(oi_change_lots)
                    
                    action = classify_action(row['tradingsymbol'], oi_change, price_change)
                    
                    # Threshold logic
                    should_alert = False
                    if "WRITER" in action or "SHORT COVERING" in action:
                        if abs_lots >= 100: should_alert = True
                    else: # BUYER or LONG UNWINDING
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

            pcr = total_put_oi / total_call_oi if total_call_oi > 0 else 1.0

        oi_str = f"{oi/1000000:.1f}M" if oi >= 1000000 else f"{oi/1000:.0f}K"
        oi_icon = "⬆️" if oi_increase_lots >= 0 else "⬇️"
        report += f"{short_names.get(name, name)}={ltp} , COP%={change:+.2f}% , TOI: {oi_str},OI{oi_icon}={abs(oi_increase_lots)}LOT,PCR-{pcr:.1f}\n\n"

    # Process Bank Nifty Index
    if INDEX_SYMBOL in data:
        idx_d = data[INDEX_SYMBOL]
        ltp, open_p, oi = idx_d["last_price"], idx_d["ohlc"]["open"], idx_d.get("oi", 0)
        change = ((ltp - open_p) / open_p) * 100 if open_p > 0 else 0
        
        idx_oi_increase_lots = 0
        if "BANKNIFTY" in last_oi_store:
            idx_oi_increase_lots = int((oi - last_oi_store["BANKNIFTY"]) / LOT_SIZES["BANKNIFTY"])
        last_oi_store["BANKNIFTY"] = oi
        
        # Calculate Bank Nifty PCR
        pcr = 1.0
        if "BANKNIFTY" in underlying_option_map:
            opt_df, u_ltp = underlying_option_map["BANKNIFTY"]
            total_call_oi = total_put_oi = 0
            for _, row in opt_df.iterrows():
                t_str = str(int(row['instrument_token']))
                if t_str in option_quotes:
                    curr_oi = option_quotes[t_str].get('oi', 0)
                    if row['instrument_type'] == 'CE': total_call_oi += curr_oi
                    else: total_put_oi += curr_oi
            pcr = total_put_oi / total_call_oi if total_call_oi > 0 else 1.0

        oi_str = f"{oi/1000000:.1f}M" if oi >= 1000000 else f"{oi/1000:.0f}K"
        oi_icon = "⬆️" if idx_oi_increase_lots >= 0 else "⬇️"
        report += f"BANKNIFTY={ltp} , COP%={change:+.2f}% , TOI: {oi_str},OI{oi_icon}={abs(idx_oi_increase_lots)}LOT,PCR-{pcr:.2f}\n\n"

    report += f"⚖️ SENTIMENT SCORE: {score:.2f}\n"
    
    if score > 30: suggestion = "🚀 STRONG BUY"
    elif score > 15: suggestion = "✅ BUY"
    elif score < -30: suggestion = "🔥 STRONG SELL"
    elif score < -15: suggestion = "❌ SELL"
    else: suggestion = "⚖️ NEUTRAL"
    report += f"💡 SUGGESTION: *{suggestion}*\n"
    
    if itm_alerts_list:
        report += "\n---\n" + "\n".join(itm_alerts_list[:5]) # Show top 5 alerts

    return score, report
