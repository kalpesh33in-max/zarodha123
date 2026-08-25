import os
import time
import datetime
import threading
import collections
import pandas as pd
from zoneinfo import ZoneInfo
from kiteconnect import KiteConnect

# Import existing configs and tools
import env_config
from websocket_flow import register_ws_callbacks, add_shared_tokens, get_symbol_quotes, get_token_quotes
from telegram_utils import send_telegram_message
from pure_iv_scanner import calculate_iv, _get_time_to_expiry_years, iv_state

IST = ZoneInfo("Asia/Kolkata")

# CONFIGURATION
WATCHLIST = ["BANKNIFTY", "HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "KOTAKBANK"]
VOLUME_LOT_THRESHOLD = 200
OI_ROC_THRESHOLD = 0.75
TRACKING_WINDOW_MINUTES = 60
CONSOLIDATION_MIN_MINUTES = 10

# Thread-safe global scanner state
active_spikes = {}  # symbol -> dict of spike levels and timestamps
candle_history = collections.defaultdict(list)  # symbol -> list of closed 1-min candles
state_lock = threading.Lock()
symbol_lot_sizes = {}
spot_future_map = {}
option_metadata = {}

def load_access_token():
    token = os.getenv("KITE_ACCESS_TOKEN", "").strip()
    if not token and os.path.exists("access_token.txt"):
        with open("access_token.txt", "r") as f:
            token = f.read().strip()
    return token

def load_instruments_data():
    if not os.path.exists("instruments.csv"):
        print("instruments.csv missing!")
        return pd.DataFrame()
    df = pd.read_csv("instruments.csv")
    if "expiry" in df.columns:
        df["expiry_dt"] = pd.to_datetime(df["expiry"], errors="coerce")
        import datetime
        today_date = datetime.datetime.now().date()
        df = df[df["expiry_dt"].isna() | (df["expiry_dt"].dt.date >= today_date)].copy()
    return df

def get_option_contracts(df, name, spot_price):
    underlying_opts = df[(df["name"] == name) & (df["segment"].isin(["NFO-OPT", "BFO-OPT"]))]
    if underlying_opts.empty:
        return pd.DataFrame()
    
    # Get closest monthly/weekly expiry
    underlying_opts = underlying_opts.copy()
    underlying_opts["expiry"] = pd.to_datetime(underlying_opts["expiry"])
    closest_expiry = underlying_opts["expiry"].min()
    if pd.isna(closest_expiry):
        return pd.DataFrame()
        
    exp_opts = underlying_opts[underlying_opts["expiry"] == closest_expiry]
    strikes = sorted(exp_opts["strike"].unique())
    if not strikes:
        return pd.DataFrame()
        
    # Find ATM strike
    atm = min(strikes, key=lambda x: abs(x - spot_price))
    idx = strikes.index(atm)
    
    # ATM +- 5 strikes
    selected_strikes = strikes[max(0, idx - 5): min(len(strikes), idx + 6)]
    return exp_opts[exp_opts["strike"].isin(selected_strikes)]

def format_volume(v):
    if v >= 1_000_000:
        val = v / 1_000_000
        return f"{int(val)}M" if val.is_integer() else f"{val:.1f}M"
    elif v >= 1_000:
        val = v / 1_000
        return f"{int(val)}K" if val.is_integer() else f"{val:.1f}K"
    return str(int(v))

def fmt_oi(v):
    if v >= 100_000:
        return f"{v/100000:.1f}L"
    elif v >= 1_000:
        return f"{int(v/1000)}K"
    return str(int(v))

def get_underlying_data(kite, symbol):
    # Try local websocket cache first
    quotes = get_symbol_quotes([symbol], max_age_seconds=10)
    if symbol in quotes:
        return quotes[symbol]
    # Fallback to REST API
    try:
        res = kite.quote([symbol])
        if symbol in res:
            return res[symbol]
    except Exception as e:
        print(f"Error fetching quote for {symbol}: {e}")
    return None

def analyze_option_chain_changes(kite, name, current_spot, spike_time, exp_date_str, target_opts, lot_size):
    # Fetch current option chain data
    opt_symbols = [f"{row['exchange']}:{row['tradingsymbol']}" for _, row in target_opts.iterrows()]
    quotes = {}
    try:
        quotes = kite.quote(opt_symbols)
    except Exception as e:
        print(f"Error fetching option quotes for validation: {e}")
        return None
        
    results = {}
    T = _get_time_to_expiry_years(pd.to_datetime(target_opts.iloc[0]["expiry"]))
    
    for _, row in target_opts.iterrows():
        sym_key = f"{row['exchange']}:{row['tradingsymbol']}"
        strike = float(row["strike"])
        opt_type = row["instrument_type"]
        
        quote = quotes.get(sym_key, {})
        current_oi = quote.get("oi", 0)
        current_ltp = quote.get("last_price", 0.0)
        
        # Calculate current IV
        current_iv = calculate_iv(current_ltp, current_spot, strike, T, option_type=opt_type) * 100
        
        # Retrieve historical state at spike time from pure_iv_scanner state
        state_key = f"{name}_{strike}_{exp_date_str}"
        hist_state = iv_state.get(state_key, {})
        
        open_iv = hist_state.get("open_iv_ce" if opt_type == "CE" else "open_iv_pe", 0.0) * 100
        open_price = hist_state.get("open_price_ce" if opt_type == "CE" else "open_price_pe", current_ltp)
        
        results[strike] = results.get(strike, {})
        results[strike][opt_type] = {
            "oi": current_oi,
            "ltp": current_ltp,
            "iv": current_iv,
            "oi_change_pct": ((current_oi - hist_state.get("ce_vol_total" if opt_type == "CE" else "pe_vol_total", current_oi)) / max(1, current_oi)) * 100,
            "price_change_pct": ((current_ltp - open_price) / max(0.01, open_price)) * 100,
            "iv_change_pct": current_iv - open_iv
        }
    return results

def get_bank_consolidation_status(kite, direction, spike_banks):
    bank_status = {}
    banks = ["HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "KOTAKBANK"]
    aligned_count = 0
    
    for bank in banks:
        fut_symbol = spot_future_map.get(bank)
        if not fut_symbol:
            continue
            
        curr_quote = get_underlying_data(kite, fut_symbol)
        if not curr_quote:
            continue
            
        # Get start state from the spike
        start_state = spike_banks.get(bank, {})
        start_vol = start_state.get("vol", 0)
        start_oi = start_state.get("oi", 0)
        start_price = start_state.get("price", 0.0)
        
        curr_vol = curr_quote.get("volume", 0)
        curr_oi = curr_quote.get("oi", 0)
        curr_price = float(curr_quote.get("last_price", 0.0))
        
        # Accumulate consolidation metrics
        consol_vol = max(0, curr_vol - start_vol)
        oi_change = curr_oi - start_oi
        oi_chg_pct = (oi_change / max(1, start_oi)) * 100
        price_change_pct = 0.0
        if start_price > 0:
            price_change_pct = ((curr_price - start_price) / start_price) * 100
            
        # Alignment check
        is_aligned = (direction == "BULLISH" and curr_price > start_price) or (direction == "BEARISH" and curr_price < start_price)
        if is_aligned:
            aligned_count += 1
            
        # Grab Option chain ATM CE/PE details
        opt_details = ""
        opts_df_bank = option_metadata.get(bank, pd.DataFrame())
        if not opts_df_bank.empty and start_price > 0:
            expiry_bank = pd.to_datetime(opts_df_bank.iloc[0]["expiry"]).strftime("%Y-%m-%d")
            strikes_bank = sorted(opts_df_bank["strike"].unique())
            atm_strike_bank = min(strikes_bank, key=lambda x: abs(x - curr_price))
            
            key_base = f"{bank}_{atm_strike_bank}_{expiry_bank}"
            state_val = iv_state.get(key_base, {})
            
            c_dir = state_val.get("dir_ce", " ").strip()
            p_dir = state_val.get("dir_pe", " ").strip()
            c_iv = state_val.get("close_iv_ce", 0.0) * 100
            p_iv = state_val.get("close_iv_pe", 0.0) * 100
            
            opt_details = f" | Option: CE:{c_dir} (IV={c_iv:.1f}%), PE:{p_dir} (IV={p_iv:.1f}%)"
            
        dir_icon = "🟩" if is_aligned else "🟥"
        bank_status[bank] = {
            "icon": dir_icon,
            "msg": f"Vol={format_volume(consol_vol)} | FutOI={oi_chg_pct:+.1f}% | Price={price_change_pct:+.2f}%{opt_details}"
        }
        
    return bank_status, aligned_count >= 3, aligned_count

def validate_and_alert_breakout(kite, symbol, direction, trigger_price):
    with state_lock:
        spike_info = active_spikes.get(symbol)
        if not spike_info:
            return
            
    now = datetime.datetime.now(IST)
    spike_time = spike_info["time"]
    minutes_elapsed = (now - spike_time).total_seconds() / 60.0
    
    # 1. Evaluate Time Window Rules
    if minutes_elapsed > TRACKING_WINDOW_MINUTES:
        print(f"[{symbol}] Breakout occurred after {minutes_elapsed:.1f} mins (exceeded limit). Ignoring.")
        return
        
    # Check if we should enforce consolidation rule
    is_delayed = minutes_elapsed >= CONSOLIDATION_MIN_MINUTES
    
    # 2. Gather Breakout Confirmation Metrics
    name = spike_info["name"]
    spot_price = trigger_price
    
    # Options Chain Analysis (ATM +- 5)
    opts_df = option_metadata.get(name, pd.DataFrame())
    expiry_date_str = ""
    opt_analysis = None
    pcr_shift = 0.0
    premium_strength = "N/A"
    options_summary_msg = ""
    
    if not opts_df.empty:
        expiry_date_str = pd.to_datetime(opts_df.iloc[0]["expiry"]).strftime("%Y-%m-%d")
        lot_size = int(opts_df.iloc[0].get("lot_size", 1))
        opt_analysis = analyze_option_chain_changes(kite, name, spot_price, spike_time, expiry_date_str, opts_df, lot_size)
        
        # Option PCR Skew Calculation (ATM +- 5)
        if opt_analysis:
            total_ce_oi = sum(data.get("CE", {}).get("oi", 0) for data in opt_analysis.values())
            total_pe_oi = sum(data.get("PE", {}).get("oi", 0) for data in opt_analysis.values())
            pcr_shift = total_pe_oi / max(1, total_ce_oi)
            
            # Premium Strength Check (Option 3)
            # Check the ATM Call/Put price performance against expected decay
            atm_strike = min(opt_analysis.keys(), key=lambda x: abs(x - spot_price))
            atm_opt = opt_analysis[atm_strike].get("CE" if direction == "BULLISH" else "PE", {})
            if atm_opt.get("price_change_pct", 0) > -5.0:  # Decayed less than 5% or gained
                premium_strength = "STRONG (Low Decay)"
            else:
                premium_strength = "WEAK (High Decay)"
                
            # Build detailed ITM, ATM, OTM Option parameter breakdown
            strikes = sorted(opts_df["strike"].unique())
            strike_step = 100
            if len(strikes) > 1:
                strike_step = strikes[1] - strikes[0]
                
            itm_strike = atm_strike - strike_step if direction == "BULLISH" else atm_strike + strike_step
            otm_strike = atm_strike + strike_step if direction == "BULLISH" else atm_strike - strike_step
            
            opt_lines = []
            for s in [itm_strike, atm_strike, otm_strike]:
                if s not in opt_analysis:
                    continue
                for o_type in ["CE", "PE"]:
                    o_data = opt_analysis[s].get(o_type, {})
                    if not o_data:
                        continue
                    iv_chg = o_data.get("iv_change_pct", 0.0)
                    p_chg = o_data.get("price_change_pct", 0.0)
                    oi_chg = o_data.get("oi_change_pct", 0.0)
                    
                    dom = "STABLE"
                    if iv_chg > 0 and p_chg > 0:
                        dom = "BUYER DOM"
                    elif iv_chg > 0 and p_chg < 0:
                        dom = "WRITER DOM"
                    elif iv_chg < 0 and p_chg > 0:
                        dom = "SHORT COVER"
                    elif iv_chg < 0 and p_chg < 0:
                        dom = "UNWINDING"
                        
                    type_label = "ATM" if s == atm_strike else ("ITM" if (o_type == "CE" and s < atm_strike) or (o_type == "PE" and s > atm_strike) else "OTM")
                    opt_lines.append(
                        f"• {int(s)} {o_type} ({type_label}): "
                        f"OI={fmt_oi(o_data.get('oi', 0))} ({oi_chg:+.1f}%), "
                        f"IV={o_data.get('iv', 0.0):.1f}% ({iv_chg:+.1f}%), "
                        f"LTP={o_data.get('ltp', 0.0):.1f} ({p_chg:+.1f}%) | *{dom}*"
                    )
            options_summary_msg = "\n" + "\n".join(opt_lines)
                
    # Spot vs Future Volume Ratio (Option 2)
    fut_quote = get_underlying_data(kite, symbol)
    spot_symbol = f"NSE:{name}" if name != "BANKNIFTY" else "NSE:NIFTY BANK"
    spot_quote = get_underlying_data(kite, spot_symbol)
    
    vol_ratio_str = "N/A"
    if fut_quote and spot_quote:
        fut_vol = fut_quote.get("volume", 0)
        spot_vol = spot_quote.get("volume", 0)
        ratio = spot_vol / max(1, fut_vol)
        vol_ratio_str = f"{ratio:.2f} (Spot/Fut)"
        
    # Future OI change calculation
    fut_oi_change_pct = 0.0
    if fut_quote:
        curr_oi = fut_quote.get("oi", 0)
        oi_start = spike_info.get("oi_start", curr_oi)
        if oi_start > 0:
            fut_oi_change_pct = ((curr_oi - oi_start) / oi_start) * 100
        
    # Index sector stock confirmation
    aligned_count = 5
    is_sector_aligned = True
    if name == "BANKNIFTY":
        spike_banks = spike_info.get("bank_snapshots", {})
        bank_status, is_sector_aligned, aligned_count = get_bank_consolidation_status(kite, direction, spike_banks)

    # Divergence warnings
    warnings = []
    if not is_sector_aligned:
        warnings.append("Sector Divergence: Less than 3/5 major banking stocks align.")
    if premium_strength == "WEAK (High Decay)" and is_delayed:
        warnings.append("Weak Options Hold: Premium decayed heavily during consolidation.")
    if fut_oi_change_pct < -0.5:
        warnings.append(f"Weak Future OI: Future OI dropped by {fut_oi_change_pct:+.2f}% (indicates unwinding).")
        
    # FINAL SIGNAL DECISION
    if warnings:
        print(f"[{symbol}] Avoid Trade: Fake Breakout/Divergence detected: {warnings}. Silent mode (No Telegram Alert sent).")
        with state_lock:
            active_spikes.pop(symbol, None)
        return
        
    trade_side = "CALL" if direction == "BULLISH" else "PUT"
    signal_type = f"🟢 TAKE TRADE: {direction} ({trade_side} SIDE)"
    status_check = "✅ Setup Confirmed - No Divergence"

    # Generate message
    msg = (
        f"🚨 *VOLUME SPIKE BREAKOUT DETECTED* 🚨\n"
        f"Asset: {symbol}\n"
        f"Price: {trigger_price:.2f}\n"
        f"Signal: *{signal_type}*\n"
        f"Consolidation: {minutes_elapsed:.1f} mins\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 *VALIDATION SUMMARY*\n"
        f"• Future OI Change: {fut_oi_change_pct:+.2f}%\n"
        f"• Vol Ratio: {vol_ratio_str}\n"
        f"• Premium: {premium_strength}\n"
        f"• PCR Shift: {pcr_shift:.2f}\n"
        f"• Banks Aligned: {aligned_count}/5\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🛡️ *STATUS CHECK*\n"
        f"{status_check}\n"
        f"TIME: {now.strftime('%H:%M:%S')} IST"
    )
    
    # Dispatch alert
    print(f"[{symbol}] Sending breakout alert!")
    send_telegram_message(msg, chat_id=env_config.TELE_CHAT_ID_STOCKS, token=env_config.TELE_TOKEN_STOCKS)
    
    # Clear active spike
    with state_lock:
        active_spikes.pop(symbol, None)

def process_closed_1m_candle(kite, symbol, candle):
    name = symbol.split(":", 1)[1] if ":" in symbol else symbol
    
    # Fetch lot size
    lot_size = symbol_lot_sizes.get(name, 1)
    
    volume = float(candle.get("volume", 0) or 0)
    vol_lots = volume / lot_size
    
    # Fetch current OI for calculating RoC
    current_oi = float(candle.get("oi", 0) or 0)
    
    history = candle_history[symbol]
    history.append(candle)
    if len(history) > 30:
        history.pop(0)
        
    if len(history) < 2:
        return
        
    prev_oi = float(history[-2].get("oi", 0) or 0)
    if prev_oi <= 0:
        return
        
    oi_roc = ((current_oi - prev_oi) / prev_oi) * 100
    
    if vol_lots >= VOLUME_LOT_THRESHOLD and abs(oi_roc) >= OI_ROC_THRESHOLD:
        High = float(candle.get("high", 0.0))
        Low = float(candle.get("low", 0.0))
        
        # Save snapshot of top 5 banks at spike time
        bank_snapshots = {}
        if name == "BANKNIFTY":
            for bank in ["HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "KOTAKBANK"]:
                fut_symbol = spot_future_map.get(bank)
                if fut_symbol:
                    fut_quote = get_underlying_data(kite, fut_symbol)
                    if fut_quote:
                        bank_snapshots[bank] = {
                            "vol": fut_quote.get("volume", 0),
                            "oi": fut_quote.get("oi", 0),
                            "price": fut_quote.get("last_price", 0.0)
                        }

        # Save reference levels
        with state_lock:
            active_spikes[symbol] = {
                "name": name,
                "high": High,
                "low": Low,
                "time": datetime.datetime.now(IST),
                "oi_start": current_oi,
                "bank_snapshots": bank_snapshots
            }
            
        print(f"[{symbol}] Spike Detected! High: {High}, Low: {Low}, Vol Lots: {vol_lots:.1f}, OI RoC: {oi_roc:.2f}%")
        
        # Format and send notification
        alert_msg = (
            f"🔔 *VOLUME SPIKE LEVEL MARKED* 🔔\n"
            f"Asset: {symbol}\n"
            f"Spike High: {High:.2f}\n"
            f"Spike Low: {Low:.2f}\n"
            f"Vol (Lots): {format_volume(volume)} ({vol_lots:.0f} L)\n"
            f"OI RoC: {oi_roc:+.2f}%\n"
            f"TIME: {datetime.datetime.now(IST).strftime('%H:%M:%S')} IST"
        )
        send_telegram_message(alert_msg, chat_id=env_config.TELE_CHAT_ID_STOCKS, token=env_config.TELE_TOKEN_STOCKS)

def monitor_breakout_ticks(kite, symbol, ltp):
    # Check if we have an active spike level registered
    with state_lock:
        spike_info = active_spikes.get(symbol)
        
    if not spike_info:
        return
        
    # Check for breakouts
    if ltp > spike_info["high"]:
        # Bullish Breakout
        threading.Thread(
            target=validate_and_alert_breakout,
            args=(kite, symbol, "BULLISH", ltp),
            daemon=True
        ).start()
    elif ltp < spike_info["low"]:
        # Bearish Breakout
        threading.Thread(
            target=validate_and_alert_breakout,
            args=(kite, symbol, "BEARISH", ltp),
            daemon=True
        ).start()

def start_vol_spike_breakout_scanner():
    print("Initializing Volume Spike Breakout Scanner...")
    
    token = load_access_token()
    if not token:
        print("No access token found! Scanner cannot start.")
        return
        
    # Init Kite Connection
    try:
        kite = KiteConnect(api_key=env_config.API_KEY)
        kite.set_access_token(token)
    except Exception as e:
        print("Kite initialization failed:", e)
        return
        
    df = load_instruments_data()
    if df.empty:
        return
        
    # Setup mapping for Spot/Future and Lot Sizes
    global symbol_lot_sizes, spot_future_map, option_metadata
    target_tokens = []
    token_symbol_map = {}
    
    for name in WATCHLIST:
        futs = df[(df["name"] == name) & (df["instrument_type"] == "FUT")].sort_values(by="expiry")
        if futs.empty:
            continue
            
        fut = futs.iloc[0]
        lot_size = int(fut.get("lot_size", 1))
        symbol_lot_sizes[name] = lot_size
        
        exchange = fut.get("exchange", "NFO")
        fut_symbol = f"{exchange}:{fut['tradingsymbol']}"
        spot_future_map[name] = fut_symbol
        
        fut_tkn = int(fut["instrument_token"])
        target_tokens.append(fut_tkn)
        token_symbol_map[fut_tkn] = fut["tradingsymbol"]
        
        # Spot mapping
        spots = df[df["tradingsymbol"] == (name if name != "NIFTY BANK" else "NIFTY BANK")]
        if not spots.empty:
            spot_token = int(spots.iloc[0]["instrument_token"])
            target_tokens.append(spot_token)
            token_symbol_map[spot_token] = name
            
        # Get ATM +- 5 options metadata
        # Resolve initial spot price
        init_quote = get_underlying_data(kite, fut_symbol)
        if init_quote:
            spot_price = float(init_quote.get("last_price", 0.0))
            if spot_price > 0:
                opts = get_option_contracts(df, name, spot_price)
                if not opts.empty:
                    option_metadata[name] = opts
                    
    print(f"Tracking {len(target_tokens)} Spot/Future instruments for spikes.")
    
    # State mapping for live candle building
    current_minute = {}
    minute_candles = {}
    
    def on_ticks(ws, ticks):
        now = datetime.datetime.now(IST)
        minute_str = now.strftime("%Y-%m-%d %H:%M")
        
        for tick in ticks:
            tkn = tick["instrument_token"]
            if tkn not in token_symbol_map:
                continue

            ltp = tick["last_price"]
            vol = tick.get("volume_traded") or tick.get("volume", 0)
            oi = tick.get("oi", 0)
            sym_name = token_symbol_map[tkn]
            
            # Run live breakout checks
            monitor_breakout_ticks(kite, sym_name, ltp)
            
            # Group into 1-minute candles
            if tkn not in current_minute:
                current_minute[tkn] = minute_str
                minute_candles[tkn] = {
                    "open": ltp, "high": ltp, "low": ltp, "close": ltp, "volume": vol, "oi": oi
                }
                
            if current_minute[tkn] != minute_str:
                # Candle completed!
                closed_candle = minute_candles[tkn]
                process_closed_1m_candle(kite, sym_name, closed_candle)
                
                # Reset for next minute
                current_minute[tkn] = minute_str
                minute_candles[tkn] = {
                    "open": ltp, "high": ltp, "low": ltp, "close": ltp, "volume": vol, "oi": oi
                }
            else:
                # Update current minute candle
                c = minute_candles[tkn]
                c["close"] = ltp
                c["high"] = max(c["high"], ltp)
                c["low"] = min(c["low"], ltp)
                c["volume"] = vol
                c["oi"] = oi
                
    def on_connect(ws, response):
        print("Breakout Scanner subscribing to tokens...")
        add_shared_tokens(target_tokens)
        
    register_ws_callbacks(on_connect, on_ticks)
    print("Volume Spike Breakout Scanner registered successfully.")

if __name__ == "__main__":
    start_vol_spike_breakout_scanner()
    while True:
        time.sleep(1)
