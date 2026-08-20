import os
import time
import threading
from datetime import datetime
import pandas as pd
from zoneinfo import ZoneInfo
from kiteconnect import KiteTicker

# Set up local timezone
IST = ZoneInfo("Asia/Kolkata")

def load_access_token():
    token = os.getenv("KITE_ACCESS_TOKEN", "").strip()
    if not token and os.path.exists("access_token.txt"):
        with open("access_token.txt", "r") as f:
            token = f.read().strip()
    return token

def load_instruments():
    if not os.path.exists("instruments.csv"):
        print("instruments.csv not found!")
        return pd.DataFrame()
    return pd.read_csv("instruments.csv")

def start_spot_volume_scanner():
    print("Starting Spot Volume Scanner (3rd WebSocket)...")
    
    # Need API keys from env_config
    import env_config
    from heatmap_engine import STOCK_BURST_NAMES, INDEX_BURST_NAMES, MCX_BURST_NAMES
    from kiteconnect import KiteConnect
    
    token = load_access_token()
    if not token:
        print("No access token found! Spot Volume Scanner cannot start.")
        return
        
    try:
        kite = KiteConnect(api_key=env_config.API_KEY)
        kite.set_access_token(token)
    except Exception as e:
        print("Failed to initialize Kite for Spot Scanner:", e)
        kite = None
        
    df = load_instruments()
    if df.empty:
        return

    # Prepare targets
    target_tokens = []
    symbol_metadata = {}
    
    # 1. Stocks: We want BOTH SPOT and FUTURE
    for name in STOCK_BURST_NAMES:
        futs = df[(df["name"] == name) & (df["instrument_type"] == "FUT")]
        if futs.empty: continue
        futs = futs.sort_values(by="expiry")
        fut = futs.iloc[0]
        lot_size = int(fut.get("lot_size", 1))
        fut_tkn = int(fut["instrument_token"])
        target_tokens.append(fut_tkn)
        
        spot_tkn = None
        spots = df[(df["tradingsymbol"] == name) & (df["segment"] == "NSE")]
        if not spots.empty:
            spot = spots.iloc[0]
            spot_tkn = int(spot["instrument_token"])
            target_tokens.append(spot_tkn)
            
        opts = df[(df["name"] == name) & (df["instrument_type"].isin(["CE", "PE"]))]
        strike_step = 50
        opts_df = pd.DataFrame()
        if not opts.empty:
            sample_strikes = sorted(opts["strike"].unique())
            strike_step = sample_strikes[1] - sample_strikes[0] if len(sample_strikes) > 1 else 50
            closest_expiry = opts["expiry"].min()
            opts_df = opts[opts["expiry"] == closest_expiry]
            
        symbol_metadata[name] = {
            "spot_tkn": spot_tkn,
            "fut_tkn": fut_tkn,
            "lot_size": lot_size,
            "is_mcx": False,
            "is_stock": True,
            "symbol": fut["tradingsymbol"],
            "strike_step": strike_step,
            "opts_df": opts_df
        }
            
    # 2. Indices (BankNifty): We want FUTURE only
    for name in INDEX_BURST_NAMES:
        futs = df[(df["name"] == name) & (df["instrument_type"] == "FUT")]
        if not futs.empty:
            futs = futs.sort_values(by="expiry")
            fut = futs.iloc[0]
            lot_size = int(fut.get("lot_size", 1))
            tkn = int(fut["instrument_token"])
            target_tokens.append(tkn)
            opts = df[(df["name"] == name) & (df["instrument_type"].isin(["CE", "PE"]))]
            strike_step = 50
            opts_df = pd.DataFrame()
            if not opts.empty:
                sample_strikes = sorted(opts["strike"].unique())
                strike_step = sample_strikes[1] - sample_strikes[0] if len(sample_strikes) > 1 else 50
                closest_expiry = opts["expiry"].min()
                opts_df = opts[opts["expiry"] == closest_expiry]
                
            symbol_metadata[name] = {
                "spot_tkn": None,
                "fut_tkn": tkn,
                "lot_size": lot_size,
                "is_mcx": False,
                "is_stock": False,
                "symbol": fut["tradingsymbol"],
                "strike_step": strike_step,
                "opts_df": opts_df
            }
            
    # 3. Commodities (CRUDEOILM): We want FUTURE only
    for name in MCX_BURST_NAMES:
        futs = df[(df["name"] == name) & (df["instrument_type"] == "FUT")]
        if not futs.empty:
            futs = futs.sort_values(by="expiry")
            fut = futs.iloc[0]
            lot_size = int(fut.get("lot_size", 1))
            tkn = int(fut["instrument_token"])
            target_tokens.append(tkn)
            symbol_metadata[name] = {
                "spot_tkn": None,
                "fut_tkn": tkn,
                "lot_size": lot_size,
                "is_mcx": True,
                "is_stock": False,
                "symbol": fut["tradingsymbol"]
            }

    if not target_tokens:
        print("No targets found for Spot Volume Scanner.")
        return

    print(f"Spot Volume Scanner tracking {len(target_tokens)} instruments.")

    # State tracking
    state_lock = threading.Lock()
    candle_state = {}

    def reset_candle_state(tkn, current_vol):
        candle_state[tkn] = {
            "start_vol": current_vol,
            "high": -1.0,
            "low": float('inf'),
            "close": -1.0
        }

    from websocket_flow import register_ws_callbacks, add_shared_tokens

    def on_ticks(ws, ticks):
        with state_lock:
            for tick in ticks:
                tkn = tick["instrument_token"]
                ltp = tick["last_price"]
                
                vol = tick.get("volume_traded") or tick.get("volume", 0)
                oi = tick.get("oi", 0)
                
                if tkn not in candle_state:
                    reset_candle_state(tkn, vol)
                
                c_state = candle_state[tkn]
                c_state["close"] = ltp
                if c_state["high"] == -1.0 or ltp > c_state["high"]:
                    c_state["high"] = ltp
                if c_state["low"] == float('inf') or ltp < c_state["low"]:
                    c_state["low"] = ltp
                c_state["current_vol"] = vol
                c_state["oi"] = oi

    def on_connect(ws, response):
        print("Spot Volume Scanner subscribing...")
        add_shared_tokens(target_tokens)

    def on_close(ws, code, reason):
        print(f"Spot Volume WebSocket closed: {code} - {reason}")

    register_ws_callbacks(on_connect, on_ticks)

    # Reporting Loop
    def reporting_loop():
        from telegram_utils import send_telegram_message
        
        msg = f"ðŸŸ¢ Spot Volume Scanner (3rd WebSocket) Started Successfully!\nTracking {len(target_tokens)} Spot/Future instruments."
        
        try:
            send_telegram_message(msg, chat_id=env_config.TELE_CHAT_ID, token=env_config.TELE_TOKEN)
        except Exception as e:
            print(f"Error sending startup message: {e}")
        
        last_reported_minute = None
        
        while True:
            time.sleep(0.5)
            now = datetime.now(IST)
            
            # Silent mode on weekends
            if now.weekday() > 4:
                time.sleep(60)
                continue
                
            t = now.time()
            is_nse_holiday = now.date().isoformat() in env_config.NSE_HOLIDAYS
            is_nse_open = datetime.strptime("09:00", "%H:%M").time() <= t <= datetime.strptime("15:30", "%H:%M").time() and not is_nse_holiday
            is_mcx_open = datetime.strptime("15:30", "%H:%M").time() <= t <= datetime.strptime("23:30", "%H:%M").time()
            
            if not is_nse_open and not is_mcx_open:
                time.sleep(60)
                continue
                
            current_minute = now.strftime("%Y-%m-%d %H:%M")
            
            # Fire at the 02-second mark of each new minute
            if now.second >= 2 and current_minute != last_reported_minute:
                last_reported_minute = current_minute
                
                alerts = []
                
                def format_vol(v):
                    if v >= 1_000_000:
                        val = v / 1_000_000
                        return f"{int(val)}M" if val.is_integer() else f"{val:.1f}M"
                    elif v >= 1_000:
                        val = v / 1_000
                        return f"{int(val)}K" if val.is_integer() else f"{val:.1f}K"
                    return str(int(v))
                    
                with state_lock:
                    for name, meta in symbol_metadata.items():
                        is_mcx = meta["is_mcx"]
                        if is_mcx and not is_mcx_open:
                            continue
                        if not is_mcx and not is_nse_open:
                            # Reset states
                            for tkn in [meta["spot_tkn"], meta["fut_tkn"]]:
                                if tkn and tkn in candle_state:
                                    reset_candle_state(tkn, candle_state[tkn].get("current_vol", 0))
                            continue
                            
                        spot_tkn = meta["spot_tkn"]
                        fut_tkn = meta["fut_tkn"]
                        lot_size = meta["lot_size"]
                        
                        spot_state = candle_state.get(spot_tkn) if spot_tkn else None
                        fut_state = candle_state.get(fut_tkn) if fut_tkn else None
                        
                        spot_vol = 0
                        spot_lots = 0
                        fut_vol = 0
                        fut_lots = 0
                        
                        spot_valid = spot_state and spot_state["high"] != -1.0
                        fut_valid = fut_state and fut_state["high"] != -1.0
                        
                        if spot_valid:
                            spot_vol = max(0, spot_state.get("current_vol", 0) - spot_state.get("start_vol", 0))
                            spot_lots = int(spot_vol / lot_size)
                            
                        if fut_valid:
                            fut_vol = max(0, fut_state.get("current_vol", 0) - fut_state.get("start_vol", 0))
                            fut_lots = int(fut_vol / lot_size)
                            
                        if spot_lots >= 500 or fut_lots >= 500:
                            oi_table = ""
                            ref_price = 0
                            
                            if meta["is_stock"] and spot_tkn:
                                s_high = spot_state["high"] if spot_valid else 0
                                s_low = spot_state["low"] if spot_valid else 0
                                s_close = spot_state["close"] if spot_valid else 0
                                s_mid = (s_high - s_low) / 2.0 if spot_valid else 0
                                buy_price = s_low + s_mid if spot_valid else 0
                                ref_price = buy_price
                                
                                f_high = fut_state["high"] if fut_valid else 0
                                f_low = fut_state["low"] if fut_valid else 0
                                f_close = fut_state["close"] if fut_valid else 0
                                f_mid = (f_high - f_low) / 2.0 if fut_valid else 0
                                fut_oi = fut_state.get("oi", 0) if fut_state else 0
                                
                                msg = (
                                    f"Symbol: {meta['symbol']} ({lot_size} lots)\n"
                                    f"S-V(L): {format_vol(spot_vol)}({spot_lots} L) & F-V(L): {format_vol(fut_vol)}({fut_lots} L)\n"
                                    f"S-Price: {s_close:.2f} F-Price: {f_close:.2f}\n"
                                    f"S-Candle C: {s_mid:.2f} FC: {f_mid:.2f}\n"
                                    f"Buying Price: {buy_price:.2f}\n"
                                )
                            else:
                                price_source = spot_state if spot_valid else fut_state
                                c_high = price_source["high"]
                                c_low = price_source["low"]
                                c_close = price_source["close"]
                                c_mid = (c_high - c_low) / 2.0
                                buy_price = c_low + c_mid
                                ref_price = buy_price
                                
                                fut_oi = price_source.get("oi", 0)
                                msg = (
                                    f"Symbol: {meta['symbol']} ({lot_size} lots)\n"
                                    f"Volume(Lots): {format_vol(fut_vol)}({fut_lots} L)\n"
                                    f"Price : {c_close:.2f}\n"
                                    f"Candle C: {c_mid:.2f}\n"
                                    f"Buying price: {buy_price:.2f}\n"
                                )
                                
                            # Generate OI Table using REST API
                            if kite and ref_price > 0 and meta.get("opts_df") is not None and not meta["opts_df"].empty:
                                strike_step = meta["strike_step"]
                                atm_strike = round(ref_price / strike_step) * strike_step
                                target_strikes = [atm_strike + i * strike_step for i in range(-2, 3)]
                                
                                opts_df = meta["opts_df"]
                                relevant_opts = opts_df[opts_df["strike"].astype(float).round(2).isin(target_strikes)]
                                
                                symbols_to_quote = []
                                symbol_to_strike = {}
                                for _, row in relevant_opts.iterrows():
                                    qs = f"{row['exchange']}:{row['tradingsymbol']}"
                                    symbols_to_quote.append(qs)
                                    symbol_to_strike[qs] = {
                                        "strike": float(row["strike"]),
                                        "type": row["instrument_type"]
                                    }
                                
                                try:
                                    quotes = kite.quote(symbols_to_quote)
                                    
                                    # Structure data by strike
                                    strike_data = {s: {"CE": 0, "PE": 0} for s in target_strikes}
                                    for qs, data in quotes.items():
                                        if qs in symbol_to_strike:
                                            s = symbol_to_strike[qs]["strike"]
                                            t = symbol_to_strike[qs]["type"]
                                            strike_data[s][t] = data.get("oi", 0)
                                            
                                    def fmt_lakhs(v):
                                        if v == 0: return "0.0L"
                                        return f"{v/100000:.1f}L"
                                        
                                    max_oi = max(max(d["CE"], d["PE"]) for d in strike_data.values()) or 1
                                    max_ce = max(d["CE"] for d in strike_data.values())
                                    max_pe = max(d["PE"] for d in strike_data.values())
                                    
                                    oi_table += "\n```\n"
                                    oi_table += f"🔴 CALL OI         | STRIKE  |   PUT OI 🟢\n"
                                    oi_table += f"-------------------+---------+---------------\n"
                                    
                                    for s in target_strikes:
                                        ce_val = strike_data[s]["CE"]
                                        pe_val = strike_data[s]["PE"]
                                        ce_str = fmt_lakhs(ce_val)
                                        pe_str = fmt_lakhs(pe_val)
                                        
                                        is_max_ce = (ce_val == max_ce and ce_val > 0)
                                        is_max_pe = (pe_val == max_pe and pe_val > 0)
                                        
                                        b_ce = int(round((ce_val / max_oi) * 4)) if ce_val > 0 else 0
                                        b_ce = max(1, b_ce) if ce_val > 0 else 0
                                        
                                        b_pe = int(round((pe_val / max_oi) * 4)) if pe_val > 0 else 0
                                        b_pe = max(1, b_pe) if pe_val > 0 else 0
                                        
                                        ce_prefix = "🔥" if is_max_ce else ""
                                        ce_boxes = "🟥" * b_ce
                                        
                                        ce_used = (2 if is_max_ce else 0) + len(ce_str) + 1 + (b_ce * 2)
                                        left_spaces = max(0, 18 - ce_used)
                                        left_part = f"{ce_prefix}{ce_str} {ce_boxes}" + (" " * left_spaces)
                                        
                                        if s == atm_strike:
                                            strike_part = f"{int(s)} 🎯"
                                        else:
                                            strike_part = f"{int(s)}   "
                                            
                                        pe_boxes = "🟩" * b_pe
                                        pe_used_boxes = b_pe * 2
                                        pe_spaces = max(1, 9 - pe_used_boxes)
                                        pe_suffix = "🔥" if is_max_pe else ""
                                        
                                        right_part = f"{pe_boxes}" + (" " * pe_spaces) + f"{pe_str:>5}{pe_suffix}"
                                        
                                        oi_table += f"{left_part}| {strike_part} | {right_part}\n"
                                    oi_table += "```\n"
                                except Exception as e:
                                    print("Error fetching OI quote:", e)
                                    
                            msg += oi_table
                            msg += f"TIME: {now.strftime('%H:%M:%S')}\n"
                            alerts.append(msg)
                            
                        # Reset states for next minute
                        if spot_tkn and spot_tkn in candle_state:
                            reset_candle_state(spot_tkn, candle_state[spot_tkn].get("current_vol", 0))
                        if fut_tkn and fut_tkn in candle_state:
                            reset_candle_state(fut_tkn, candle_state[fut_tkn].get("current_vol", 0))
                        
                # Dispatch alerts
                token_stocks = os.getenv("TELE_TOKEN_STOCKS", env_config.TELE_TOKEN)
                chat_stocks = os.getenv("CHAT_ID_STOCKS", env_config.TELE_CHAT_ID)
                
                for alert in alerts:
                    try:
                        send_telegram_message(alert, chat_id=chat_stocks, token=token_stocks)
                    except Exception as e:
                        print(f"Error sending spot volume alert: {e}")

    threading.Thread(target=reporting_loop, daemon=True).start()

if __name__ == "__main__":
    start_spot_volume_scanner()
    while True:
        time.sleep(1)
