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

# Cache options data
_options_df = None

def load_options_data():
    global _options_df
    if _options_df is None:
        try:
            df = pd.read_csv("instruments.csv")
            _options_df = df[df['segment'] == 'NFO-OPT'].copy()
            _options_df['expiry'] = pd.to_datetime(_options_df['expiry'])
        except Exception as e:
            print(f"Error loading instruments.csv: {e}")
    return _options_df

def get_bank_futures(kite):
    now = datetime.now()
    month_str = now.strftime("%b").upper()
    year_str = now.strftime("%y")
    return [f"NFO:{name}{year_str}{month_str}FUT" for name in BANK_NAMES]

def get_live_pcr(kite, underlying_name, ltp):
    try:
        df = load_options_data()
        if df is None or df.empty: return 1.0
        options = df[df['name'] == underlying_name]
        if options.empty: return 1.0
        nearest_expiry = options['expiry'].min()
        current_expiry_options = options[options['expiry'] == nearest_expiry]
        strikes = sorted(current_expiry_options['strike'].unique())
        if not strikes: return 1.0
        atm_strike = min(strikes, key=lambda x: abs(x - ltp))
        idx = strikes.index(atm_strike)
        min_idx, max_idx = max(0, idx - 10), min(len(strikes) - 1, idx + 10)
        relevant_options = current_expiry_options[
            (current_expiry_options['strike'] >= strikes[min_idx]) & 
            (current_expiry_options['strike'] <= strikes[max_idx])
        ]
        tokens = relevant_options['instrument_token'].tolist()
        quotes = kite.quote(tokens)
        total_call_oi = total_put_oi = 0
        for t_str in quotes:
            instr_type = relevant_options[relevant_options['instrument_token'] == int(t_str)]['instrument_type'].values[0]
            oi = quotes[t_str].get('oi', 0)
            if instr_type == 'CE': total_call_oi += oi
            elif instr_type == 'PE': total_put_oi += oi
        return total_put_oi / total_call_oi if total_call_oi > 0 else 1.0
    except: return 1.0

def classify_action(price_change, oi_change):
    if price_change >= 0 and oi_change >= 0: return "BUYER (Long Build-up) 📈"
    if price_change < 0 and oi_change >= 0: return "WRITER (Short Build-up) 📉"
    if price_change >= 0 and oi_change < 0: return "SHORT COVERING 🚀"
    if price_change < 0 and oi_change < 0: return "LONG UNWINDING 💨"
    return "UNKNOWN"

def scan_option_alerts(kite, name, ltp):
    """Scans for ITM/ATM options with OI increase > 300 lots, one-hour health check & classification."""
    alerts = []
    try:
        df = load_options_data()
        options = df[df['name'] == name]
        nearest_expiry = options['expiry'].min()
        current_expiry_options = options[options['expiry'] == nearest_expiry]
        
        # Filter ITM & ATM Strikes (Targeting ATM and 5 strikes ITM)
        # CE ITM: Strike < LTP | PE ITM: Strike > LTP
        itm_ce = current_expiry_options[(current_expiry_options['instrument_type'] == 'CE') & (current_expiry_options['strike'] <= ltp)].tail(6)
        itm_pe = current_expiry_options[(current_expiry_options['instrument_type'] == 'PE') & (current_expiry_options['strike'] >= ltp)].head(6)
        
        target_options = pd.concat([itm_ce, itm_pe])
        tokens = target_options['instrument_token'].tolist()
        if not tokens: return []
        
        quotes = kite.quote(tokens)
        lot_size = LOT_SIZES.get(name, 1)
        now = datetime.now()

        for t_str in quotes:
            token_int = int(t_str)
            row = target_options[target_options['instrument_token'] == token_int].iloc[0]
            curr_oi = quotes[t_str].get('oi', 0)
            curr_price = quotes[t_str].get('last_price', 0)
            
            if token_int not in option_history:
                option_history[token_int] = []
            
            history = option_history[token_int]
            history.append({'time': now, 'oi': curr_oi, 'price': curr_price})
            
            # Keep only last 60 minutes
            history[:] = [p for p in history if (now - p['time']).total_seconds() < 3600]

            if len(history) < 2: continue
            
            # One-Hour Health Check: Ignore if 300-lot decrease in last hour
            has_major_decrease = False
            for i in range(1, len(history)):
                diff = history[i]['oi'] - history[i-1]['oi']
                if diff <= -(300 * lot_size):
                    has_major_decrease = True
                    break
            
            if has_major_decrease: continue

            # Minute Spike Check (current vs previous)
            prev = history[-2]
            oi_change = curr_oi - prev['oi']
            price_change = curr_price - prev['price']
            oi_change_lots = int(oi_change / lot_size)
            
            if oi_change_lots >= 300:
                action = classify_action(price_change, oi_change)
                
                alert = (f"🚨 *ITM {action}*\n"
                         f"UNDERLYING: {name}\n"
                         f"STRIKE: {row['strike']} {row['instrument_type']}\n"
                         f"LAST RATE: {curr_price}\n"
                         f"RATE CHANGE: {price_change:+.2f}\n"
                         f"EXISTING OI: {curr_oi}\n"
                         f"CHANGE OI: {oi_change:+d}\n"
                         f"CHANGE IN LOTS: {oi_change_lots} LOT\n")
                alerts.append(alert)
                
    except Exception as e:
        print(f"Option Alert Error: {e}")
    return alerts

