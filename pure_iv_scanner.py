import time
import math
import collections
from datetime import datetime
import pandas as pd
from zoneinfo import ZoneInfo
from kiteconnect import KiteTicker
import threading

# Import your existing credentials and functions
from env_config import API_KEY, TELE_TOKEN_BN, TELE_CHAT_ID_BN
from telegram_utils import send_telegram_message
from iv_engine import calculate_iv

def _get_time_to_expiry_years(expiry_date):
    """Calculate T in years from now until 15:30 IST on expiry date."""
    now = datetime.now()
    if hasattr(expiry_date, "date") and not isinstance(expiry_date, datetime):
        expiry = datetime.combine(expiry_date, datetime.min.time())
    else:
        expiry = expiry_date
    if isinstance(expiry, datetime):
        expiry = expiry.replace(hour=15, minute=30, second=0, microsecond=0)
    diff = (expiry - now).total_seconds()
    return max(diff / (365 * 24 * 3600), 0.00001)

# TARGETS
TARGET_SYMBOLS = [
    "BANKNIFTY", "CRUDEOIL", "CRUDEOILM",
    "360ONE", "ADANIENSOL", "ADANIENT", "ADANIGREEN", "ADANIPORTS",
    "APLAPOLLO", "ASIANPAINT", "ASTRAL", "AUROPHARMA", "AXISBANK",
    "ABB", "BAJAJ-AUTO", "BAJAJFINSV", "BAJFINANCE", "BDL",
    "BHARATFORG", "BHARTIARTL", "BLUESTARCO", "BSE", "BRITANNIA", "CDSL",
    "CGPOWER", "CHOLAFIN", "CIPLA", "COCHINSHIP", "COFORGE",
    "COLPAL", "CUMMINSIND", "DALBHARAT", "DMART", "DIVISLAB",
    "DRREDDY", "EICHERMOT", "GLENMARK", "GODFRYPHLP", "GODREJCP",
    "GODREJPROP", "GRASIM", "GVT&D", "HAL", "HAVELLS",
    "HCLTECH", "HDFCAMC", "HDFCBANK", "HEROMOTOCO", "HINDALCO",
    "HINDUNILVR", "HYUNDAI", "ICICIBANK", "ICICIGI", "INDUSINDBK",
    "JINDALSTEL", "JSWSTEEL", "KAYNES", "KPITTECH", "LAURUSLABS",
    "LODHA", "LT", "LTM", "LUPIN", "M&M",
    "MANKIND", "MARUTI", "MAXHEALTH", "MAZDOCK", "MCX",
    "MFSL", "MOTILALOFS", "MPHASIS", "MUTHOOTFIN", "NAM-INDIA",
    "NAUKRI", "NESTLEIND", "OBEROIRLTY", "OFSS", "PAYTM",
    "PERSISTENT", "PHOENIXLTD", "PIIND", "PNBHOUSING", "POLICYBZR",
    "PRESTIGE", "RADICO", "RELIANCE", "SBICARD", "SBILIFE",
    "SBIN", "SHRIRAMFIN", "SHREECEM", "SIEMENS", "SRF",
    "SUNPHARMA", "SUPREMEIND", "TATACONSUM", "TATAELXSI", "TCS",
    "TECHM", "TIINDIA", "TITAN", "TORNTPHARM", "TRENT",
    "TVSMOTOR", "UNITDSPR", "ULTRACEMCO", "UNOMINDA", "VOLTAS"
]

# State Management
snapshots = collections.defaultdict(dict)
iv_state = collections.defaultdict(dict)
symbol_metadata = {}
spot_prices = {}

def get_atm_and_itm_strikes(spot_price, strike_step=50, num_itm=10):
    """Calculate ATM and the surrounding strikes in both directions"""
    atm_strike = round(spot_price / strike_step) * strike_step
    
    # CE and PE strikes are exactly the same now! 
    # We go from -num_itm to +num_itm so ATM is perfectly in the middle.
    strikes = [atm_strike + (i * strike_step) for i in range(-num_itm, num_itm + 1)]
    
    return strikes, strikes

def process_pure_iv_pairs(symbol_base, strike, expiry, spot_price, ce_ltp, pe_ltp):
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    current_minute = now.strftime("%Y-%m-%d %H:%M")
    
    # Calculate IV for both CE and PE
    T = _get_time_to_expiry_years(expiry)
    iv_ce = calculate_iv(ce_ltp, spot_price, strike, T, option_type="CE")
    iv_pe = calculate_iv(pe_ltp, spot_price, strike, T, option_type="PE")
    
    # Use a unique key for the pair
    pair_key = f"{symbol_base}_{strike}_{expiry.strftime('%Y-%m-%d')}"
    state = iv_state[pair_key]
    
    # 1-Minute ROC logic
    if state.get("minute") != current_minute:
        if "minute" in state:
            prev_iv_ce = state.get("open_iv_ce", iv_ce)
            prev_iv_pe = state.get("open_iv_pe", iv_pe)
            
            # Fetch the previous minute's close from state
            close_iv_ce = state.get("close_iv_ce", iv_ce)
            close_iv_pe = state.get("close_iv_pe", iv_pe)
            
            # Calculate ROC in percentage terms (multiply by 100)
            # We measure the change from the previous minute's open to the previous minute's close
            roc_ce = (close_iv_ce - prev_iv_ce) * 100
            roc_pe = (close_iv_pe - prev_iv_pe) * 100
            
            state["roc_ce"] = roc_ce
            state["roc_pe"] = roc_pe
            
            # Individual spike alerts (> 10%) have been removed as per request.
            # Now relying entirely on the 1-minute summary tables.

        state["minute"] = current_minute
        state["open_iv_ce"] = iv_ce
        state["open_iv_pe"] = iv_pe
        
    state["close_iv_ce"] = iv_ce
    state["close_iv_pe"] = iv_pe

