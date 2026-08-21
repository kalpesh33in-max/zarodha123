import time
import math
import collections
from datetime import datetime
import pandas as pd
from zoneinfo import ZoneInfo
from kiteconnect import KiteTicker
import threading

# Import your existing credentials and functions
from env_config import API_KEY, TELE_TOKEN, TELE_CHAT_ID
from telegram_utils import send_telegram_message

# Newton-Raphson Implied Volatility Calculations
RISK_FREE_RATE = 0.07

def norm_cdf(x):
    """Cumulative distribution function for standard normal distribution."""
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0

def norm_pdf(x):
    """Probability density function for standard normal distribution."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)

def black_scholes_price(S, K, T, r, sigma, option_type="CE"):
    """
    Calculate the Black-Scholes option price.
    S: Underlying price (Future LTP)
    K: Strike price
    T: Time to expiry in years
    r: Risk-free rate
    sigma: Implied volatility
    """
    if T <= 0 or sigma <= 0:
        return max(0.0, S - K) if option_type == "CE" else max(0.0, K - S)

    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    if option_type == "CE":
        price = S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
    else:
        price = K * math.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1)
    
    return price

def calculate_iv(target_price, S, K, T, r=RISK_FREE_RATE, option_type="CE", max_iterations=100, tolerance=1e-5):
    """
    Calculate Implied Volatility using the Newton-Raphson method.
    """
    intrinsic = max(0.0, S - K) if option_type == "CE" else max(0.0, K - S)
    if target_price <= intrinsic:
        return 0.001  # Minimum IV
    
    if T <= 0:
        return 0.001

    sigma = 0.3  # Initial guess (30% IV)
    
    for _ in range(max_iterations):
        price = black_scholes_price(S, K, T, r, sigma, option_type)
        diff = price - target_price
        
        if abs(diff) < tolerance:
            return sigma
            
        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        vega = S * norm_pdf(d1) * math.sqrt(T)
        
        if vega == 0:
            return sigma
            
        sigma -= diff / vega
        
        if sigma <= 0:
            sigma = 0.001  # Prevent negative IV
        elif sigma > 5.0:
            sigma = 5.0  # Cap maximum IV at 500% to prevent math explosion
            
    return sigma


def _get_time_to_expiry_years(expiry_date):
    """Calculate T in years from now until 15:30 IST on expiry date."""
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    if hasattr(expiry_date, "date") and not isinstance(expiry_date, datetime):
        expiry = datetime.combine(expiry_date, datetime.min.time())
    else:
        expiry = expiry_date
    
    # Target expiry time is 15:30 IST on the expiry day
    expiry_target = expiry.replace(hour=15, minute=30, second=0, microsecond=0)
    if expiry_target.tzinfo is None:
        expiry_target = expiry_target.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
        
    diff = (expiry_target - now).total_seconds()
    return max(diff / (365 * 24 * 3600), 0.00001)

# TARGETS
TARGET_SYMBOLS = [
    # Indices & Commodities
    "NIFTY", "BANKNIFTY", "CRUDEOILM",
    # Top 5 Banking Stocks
    "HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "KOTAKBANK",
    # Key Heavyweights
    "RELIANCE", "TCS", "INFY", "BHARTIARTL", "LT", "M&M", "BAJFINANCE"
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
    strikes = [round(atm_strike + (i * strike_step), 2) for i in range(-num_itm, num_itm + 1)]
    
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
    symbol_lot_sizes = {}
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
            symbol_lot_sizes[name] = int(float(fut.get("lot_size", 1)))
            
    # We will update subscriptions dynamically when Future ticks come in
    subscribed_options = set()
    
    from websocket_flow import register_ws_callbacks, add_shared_tokens
    
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
                    
                # Use 10 ITM for NIFTY, BANKNIFTY and CRUDEOILM, 5 ITM for stocks
                num_itm = 10 if name in ["NIFTY", "BANKNIFTY", "CRUDEOILM"] else 5
                ce_strikes, pe_strikes = get_atm_and_itm_strikes(ltp, strike_step, num_itm=num_itm)
                
                closest_expiry = opts["expiry"].min()
                symbol_metadata[name] = {
                    "ce_strikes": ce_strikes,
                    "pe_strikes": pe_strikes,
                    "expiry": pd.to_datetime(closest_expiry)
                }
                
                # Find exactly these options in the dataframe for the closest expiry
                closest_expiry = opts["expiry"].min()
                rounded_opts_strike = opts["strike"].astype(float).round(2)
                target_opts = opts[
                    (opts["expiry"] == closest_expiry) & 
                    (
                        ((opts["instrument_type"] == "CE") & (rounded_opts_strike.isin(ce_strikes))) |
                        ((opts["instrument_type"] == "PE") & (rounded_opts_strike.isin(pe_strikes)))
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
                            "strike": round(float(row["strike"]), 2),
                            "expiry": pd.to_datetime(row["expiry"])
                        }
                    add_shared_tokens(list(to_subscribe))
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
                    state["ce_vol_total"] = tick.get("volume_traded") or tick.get("volume", 0)
                else:
                    state["pe_ltp"] = ltp
                    state["pe_vol_total"] = tick.get("volume_traded") or tick.get("volume", 0)
                    
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
            add_shared_tokens(future_tokens)

    def on_close(ws, code, reason):
        print(f"WebSocket closed: {code} - {reason}")

    def reporting_loop():
        from env_config import NSE_HOLIDAYS
        last_reported = None
        while True:
            time.sleep(1)
            now = datetime.now(ZoneInfo("Asia/Kolkata"))
            
            if now.weekday() > 4:
                time.sleep(59)
                continue
                
            t = now.time()
            is_nse_holiday = now.date().isoformat() in NSE_HOLIDAYS
            is_nse_open = datetime.strptime("09:00", "%H:%M").time() <= t <= datetime.strptime("15:30", "%H:%M").time() and not is_nse_holiday
            is_mcx_open = datetime.strptime("15:30", "%H:%M").time() <= t <= datetime.strptime("23:30", "%H:%M").time()
            
            if not is_nse_open and not is_mcx_open:
                time.sleep(59)
                continue
                
            current_minute = now.strftime("%Y-%m-%d %H:%M")
            
            # Fire at the 02-second mark of each new minute
            if now.second == 2 and current_minute != last_reported:
                last_reported = current_minute
                
                for name, meta in symbol_metadata.items():
                    # For now, let's only log targets that are actively ticked and have ROC data
                    spot = spot_prices.get(name, 0)
                    if not spot: continue
                    
                    is_mcx = name == "CRUDEOILM"
                    if is_mcx and not is_mcx_open: continue
                    if not is_mcx and not is_nse_open: continue
                    
                    ce_s = meta["ce_strikes"]
                    pe_s = meta["pe_strikes"]
                    expiry = meta["expiry"]
                    
                    # We need at least 9 strikes to do -4 to +4
                    if len(ce_s) < 9:
                        continue
                        
                    def get_roc(strike, opt_type):
                        key = f"{name}_{strike}_{expiry.strftime('%Y-%m-%d')}"
                        return iv_state[key].get(f"roc_{opt_type.lower()}", 0.0)
                        
                    def get_1m_vol(strike, opt_type):
                        key = f"{name}_{strike}_{expiry.strftime('%Y-%m-%d')}"
                        state = iv_state[key]
                        vol_key = f"{opt_type.lower()}_vol_total"
                        prev_key = f"{opt_type.lower()}_vol_prev"
                        
                        current_vol = state.get(vol_key, 0)
                        prev_vol = state.get(prev_key, current_vol)
                        
                        one_min_vol = current_vol - prev_vol
                        state[prev_key] = current_vol
                        return max(0, one_min_vol)
                        
                    atm_idx = len(ce_s) // 2
                    target_strikes = ce_s[atm_idx-4 : atm_idx+5]
                    
                    ce_rocs = [get_roc(s, "CE") for s in target_strikes]
                    pe_rocs = [get_roc(s, "PE") for s in target_strikes]
                    
                    lot_size = symbol_lot_sizes.get(name, 1)
                    ce_vols = [int(get_1m_vol(s, "CE") / lot_size) for s in target_strikes]
                    pe_vols = [int(get_1m_vol(s, "PE") / lot_size) for s in target_strikes]
                    
                    threshold = 0.1 if name == "CRUDEOILM" else 20.0
                    
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
                        
                    def f(v): return f"{v:4.1f}"
                    
                    def fmt_vol(v):
                        if v >= 1_000_000:
                            val = v / 1_000_000
                            return f"{int(val)}M" if val.is_integer() else f"{val:.1f}M"
                        elif v >= 1_000:
                            val = v / 1_000
                            return f"{int(val)}K" if val.is_integer() else f"{val:.1f}K"
                        return str(int(v))
                    msg =  f"```\n"
                    msg += f"🏦 {name} ({int(spot)})\n"
                    msg += f" Strike | CE %| PE %| C.Lot| P.Lot\n"
                    msg += f"--------+-----+-----+------+------\n"
                    
                    for i in range(4):
                        msg += f" {int(target_strikes[i]):<6} | {f(ce_rocs[i]):>4} | {f(pe_rocs[i]):>4} | {fmt_vol(ce_vols[i]):>4} | {fmt_vol(pe_vols[i]):>4}\n"
                        
                    msg += f"--------+-----+-----+------+------\n"
                    # ATM Row
                    msg += f" {int(target_strikes[4]):<6} | {f(ce_rocs[4]):>4} | {f(pe_rocs[4]):>4} | {fmt_vol(ce_vols[4]):>4} | {fmt_vol(pe_vols[4]):>4}🎯\n"
                    msg += f"--------+-----+-----+------+------\n"
                    
                    for i in range(5, 9):
                        msg += f" {int(target_strikes[i]):<6} | {f(ce_rocs[i]):>4} | {f(pe_rocs[i]):>4} | {fmt_vol(ce_vols[i]):>4} | {fmt_vol(pe_vols[i]):>4}\n"
                        
                    ce_total = sum(ce_rocs)
                    pe_total = sum(pe_rocs)
                    ce_vtotal = sum(ce_vols)
                    pe_vtotal = sum(pe_vols)
                    
                    msg += f"--------+-----+-----+------+------\n"
                    msg += f"  TOTAL | {f(ce_total):>4} | {f(pe_total):>4} | {fmt_vol(ce_vtotal):>4} | {fmt_vol(pe_vtotal):>4}\n"
                    msg += f"```"
                    
                    print(f"Reporting per-minute IV for {name}")
                    send_telegram_message(msg, chat_id=TELE_CHAT_ID, token=TELE_TOKEN)

    # Start reporter thread
    threading.Thread(target=reporting_loop, daemon=True).start()

    register_ws_callbacks(on_connect, on_ticks)
    print("Pure IV Scanner registered with shared WebSocket.")

if __name__ == "__main__":
    start_pure_iv_scanner()
