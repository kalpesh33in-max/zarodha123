import os
import time
import math
import threading
import datetime
import pandas as pd
from zoneinfo import ZoneInfo
from kiteconnect import KiteConnect

# Import existing configs and tools
import env_config
from websocket_flow import register_ws_callbacks, add_shared_tokens, get_symbol_quotes, get_token_quotes
from telegram_utils import send_telegram_message

IST = ZoneInfo("Asia/Kolkata")

# Expry Day Router configuration
# Tuesday = NIFTY (NSE), Thursday = SENSEX (BSE)
def get_todays_expiry_instrument():
    weekday = datetime.datetime.now(IST).weekday()
    if weekday == 1:
        return "NIFTY", "NSE"
    elif weekday == 3:
        return "SENSEX", "BSE"
    else:
        return None, None

def norm_pdf(x):
    """Probability density function for standard normal distribution."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)

def calculate_gamma(spot, strike, iv_pct, minutes_to_close, r=0.07):
    """
    Computes Black-Scholes Gamma adjusted for intraday minutes left.
    """
    if minutes_to_close <= 0 or iv_pct <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    sigma = iv_pct / 100.0
    # Annualized time based on 375 trading minutes/day, 252 trading days/year
    T = minutes_to_close / (375.0 * 252.0)
    
    try:
        d1 = (math.log(spot / strike) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        gamma = norm_pdf(d1) / (spot * sigma * math.sqrt(T))
        return gamma
    except (ZeroDivisionError, ValueError):
        return 0.0

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
    return pd.read_csv("instruments.csv")

# State Management
spot_price = 0.0
option_quotes = {}
option_metadata = {}
last_gex_sign = None  # Tracks positive/negative GEX for Flip Alert
last_alert_time = {}

def start_expiry_gamma_scanner():
    print("Initializing Expiry Gamma Scanner...")
    
    name, exchange = get_todays_expiry_instrument()
    if not name:
        print("Today is not a weekly expiry day for Nifty (Tuesday) or Sensex (Thursday). Gamma Scanner Standby.")
        return
        
    token = load_access_token()
    if not token:
        print("No access token found! Gamma Scanner cannot start.")
        return
        
    try:
        kite = KiteConnect(api_key=env_config.API_KEY)
        kite.set_access_token(token)
    except Exception as e:
        print("Kite initialization failed for Gamma Scanner:", e)
        return
        
    df = load_instruments_data()
    if df.empty:
        return
        
    # Get active expiry day instrument
    # We want weekly expiry (closest active expiry which is today or upcoming today!)
    opts = df[(df["name"] == name) & (df["segment"].isin(["NFO-OPT", "BFO-OPT"]))]
    if opts.empty:
        print(f"No option contracts found for {name} today.")
        return
        
    opts = opts.copy()
    opts["expiry"] = pd.to_datetime(opts["expiry"])
    today_date = datetime.datetime.now(IST).date()
    
    # Filter active contracts on or after today so past expiries don't block
    active_opts = opts[opts["expiry"].dt.date >= today_date]
    if active_opts.empty:
        print(f"No future/today contracts found for {name}.")
        return

    closest_expiry = active_opts["expiry"].min()
    
    # Expiry validation: Must be today's 0-DTE
    if closest_expiry.date() != today_date:
        print(f"Closest active expiry for {name} is {closest_expiry.date()}, not today ({today_date}). Standby.")
        return
        
    # Get Spot token
    spot_symbol = "NSE:NIFTY 50" if name == "NIFTY" else "BSE:SENSEX"
    spot_tsym = "NIFTY 50" if name == "NIFTY" else "SENSEX"
    spots = df[df["tradingsymbol"] == spot_tsym]
    if spots.empty:
        print(f"Spot index token not found for {name}.")
        return
    spot_token = int(spots.iloc[0]["instrument_token"])
    lot_size = int(active_opts.iloc[0].get("lot_size", 10 if name == "SENSEX" else 25))
    
    print(f"Expiry Gamma Scanner started for {name} on Expiry {today_date} (Lot Size: {lot_size}). Spot Token: {spot_token}")
    
    # Track the active options strikes
    active_option_tokens = []
    token_to_strike_info = {}
    
    # Find ATM & surrounding strikes
    # Initially we fetch quotes to get initial Spot Price
    try:
        spot_quote = kite.quote([spot_symbol]).get(spot_symbol, {})
        initial_spot = float(spot_quote.get("last_price", 0.0))
    except Exception as e:
        print(f"Failed to fetch initial spot for {name}: {e}")
        initial_spot = 0.0
        
    if initial_spot <= 0:
        return
        
    strikes = sorted(active_opts["strike"].unique())
    atm_strike = min(strikes, key=lambda x: abs(x - initial_spot))
    idx = strikes.index(atm_strike)
    
    # Track ATM +/- 6 strikes (13 strikes * 2 = 26 contracts)
    # This keeps WebSocket feed lightweight, fast, and focused on active strikes
    selected_strikes = strikes[max(0, idx - 6): min(len(strikes), idx + 7)]
    expiry_opts = active_opts[(active_opts["expiry"] == closest_expiry) & (active_opts["strike"].isin(selected_strikes))]
    
    for _, row in expiry_opts.iterrows():
        tkn = int(row["instrument_token"])
        active_option_tokens.append(tkn)
        token_to_strike_info[tkn] = {
            "strike": float(row["strike"]),
            "type": row["instrument_type"],
            "symbol": row["tradingsymbol"]
        }
        
    target_tokens = [spot_token] + active_option_tokens
    
    # Webhook updates
    def on_ticks(ws, ticks):
        global spot_price, option_quotes
        for tick in ticks:
            tkn = tick["instrument_token"]
            ltp = tick["last_price"]
            if tkn == spot_token:
                spot_price = ltp
            elif tkn in token_to_strike_info:
                option_quotes[tkn] = {
                    "ltp": ltp,
                    "oi": tick.get("oi", 0),
                    "iv": tick.get("iv", 0.0) or 15.0, # default IV fallback if not provided by feed
                    "volume": tick.get("volume_traded") or tick.get("volume", 0)
                }
                
    def on_connect(ws, response):
        print("Gamma Scanner subscribing to tokens...")
        add_shared_tokens(target_tokens)
        
    register_ws_callbacks(on_connect, on_ticks)
    
    # Background analysis loop
    def evaluation_loop():
        global spot_price, last_gex_sign
        
        while True:
            time.sleep(60) # Run every minute
            now = datetime.datetime.now(IST)
            if now.weekday() > 4:
                continue
                
            # Expiry close time target is 15:30 IST
            close_time = datetime.datetime.combine(now.date(), datetime.time(15, 30), tzinfo=IST)
            minutes_left = (close_time - now).total_seconds() / 60.0
            
            if minutes_left <= 0:
                print("Market closed. Gamma Scanner paused.")
                time.sleep(300)
                continue
                
            if spot_price <= 0 or not option_quotes:
                continue
                
            # Perform calculations
            option_chain_data = {}
            for tkn, info in token_to_strike_info.items():
                quote = option_quotes.get(tkn)
                if not quote:
                    continue
                strike = info["strike"]
                opt_type = info["type"]
                
                if strike not in option_chain_data:
                    option_chain_data[strike] = {"strike": strike}
                
                option_chain_data[strike][f"{opt_type.lower()}_oi"] = quote["oi"]
                option_chain_data[strike][f"{opt_type.lower()}_iv"] = quote["iv"]
                option_chain_data[strike][f"{opt_type.lower()}_ltp"] = quote["ltp"]
                option_chain_data[strike][f"{opt_type.lower()}_vol"] = quote["volume"]
                
            total_net_gex = 0.0
            gamma_profile = []
            
            for strike, data in option_chain_data.items():
                ce_oi = data.get("ce_oi", 0)
                pe_oi = data.get("pe_oi", 0)
                ce_iv = data.get("ce_iv", 15.0)
                pe_iv = data.get("pe_iv", 15.0)
                
                ce_gamma = calculate_gamma(spot_price, strike, ce_iv, minutes_left)
                pe_gamma = calculate_gamma(spot_price, strike, pe_iv, minutes_left)
                
                call_gex = ce_oi * ce_gamma * spot_price * lot_size
                put_gex = pe_oi * pe_gamma * spot_price * lot_size
                net_gex = call_gex - put_gex
                
                total_net_gex += net_gex
                gamma_profile.append({
                    "strike": strike,
                    "call_gex": call_gex,
                    "put_gex": put_gex,
                    "net_gex": net_gex,
                    "ce_gamma": ce_gamma,
                    "pe_gamma": pe_gamma,
                    "ce_ltp": data.get("ce_ltp", 0.0),
                    "pe_ltp": data.get("pe_ltp", 0.0),
                    "ce_oi": ce_oi,
                    "pe_oi": pe_oi
                })
                
            if not gamma_profile:
                continue
                
            # Convert GEX to Cr (Crores) for Indian style representation
            gex_in_cr = total_net_gex / 10_000_000.0
            current_gex_sign = "POSITIVE" if gex_in_cr >= 0 else "NEGATIVE"
            
            # Find walls
            call_wall = max(gamma_profile, key=lambda x: x["call_gex"])
            put_wall = max(gamma_profile, key=lambda x: x["put_gex"])
            
            # --- 1. Gamma Flip Alert ---
            if last_gex_sign is not None and last_gex_sign != current_gex_sign:
                last_label = "POSITIVE(seller)" if last_gex_sign == "POSITIVE" else "NEGATIVE(buyer)"
                curr_label = "POSITIVE(seller)" if current_gex_sign == "POSITIVE" else "NEGATIVE GEX(buyer)"
                msg = (
                    f"🔄 *GAMMA FLIP DETECTED: {name} 0-DTE*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"⏰ Time: {now.strftime('%H:%M:%S')} IST\n"
                    f"Spot Price: {spot_price:.2f}\n"
                    f"Transition: {last_label} ➔ {curr_label}\n"
                    f"Current Net GEX: {gex_in_cr:+.2f} Cr\n"
                    f"Key Levels:\n"
                    f"• Upper Call Wall: {int(call_wall['strike'])} (GEX: {call_wall['call_gex']/10_000_000.0:.1f} Cr)\n"
                    f"• Lower Put Wall: {int(put_wall['strike'])} (GEX: {put_wall['put_gex']/10_000_000.0:.1f} Cr)\n"
                    f"💡 *Market Implication*: Volatility expansion expected."
                )
                send_telegram_message(msg, chat_id=env_config.TELE_CHAT_ID_BN, token=env_config.TELE_TOKEN_BN)
                
            last_gex_sign = current_gex_sign
            
            # --- 2. Gamma Wall Breakout/Rejection Alert ---
            for wall, wall_type in [(call_wall, "Call"), (put_wall, "Put")]:
                dist_pct = abs(spot_price - wall["strike"]) / spot_price * 100.0
                if dist_pct < 0.15: # spot is very close to the wall (within 0.15%)
                    alert_key = f"wall_{wall['strike']}_{wall_type}"
                    last_alert = last_alert_time.get(alert_key, 0.0)
                    if time.time() - last_alert > 600: # 10 min cooldown
                        last_alert_time[alert_key] = time.time()
                        msg = (
                            f"⚡ *GAMMA WALL APPROACH/BREACH: {name} 0-DTE*\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"⏰ Time: {now.strftime('%H:%M:%S')} IST\n"
                            f"Spot Price: {spot_price:.2f}\n"
                            f"Wall Level: {int(wall['strike'])} {wall_type} Wall\n"
                            f"Wall GEX: {wall['call_gex' if wall_type == 'Call' else 'put_gex']/10_000_000.0:.1f} Cr\n"
                            f"💡 *Market Implication*: Resistance/Support active. Watch for breakout/rejection."
                        )
                        send_telegram_message(msg, chat_id=env_config.TELE_CHAT_ID_BN, token=env_config.TELE_TOKEN_BN)
                        
            # --- 3. Hero-Zero Afternoon Gamma Spike Alert ---
            # Between 1:30 PM (13:30) and 3:00 PM (15:00)
            is_afternoon = datetime.time(13, 30) <= now.time() <= datetime.time(15, 0)
            if is_afternoon:
                max_otm_dist = 300 if name == "SENSEX" else 100
                gamma_threshold = 0.0020 if name == "SENSEX" else 0.0050

                for strike_data in gamma_profile:
                    dist = strike_data["strike"] - spot_price
                    # OTM Call: strike > spot, OTM Put: strike < spot
                    is_otm_ce = 0 < dist <= max_otm_dist
                    is_otm_pe = -max_otm_dist <= dist < 0
                    
                    if is_otm_ce and strike_data["ce_gamma"] > gamma_threshold:
                        alert_key = f"hz_ce_{strike_data['strike']}"
                        last_alert = last_alert_time.get(alert_key, 0.0)
                        if time.time() - last_alert > 900: # 15 min cooldown
                            last_alert_time[alert_key] = time.time()
                            msg = (
                                f"🔥 *0-DTE GAMMA SURGE ALERT (CALL SIDE): {name}*\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"⏰ Time: {now.strftime('%H:%M:%S')} IST\n"
                                f"Time to Expiry: {int(minutes_left)} Mins Left\n"
                                f"Spot Price: {spot_price:.2f}\n"
                                f"🎯 *Hero-Zero Strike to BUY: {int(strike_data['strike'])} CE*\n"
                                f"Premium LTP: ₹{strike_data['ce_ltp']:.2f}\n"
                                f"Strike Gamma (Γ): {strike_data['ce_gamma']:.4f}\n"
                                f"💡 *Trade Direction*: SCENARIO A (Call Gamma Surge). Small index upside will explode CE premium."
                            )
                            send_telegram_message(msg, chat_id=env_config.TELE_CHAT_ID_BN, token=env_config.TELE_TOKEN_BN)
                            
                    if is_otm_pe and strike_data["pe_gamma"] > gamma_threshold:
                        alert_key = f"hz_pe_{strike_data['strike']}"
                        last_alert = last_alert_time.get(alert_key, 0.0)
                        if time.time() - last_alert > 900: # 15 min cooldown
                            last_alert_time[alert_key] = time.time()
                            msg = (
                                f"🔥 *0-DTE GAMMA SURGE ALERT (PUT SIDE): {name}*\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"⏰ Time: {now.strftime('%H:%M:%S')} IST\n"
                                f"Time to Expiry: {int(minutes_left)} Mins Left\n"
                                f"Spot Price: {spot_price:.2f}\n"
                                f"🎯 *Hero-Zero Strike to BUY: {int(strike_data['strike'])} PE*\n"
                                f"Premium LTP: ₹{strike_data['pe_ltp']:.2f}\n"
                                f"Strike Gamma (Γ): {strike_data['pe_gamma']:.4f}\n"
                                f"💡 *Trade Direction*: SCENARIO B (Put Gamma Surge). Index downside/rejection will explode PE premium."
                            )
                            send_telegram_message(msg, chat_id=env_config.TELE_CHAT_ID_BN, token=env_config.TELE_TOKEN_BN)
                            
    threading.Thread(target=evaluation_loop, daemon=True).start()

if __name__ == "__main__":
    start_expiry_gamma_scanner()
    while True:
        time.sleep(1)