import os

def load_access_token():
    token = os.getenv("KITE_ACCESS_TOKEN", "").strip()
    if not token and os.path.exists("access_token.txt"):
        with open("access_token.txt", "r") as f:
            token = f.read().strip()
    return token

def load_instruments():
    if not os.path.exists("instruments.csv"):
        print("instruments.csv not found! Please ensure main app has downloaded it.")
        return pd.DataFrame()
    return pd.read_csv("instruments.csv")

def start_pure_iv_scanner():
    print("Starting Pure IV Scanner (>10 Logic)...")
    
    token = load_access_token()
    if not token:
        print("No access token found! Scanner cannot start.")
        return
        
    df = load_instruments()
    if df.empty:
        return
        
    # We only care about active targets
    df = df[df["name"].isin(TARGET_SYMBOLS)]
    
    # Map for easy lookup
    token_to_instrument = {}
    
    # 1. Find the Futures to get the spot price (we use Future price as spot for IV)
    # We will subscribe to futures to keep the spot price updated
    future_tokens = []
    global spot_prices
    for name in TARGET_SYMBOLS:
        futs = df[(df["name"] == name) & (df["instrument_type"] == "FUT")]
        if not futs.empty:
            # Get closest expiry
            futs = futs.sort_values(by="expiry")
            fut = futs.iloc[0]
            future_tokens.append(int(fut["instrument_token"]))
            token_to_instrument[int(fut["instrument_token"])] = {
                "type": "FUT", "name": name, "symbol": fut["tradingsymbol"]
            }
            
    # 2. Start WebSocket
    kws = KiteTicker(API_KEY, token)
    
    # We will update subscriptions dynamically when Future ticks come in
    subscribed_options = set()
    
    def on_ticks(ws, ticks):
        for tick in ticks:
            token = tick["instrument_token"]
            ltp = tick["last_price"]
            inst = token_to_instrument.get(token)
            
            if not inst:
                continue
                
            if inst["type"] == "FUT":
                name = inst["name"]
                spot_prices[name] = ltp
                
                # Dynamically find ATM and 10 ITM Options based on this new spot price
                opts = df[(df["name"] == name) & (df["instrument_type"].isin(["CE", "PE"]))]
                if opts.empty: continue
                
                # Approximate strike step
                sample_strikes = sorted(opts["strike"].unique())
                if len(sample_strikes) > 1:
                    strike_step = sample_strikes[1] - sample_strikes[0]
                else:
                    strike_step = 50
                    
                # Use 10 ITM for BANKNIFTY and CRUDEOIL, 5 ITM for stocks
                num_itm = 10 if name in ["BANKNIFTY", "CRUDEOIL", "CRUDEOILM"] else 5
                ce_strikes, pe_strikes = get_atm_and_itm_strikes(ltp, strike_step, num_itm=num_itm)
                
                closest_expiry = opts["expiry"].min()
                symbol_metadata[name] = {
                    "ce_strikes": ce_strikes,
                    "pe_strikes": pe_strikes,
                    "expiry": pd.to_datetime(closest_expiry)
                }
                
                # Find exactly these options in the dataframe for the closest expiry
                closest_expiry = opts["expiry"].min()
                target_opts = opts[
                    (opts["expiry"] == closest_expiry) & 
                    (
                        ((opts["instrument_type"] == "CE") & (opts["strike"].isin(ce_strikes))) |
                        ((opts["instrument_type"] == "PE") & (opts["strike"].isin(pe_strikes)))
                    )
                ]
                
                new_tokens = set(target_opts["instrument_token"].astype(int))
                to_subscribe = new_tokens - subscribed_options
                
                if to_subscribe:
                    # Update mapping
                    for _, row in target_opts.iterrows():
                        tkn = int(row["instrument_token"])
                        token_to_instrument[tkn] = {
                            "type": row["instrument_type"],
                            "name": name,
                            "symbol": row["tradingsymbol"],
                            "strike": row["strike"],
                            "expiry": pd.to_datetime(row["expiry"])
                        }
                    ws.subscribe(list(to_subscribe))
                    ws.set_mode(ws.MODE_QUOTE, list(to_subscribe))
                    subscribed_options.update(to_subscribe)
                    
            elif inst["type"] in ["CE", "PE"]:
                name = inst["name"]
                spot = spot_prices.get(name)
                if not spot: continue
                
                # In this simplified pairs logic, we actually need the opposite option's LTP.
                # To perfectly replicate process_pure_iv_pairs, we need both CE and PE LTPs.
                # But since ticks arrive individually, we will store them and process them on the fly!
                state = iv_state[f"{name}_{inst['strike']}_{inst['expiry'].strftime('%Y-%m-%d')}"]
                
                if inst["type"] == "CE":
                    state["ce_ltp"] = ltp
                else:
                    state["pe_ltp"] = ltp
                    
                ce_ltp = state.get("ce_ltp")
                pe_ltp = state.get("pe_ltp")
                
                if ce_ltp and pe_ltp:
                    # We have both prices! Run the math.
                    process_pure_iv_pairs(
                        name, inst["strike"], inst["expiry"], spot, ce_ltp, pe_ltp
                    )

    def on_connect(ws, response):
        print("Connected to WebSocket. Subscribing to Futures...")
        if future_tokens:
            ws.subscribe(future_tokens)
            ws.set_mode(ws.MODE_QUOTE, future_tokens)

    def on_close(ws, code, reason):
        print(f"WebSocket closed: {code} - {reason}")

    def reporting_loop():
        last_reported = None
        while True:
            time.sleep(1)
            now = datetime.now(ZoneInfo("Asia/Kolkata"))
            current_minute = now.strftime("%Y-%m-%d %H:%M")
            
            # Fire at the 02-second mark of each new minute
            if now.second == 2 and current_minute != last_reported:
                last_reported = current_minute
                
                for name, meta in symbol_metadata.items():
                    # For now, let's only log targets that are actively ticked and have ROC data
                    spot = spot_prices.get(name, 0)
                    if not spot: continue
                    
                    ce_s = meta["ce_strikes"]
                    pe_s = meta["pe_strikes"]
                    expiry = meta["expiry"]
                    
                    # We need at least 9 strikes to do -4 to +4
                    if len(ce_s) < 9:
                        continue
                        
                    def get_roc(strike, opt_type):
                        key = f"{name}_{strike}_{expiry.strftime('%Y-%m-%d')}"
                        return iv_state[key].get(f"roc_{opt_type.lower()}", 0.0)
                        
                    atm_idx = len(ce_s) // 2
                    target_strikes = ce_s[atm_idx-4 : atm_idx+5]
                    
                    ce_rocs = [get_roc(s, "CE") for s in target_strikes]
                    pe_rocs = [get_roc(s, "PE") for s in target_strikes]
                    
                    # Volatility Filter Logic
                    threshold = 0.5 if name in ["CRUDEOIL", "CRUDEOILM"] else 3.0
                    
                    has_spike = False
                    
                    # Check CE ITM & ATM (Indices 0 to 4)
                    for i in range(5):
                        if abs(ce_rocs[i]) >= threshold:
                            has_spike = True
                            break
                            
                    # Check PE ATM & ITM (Indices 4 to 8)
                    if not has_spike:
                        for i in range(4, 9):
                            if abs(pe_rocs[i]) >= threshold:
                                has_spike = True
                                break
                                
                    if not has_spike:
                        continue
                        
                    def f(v): return f"{v:5.1f}"
                    
                    msg =  f"```\n"
                    msg += f"🏦 {name} ({int(spot)})\n"
                    msg += f" Strike |  CE % |  PE %\n"
                    msg += f"--------+-------+-------\n"
                    
                    for i in range(4):
                        msg += f" {int(target_strikes[i]):<6} | {f(ce_rocs[i])} | {f(pe_rocs[i])}\n"
                        
                    msg += f"--------+-------+-------\n"
                    msg += f"🎯{int(target_strikes[4])}🎯| {f(ce_rocs[4])} | {f(pe_rocs[4])}\n"
                    msg += f"--------+-------+-------\n"
                    
                    for i in range(5, 9):
                        msg += f" {int(target_strikes[i]):<6} | {f(ce_rocs[i])} | {f(pe_rocs[i])}\n"
                        
                    ce_total = sum(ce_rocs)
                    pe_total = sum(pe_rocs)
                    
                    msg += f"--------+-------+-------\n"
                    msg += f"  TOTAL | {f(ce_total)} | {f(pe_total)}\n"
                    msg += f"```"
                    
                    print(f"Reporting per-minute IV for {name}")
                    send_telegram_message(msg, chat_id=TELE_CHAT_ID_BN, token=TELE_TOKEN_BN)

    # Start reporter thread
    threading.Thread(target=reporting_loop, daemon=True).start()

    kws.on_ticks = on_ticks
    kws.on_connect = on_connect
    kws.on_close = on_close

    print("Connecting to Zerodha WebSocket...")
    kws.connect(threaded=False)

if __name__ == "__main__":
    start_pure_iv_scanner()
