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
current_expiry_date = None


def start_expiry_gamma_scanner():
    print("Initializing Expiry Gamma Scanner (0-DTE & Hero-Zero Engine)...")

    def main_supervisor():
        global spot_price, option_quotes, last_gex_sign, current_expiry_date, last_alert_time

        while True:
            try:
                now = datetime.datetime.now(IST)
                today_date = now.date()

                # Weekend check
                if now.weekday() > 4 or now.date().isoformat() in getattr(env_config, "NSE_HOLIDAYS", set()):
                    time.sleep(60)
                    continue

                name, exchange = get_todays_expiry_instrument()
                if not name:
                    # Non-expiry day (e.g. Mon/Wed/Fri) -> standby and check every 5 minutes
                    time.sleep(300)
                    continue

                t = now.time()
                market_start = datetime.time(9, 15)
                market_end = datetime.time(15, 30)

                if not (market_start <= t <= market_end):
                    time.sleep(30)
                    continue

                token = load_access_token()
                if not token:
                    time.sleep(30)
                    continue

                try:
                    kite = KiteConnect(api_key=env_config.API_KEY)
                    kite.set_access_token(token)
                except Exception as e:
                    print(f"[GAMMA] Kite init failed: {e}")
                    time.sleep(30)
                    continue

                df = load_instruments_data()
                if df.empty:
                    time.sleep(30)
                    continue

                opts = df[(df["name"] == name) & (df["segment"].isin(["NFO-OPT", "BFO-OPT"]))].copy()
                if opts.empty:
                    time.sleep(60)
                    continue

                opts["expiry"] = pd.to_datetime(opts["expiry"])
                active_opts = opts[opts["expiry"].dt.date >= today_date]
                if active_opts.empty:
                    time.sleep(60)
                    continue

                closest_expiry = active_opts["expiry"].min()
                if closest_expiry.date() != today_date:
                    time.sleep(300)
                    continue

                # Get Spot token
                spot_tsym = "NIFTY 50" if name == "NIFTY" else "SENSEX"
                spot_symbol = "NSE:NIFTY 50" if name == "NIFTY" else "BSE:SENSEX"
                spots = df[(df["tradingsymbol"] == spot_tsym) & (df["segment"] == "INDICES")]
                if spots.empty:
                    spots = df[df["tradingsymbol"] == spot_tsym]
                if spots.empty:
                    time.sleep(30)
                    continue

                spot_token = int(spots.iloc[0]["instrument_token"])
                lot_size = int(active_opts.iloc[0].get("lot_size", 20 if name == "SENSEX" else 65))

                # If new expiry day initialized
                if current_expiry_date != today_date:
                    current_expiry_date = today_date
                    last_gex_sign = None
                    last_alert_time.clear()
                    print(f"🟢 [0-DTE GAMMA] Tracking {name} on Expiry {today_date} (Lot Size: {lot_size}, Spot Token: {spot_token})")

                    try:
                        spot_quote = kite.quote([spot_symbol]).get(spot_symbol, {})
                        initial_spot = float(spot_quote.get("last_price", 0.0))
                    except Exception:
                        initial_spot = 0.0

                    if initial_spot <= 0:
                        time.sleep(10)
                        continue

                    strikes = sorted(active_opts["strike"].unique())
                    atm_strike = min(strikes, key=lambda x: abs(x - initial_spot))
                    idx = strikes.index(atm_strike)

                    # Track ATM +/- 8 strikes (17 strikes * 2 = 34 contracts)
                    selected_strikes = strikes[max(0, idx - 8): min(len(strikes), idx + 9)]
                    expiry_opts = active_opts[(active_opts["expiry"] == closest_expiry) & (active_opts["strike"].isin(selected_strikes))]

                    active_option_tokens = []
                    token_to_strike_info = {}
                    for _, row in expiry_opts.iterrows():
                        tkn = int(row["instrument_token"])
                        active_option_tokens.append(tkn)
                        token_to_strike_info[tkn] = {
                            "strike": float(row["strike"]),
                            "type": row["instrument_type"],
                            "symbol": row["tradingsymbol"]
                        }

                    target_tokens = [spot_token] + active_option_tokens

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
                                    "iv": tick.get("iv", 0.0) or 15.0,
                                    "volume": tick.get("volume_traded") or tick.get("volume", 0)
                                }

                    def on_connect(ws, response):
                        print(f"[GAMMA] Subscribing {len(target_tokens)} tokens for {name} 0-DTE...")
                        add_shared_tokens(target_tokens)

                    register_ws_callbacks(on_connect, on_ticks)
                    add_shared_tokens(target_tokens)

                # --- 1-Minute Evaluation Loop ---
                close_time = datetime.datetime.combine(now.date(), market_end, tzinfo=IST)
                minutes_left = (close_time - now).total_seconds() / 60.0

                if minutes_left <= 0:
                    time.sleep(300)
                    continue

                if spot_price <= 0 or not option_quotes:
                    time.sleep(10)
                    continue

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

                if gamma_profile:
                    gex_in_cr = total_net_gex / 10_000_000.0
                    current_gex_sign = "POSITIVE" if gex_in_cr >= 0 else "NEGATIVE"

                    call_wall = max(gamma_profile, key=lambda x: x["call_gex"])
                    put_wall = max(gamma_profile, key=lambda x: x["put_gex"])

                    # 0-DTE General Alerts Target: Default Channel (TELE_CHAT_ID)
                    target_chat = env_config.TELE_CHAT_ID
                    # Fire & Forget Hero-Zero Afternoon Squeeze Engine (13:50 to 15:05 IST)
                    is_hero_zero_window = datetime.time(13, 50) <= now.time() <= datetime.time(15, 5)
                    if is_hero_zero_window:
                        # Maintain rolling 30-minute spot history for breakout confirmation
                        now_ts = now.timestamp()
                        if not hasattr(main_supervisor, "spot_history"):
                            main_supervisor.spot_history = []
                            main_supervisor.hero_zero_locked_dir = None

                        main_supervisor.spot_history.append((now_ts, spot_price))
                        # Keep only last 30 minutes (1800 seconds)
                        main_supervisor.spot_history = [
                            (t_s, p) for t_s, p in main_supervisor.spot_history
                            if now_ts - t_s <= 1800
                        ]

                        if len(main_supervisor.spot_history) >= 10:
                            spot_prices_30m = [p for _, p in main_supervisor.spot_history]
                            spot_30m_high = max(spot_prices_30m)
                            spot_30m_low = min(spot_prices_30m)
                            spot_30m_vwap = sum(spot_prices_30m) / len(spot_prices_30m)

                            # Directional Breakout determination
                            is_bullish_breakout = (spot_price >= spot_30m_high - 1.5) and (spot_price > spot_30m_vwap)
                            is_bearish_breakdown = (spot_price <= spot_30m_low + 1.5) and (spot_price < spot_30m_vwap)

                            # Identify single closest OTM strike
                            otm_ce_strike_data = None
                            otm_pe_strike_data = None

                            for s_data in sorted(gamma_profile, key=lambda x: x["strike"]):
                                s_val = s_data["strike"]
                                if s_val > spot_price and otm_ce_strike_data is None:
                                    otm_ce_strike_data = s_data
                                if s_val < spot_price:
                                    otm_pe_strike_data = s_data

                            def _send_fire_and_forget(hz_msg):
                                send_telegram_message(hz_msg, chat_id=env_config.TELE_CHAT_ID, token=env_config.TELE_TOKEN)
                                if env_config.TELE_CHAT_ID_BN and env_config.TELE_CHAT_ID_BN != env_config.TELE_CHAT_ID:
                                    send_telegram_message(hz_msg, chat_id=env_config.TELE_CHAT_ID_BN, token=env_config.TELE_TOKEN_BN)

                            # Price sweet spot: ₹10 - ₹35 for Nifty, ₹25 - ₹90 for Sensex
                            min_price = 25.0 if name == "SENSEX" else 10.0
                            max_price = 90.0 if name == "SENSEX" else 35.0

                            # 1. Bullish Call Side Hero-Zero
                            if is_bullish_breakout and main_supervisor.hero_zero_locked_dir in (None, "CALL") and otm_ce_strike_data:
                                ce_ltp = otm_ce_strike_data["ce_ltp"]
                                ce_strike = int(otm_ce_strike_data["strike"])

                                if min_price <= ce_ltp <= max_price:
                                    alert_key = f"hz_ff_ce_{ce_strike}"
                                    last_alert = last_alert_time.get(alert_key, 0.0)
                                    if time.time() - last_alert > 1800:
                                        last_alert_time[alert_key] = time.time()
                                        main_supervisor.hero_zero_locked_dir = "CALL"

                                        sl_price = max(4.0, round(ce_ltp * 0.45, 1))
                                        tgt1_price = round(ce_ltp * 2.2, 1)
                                        tgt2_price = round(ce_ltp * 3.8, 1)

                                        msg = (
                                            f"🚀 *HERO-ZERO: {name} {ce_strike} CE*\n"
                                            f"Price: *₹{ce_ltp:.2f}*\n"
                                            f"SL: *₹{sl_price:.2f}* | Target: *₹{tgt1_price:.2f}* / *₹{tgt2_price:.2f}*\n"
                                            f"Spot: {spot_price:.2f} (30M High Break)\n"
                                            f"Time: {now.strftime('%H:%M:%S')}"
                                        )
                                        print(f"[HERO-ZERO] Sent {name} {ce_strike} CE @ ₹{ce_ltp:.2f}")
                                        _send_fire_and_forget(msg)

                            # 2. Bearish Put Side Hero-Zero
                            elif is_bearish_breakdown and main_supervisor.hero_zero_locked_dir in (None, "PUT") and otm_pe_strike_data:
                                pe_ltp = otm_pe_strike_data["pe_ltp"]
                                pe_strike = int(otm_pe_strike_data["strike"])

                                if min_price <= pe_ltp <= max_price:
                                    alert_key = f"hz_ff_pe_{pe_strike}"
                                    last_alert = last_alert_time.get(alert_key, 0.0)
                                    if time.time() - last_alert > 1800:
                                        last_alert_time[alert_key] = time.time()
                                        main_supervisor.hero_zero_locked_dir = "PUT"

                                        sl_price = max(4.0, round(pe_ltp * 0.45, 1))
                                        tgt1_price = round(pe_ltp * 2.2, 1)
                                        tgt2_price = round(pe_ltp * 3.8, 1)

                                        msg = (
                                            f"🚨 *HERO-ZERO: {name} {pe_strike} PE*\n"
                                            f"Price: *₹{pe_ltp:.2f}*\n"
                                            f"SL: *₹{sl_price:.2f}* | Target: *₹{tgt1_price:.2f}* / *₹{tgt2_price:.2f}*\n"
                                            f"Spot: {spot_price:.2f} (30M Low Break)\n"
                                            f"Time: {now.strftime('%H:%M:%S')}"
                                        )
                                        print(f"[HERO-ZERO] Sent {name} {pe_strike} PE @ ₹{pe_ltp:.2f}")
                                        _send_fire_and_forget(msg)

            except Exception as e:
                print(f"[GAMMA SCANNER] Loop error: {e}")

            time.sleep(30)

    threading.Thread(target=main_supervisor, daemon=True).start()


if __name__ == "__main__":
    start_expiry_gamma_scanner()
    while True:
        time.sleep(1)