def calculate_heatmap(kite):
    fut_symbols = get_bank_futures(kite)
    all_symbols = fut_symbols + [INDEX_SYMBOL, TEST_SYMBOL]
    
    try:
        data = kite.quote(all_symbols)
    except Exception as e:
        return 0, f"Error: {e}"

    score = 0
    report = "📊 *COMMODITY TEST (CRUDE OIL)* 🛢\n"
    
    # 0. Process Crude Oil (Test Only)
    if TEST_SYMBOL in data:
        crude_d = data[TEST_SYMBOL]
        ltp, open_p, oi = crude_d["last_price"], crude_d["ohlc"]["open"], crude_d.get("oi", 0)
        change = ((ltp - open_p) / open_p) * 100 if open_p > 0 else 0
        
        report += f"CRUDEOIL={ltp} , COP%={change:+.2f}% , TOI: {oi/1000:.0f}K\n\n"

    report += "📊 BANK MOVEMENT (FUTURES)\n\n"
    itm_alerts_list = []

    # Short names mapping
    short_names = {
        "HDFCBANK": "HDBFU",
        "ICICIBANK": "ICIBFU",
        "SBIN": "SBINFU",
        "AXISBANK": "AXISFU",
        "KOTAKBANK": "KOTFU",
        "BANKNIFTY": "BANKNIFTY"
    }

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

        pcr = get_live_pcr(kite, name, ltp)
        oi_str = f"{oi/1000000:.1f}M" if oi >= 1000000 else f"{oi/1000:.0f}K"
        
        name_short = short_names.get(name, name)
        oi_icon = "⬆️" if oi_increase_lots >= 0 else "⬇️"
        
        report += f"{name_short}={ltp} , COP%={change:+.2f}% , TOI: {oi_str},OI{oi_icon}={abs(oi_increase_lots)}LOT,PCR-{pcr:.1f}\n\n"

        itm_alerts_list.extend(scan_option_alerts(kite, name, ltp))

    if INDEX_SYMBOL in data:
        idx_d = data[INDEX_SYMBOL]
        ltp, open_p, oi = idx_d["last_price"], idx_d["ohlc"]["open"], idx_d.get("oi", 0)
        change = ((ltp - open_p) / open_p) * 100 if open_p > 0 else 0
        
        idx_oi_increase_lots = 0
        if "BANKNIFTY" in last_oi_store:
            idx_oi_increase_lots = int((oi - last_oi_store["BANKNIFTY"]) / LOT_SIZES["BANKNIFTY"])
        last_oi_store["BANKNIFTY"] = oi
        
        pcr = get_live_pcr(kite, "BANKNIFTY", ltp)
        oi_str = f"{oi/1000000:.1f}M" if oi >= 1000000 else f"{oi/1000:.0f}K"
        oi_icon = "⬆️" if idx_oi_increase_lots >= 0 else "⬇️"
        
        report += f"BANKNIFTY={ltp} , COP%={change:+.2f}% , TOI: {oi_str},OI{oi_icon}={abs(idx_oi_increase_lots)}LOT,PCR-{pcr:.2f}\n\n"
        
        itm_alerts_list.extend(scan_option_alerts(kite, "BANKNIFTY", ltp))

    report += f"⚖️ SENTIMENT SCORE: {score:.2f}\n"
    
    if score > 30: suggestion = "🚀 STRONG BUY"
    elif score > 15: suggestion = "✅ BUY"
    elif score < -30: suggestion = "🔥 STRONG SELL"
    elif score < -15: suggestion = "❌ SELL"
    else: suggestion = "⚖️ NEUTRAL"
    report += f"💡 SUGGESTION: *{suggestion}*\n"
    
    if itm_alerts_list:
        report += "\n---\n".join(itm_alerts_list)

    return score, report
