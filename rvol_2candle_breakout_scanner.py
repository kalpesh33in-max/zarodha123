import os
import time
import datetime
import threading
import pandas as pd
from zoneinfo import ZoneInfo
from kiteconnect import KiteConnect

import env_config
from websocket_flow import register_ws_callbacks, add_shared_tokens
from telegram_utils import send_telegram_message

IST = ZoneInfo("Asia/Kolkata")

# 1. 4 Major Indices (Futures Only - No Spot)
INDEX_NAMES = ["BANKNIFTY", "NIFTY", "SENSEX", "MIDCPNIFTY"]

# 2. MCX Commodity (Futures Only - No Spot)
MCX_NAMES = ["CRUDEOILM"]

# 3. 32 Stocks (5 Major Banks + 27 F&O Stocks) -> Both Spot & Future
STOCK_NAMES = [
    # 5 Major Banking Stocks
    "HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "KOTAKBANK",
    # Stocks with Lot Size <= 550
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "BAJAJ-AUTO",
    "BAJAJFINSV", "BHARTIARTL", "BRITANNIA", "CIPLA", "EICHERMOT",
    "GRASIM", "HCLTECH", "HEROMOTOCO", "HINDUNILVR", "INFY",
    "LT", "M&M", "MARUTI", "NESTLEIND", "RELIANCE",
    "SBILIFE", "SUNPHARMA", "TATACONSUM", "TCS", "TITAN",
    "TRENT", "ULTRACEMCO"
]

ALL_UNDERLYINGS = INDEX_NAMES + STOCK_NAMES + MCX_NAMES

# Volume Thresholds in Lots
CANDLE1_MIN_LOTS = 500
CANDLE2_MIN_LOTS = 300

# MCX Crude Oil Volume Thresholds
CANDLE1_MCX_MIN_LOTS = 75
CANDLE2_MCX_MIN_LOTS = 50

BODY_RATIO_MIN = 0.60       # Real body >= 60% of total candle range
CLOSE_LOC_MAX = 0.20        # Close in top/bottom 20% of range
MAX_OPPOSITE_WICK = 0.30    # Opposite rejection wick <= 30% of range

# Thread-safe state tracking
candle_history = {}         # token -> list of closed 1m candles
candle_state = {}           # token -> live 1m candle dict
token_metadata = {}         # token -> metadata dict
option_contracts_cache = {} # name -> DataFrame of active options
last_alert_times = {}       # alert_key -> timestamp
state_lock = threading.Lock()
kite_client = None


def load_access_token():
    token = os.getenv("KITE_ACCESS_TOKEN", "").strip()
    if not token and os.path.exists("access_token.txt"):
        with open("access_token.txt", "r") as f:
            token = f.read().strip()
    return token


def load_instruments():
    if not os.path.exists("instruments.csv"):
        print("[2-CANDLE] instruments.csv not found!")
        return pd.DataFrame()
    df = pd.read_csv("instruments.csv")
    if "expiry" in df.columns:
        df["expiry_dt"] = pd.to_datetime(df["expiry"], errors="coerce")
        today_date = datetime.datetime.now(IST).date()
        df = df[df["expiry_dt"].isna() | (df["expiry_dt"].dt.date >= today_date)].copy()
    return df


def fmt_oi(v):
    v = float(v or 0)
    if v >= 10_000_000:
        return f"{v/10_000_000:.1f}Cr"
    elif v >= 100_000:
        return f"{v/100_000:.1f}L"
    elif v >= 1_000:
        return f"{int(v/1000)}K"
    return str(int(v))


def get_highest_oi_option(name, ref_price, direction):
    """
    Finds the ATM +- 1 strike with the Highest Open Interest (OI)
    for CE (Bullish) or PE (Bearish).
    """
    global kite_client, option_contracts_cache
    if not kite_client or name not in option_contracts_cache or ref_price <= 0:
        action_verb = "BUY CALL (CE)" if direction == "BULLISH" else "BUY PUT (PE)"
        return f"Action:- *{action_verb}*\n(ATM Strike)"

    opts = option_contracts_cache.get(name)
    if opts is None or opts.empty:
        action_verb = "BUY CALL (CE)" if direction == "BULLISH" else "BUY PUT (PE)"
        return f"Action:- *{action_verb}*\n(ATM Strike)"

    target_type = "CE" if direction == "BULLISH" else "PE"
    opts_side = opts[opts["instrument_type"] == target_type]
    if opts_side.empty:
        action_verb = "BUY CALL (CE)" if direction == "BULLISH" else "BUY PUT (PE)"
        return f"Action:- *{action_verb}*\n(ATM {target_type} Strike)"

    unique_strikes = sorted(opts_side["strike"].unique())
    if not unique_strikes:
        action_verb = "BUY CALL (CE)" if direction == "BULLISH" else "BUY PUT (PE)"
        return f"Action:- *{action_verb}*\n(ATM {target_type} Strike)"

    # Find ATM Strike
    atm_strike = min(unique_strikes, key=lambda x: abs(x - ref_price))
    idx = unique_strikes.index(atm_strike)

    # ATM +- 1 Strikes (ATM-1, ATM, ATM+1)
    selected_strikes = unique_strikes[max(0, idx - 1): min(len(unique_strikes), idx + 2)]
    target_opts = opts_side[opts_side["strike"].isin(selected_strikes)]
    if target_opts.empty:
        action_verb = "BUY CALL (CE)" if direction == "BULLISH" else "BUY PUT (PE)"
        return f"Action:- *{action_verb}*\n(ATM {target_type} Strike)"

    # Query live quotes to identify Highest OI strike
    symbols_to_quote = [f"{r['exchange']}:{r['tradingsymbol']}" for _, r in target_opts.iterrows()]
    try:
        quotes = kite_client.quote(symbols_to_quote)
    except Exception as e:
        print(f"[2-CANDLE] Quote fetch failed for {name} options: {e}")
        quotes = {}

    best_strike = None
    max_oi = -1
    best_ltp = 0.0
    best_symbol = ""

    for _, row in target_opts.iterrows():
        sym_key = f"{row['exchange']}:{row['tradingsymbol']}"
        q = quotes.get(sym_key, {})
        oi = q.get("oi", 0)
        ltp = q.get("last_price", 0.0)
        strike_val = float(row["strike"])
        
        # If ATM strike is close, give tie-breaker preference
        if oi > max_oi or (oi == max_oi and strike_val == atm_strike):
            max_oi = oi
            best_strike = strike_val
            best_ltp = ltp
            best_symbol = row["tradingsymbol"]

    if best_symbol and best_strike is not None:
        action_verb = "BUY CALL (CE)" if direction == "BULLISH" else "BUY PUT (PE)"
        return f"Action:- *{action_verb}*\n*{best_symbol}*\nLTP: *₹{best_ltp:.2f}*"

    action_verb = "BUY CALL (CE)" if direction == "BULLISH" else "BUY PUT (PE)"
    return f"Action:- *{action_verb}*\n(ATM {target_type} Strike)"


def analyze_2candle_pattern(c1, c2, is_mcx=False):
    """
    Evaluates 2 completed consecutive 1-minute candles.
    Returns: (is_signal, direction, score, details)
    """
    c1_c = c1["close"]
    c1_o = c1["open"]
    c1_h = c1["high"]
    c1_l = c1["low"]
    c1_lots = c1["lots"]

    c2_c = c2["close"]
    c2_o = c2["open"]
    c2_h = c2["high"]
    c2_l = c2["low"]
    c2_lots = c2["lots"]

    c1_range = max(0.01, c1_h - c1_l)
    c2_range = max(0.01, c2_h - c2_l)

    c1_body = abs(c1_c - c1_o)
    c2_body = abs(c2_c - c2_o)

    c1_body_ratio = c1_body / c1_range
    c2_body_ratio = c2_body / c2_range

    # Threshold checks
    c1_req_lots = CANDLE1_MCX_MIN_LOTS if is_mcx else CANDLE1_MIN_LOTS
    c2_req_lots = CANDLE2_MCX_MIN_LOTS if is_mcx else CANDLE2_MIN_LOTS

    if c1_lots < c1_req_lots or c2_lots < c2_req_lots:
        return False, None, 0, {}

    # Check 1: Bearish Breakdown
    if c1_c < c1_o and c2_c < c2_o:  # Both Red candles
        c1_lower_wick = max(0.0, c1_c - c1_l) / c1_range
        c2_lower_wick = max(0.0, c2_c - c2_l) / c2_range
        c1_rejection = max(0.0, c1_c - c1_l) / c1_range
        c2_rejection = max(0.0, c2_c - c2_l) / c2_range

        # Candle 1 checks: body >= 60%, close in bottom 20%, lower wick <= 30%
        c1_valid = (c1_body_ratio >= BODY_RATIO_MIN) and (c1_lower_wick <= CLOSE_LOC_MAX) and (c1_rejection <= MAX_OPPOSITE_WICK)
        # Candle 2 checks: closes below Candle 1 Low, body >= 60%, close in bottom 20%
        c2_valid = (c2_c < c1_l) and (c2_body_ratio >= BODY_RATIO_MIN) and (c2_lower_wick <= CLOSE_LOC_MAX) and (c2_rejection <= MAX_OPPOSITE_WICK)

        if c1_valid and c2_valid:
            score = 9
            if c1_lots >= c1_req_lots * 1.5 and c2_lots >= c2_req_lots * 1.5:
                score = 10
            details = {
                "c1_body_pct": c1_body_ratio * 100,
                "c2_body_pct": c2_body_ratio * 100,
                "c1_lots": c1_lots,
                "c2_lots": c2_lots,
                "broken_level": c1_l,
                "level_type": "Support (C1 Low)",
                "move_pts": c1_o - c2_c
            }
            return True, "BEARISH", score, details

    # Check 2: Bullish Breakout
    elif c1_c > c1_o and c2_c > c2_o:  # Both Green candles
        c1_upper_wick = max(0.0, c1_h - c1_c) / c1_range
        c2_upper_wick = max(0.0, c2_h - c2_c) / c2_range
        c1_rejection = max(0.0, c1_h - c1_c) / c1_range
        c2_rejection = max(0.0, c2_h - c2_c) / c2_range

        # Candle 1 checks: body >= 60%, close in top 20%, upper wick <= 30%
        c1_valid = (c1_body_ratio >= BODY_RATIO_MIN) and (c1_upper_wick <= CLOSE_LOC_MAX) and (c1_rejection <= MAX_OPPOSITE_WICK)
        # Candle 2 checks: closes above Candle 1 High, body >= 60%, close in top 20%
        c2_valid = (c2_c > c1_h) and (c2_body_ratio >= BODY_RATIO_MIN) and (c2_upper_wick <= CLOSE_LOC_MAX) and (c2_rejection <= MAX_OPPOSITE_WICK)

        if c1_valid and c2_valid:
            score = 9
            if c1_lots >= c1_req_lots * 1.5 and c2_lots >= c2_req_lots * 1.5:
                score = 10
            details = {
                "c1_body_pct": c1_body_ratio * 100,
                "c2_body_pct": c2_body_ratio * 100,
                "c1_lots": c1_lots,
                "c2_lots": c2_lots,
                "broken_level": c1_h,
                "level_type": "Resistance (C1 High)",
                "move_pts": c2_c - c1_o
            }
            return True, "BULLISH", score, details

    return False, None, 0, {}


def process_completed_1m_candle(token, closed_candle):
    with state_lock:
        meta = token_metadata.get(token)
        if not meta:
            return

        if token not in candle_history:
            candle_history[token] = []

        history = candle_history[token]
        history.append(closed_candle)
        if len(history) > 10:
            history.pop(0)

        if len(history) < 2:
            return

        c1 = history[-2]
        c2 = history[-1]

    # Pattern check
    is_mcx = meta.get("is_mcx", False)
    is_signal, direction, score, details = analyze_2candle_pattern(c1, c2, is_mcx=is_mcx)

    if is_signal:
        now = datetime.datetime.now(IST)
        name = meta["name"]
        display_label = meta["display_label"]

        # Cooldown of 5 minutes per asset direction
        alert_key = f"{display_label}_{direction}"
        last_sent = last_alert_times.get(alert_key, 0.0)
        if time.time() - last_sent < 300:
            return
        last_alert_times[alert_key] = time.time()

        if direction == "BULLISH":
            signal_header = "🚀 *1-MIN 2-CANDLE BREAKOUT*"
        else:
            signal_header = "🚨 *1-MIN 2-CANDLE BREAKDOWN*"

        # Resolve ATM +- 1 strike with Highest Open Interest (OI)
        action_line = get_highest_oi_option(name, c2["close"], direction)

        msg = (
            f"{signal_header}\n"
            f"Asset: *{display_label}* (₹{c2['close']:.2f})\n"
            f"Vol: C1: *{details['c1_lots']}L* | C2: *{details['c2_lots']}L*\n"
            f"Broken: *₹{details['broken_level']:.2f}*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"{action_line}\n"
            f"TIME: {now.strftime('%H:%M:%S')}"
        )

        print(f"[2-CANDLE BREAKOUT] {display_label} {direction} confirmed (C1: {details['c1_lots']}L, C2: {details['c2_lots']}L)")
        chat_stocks = getattr(env_config, "TELE_CHAT_ID_STOCKS", env_config.TELE_CHAT_ID)
        token_stocks = getattr(env_config, "TELE_TOKEN_STOCKS", env_config.TELE_TOKEN)
        send_telegram_message(msg, chat_id=chat_stocks, token=token_stocks)


def start_rvol_2candle_breakout_scanner():
    print("Initializing 2-Candle 1-Minute Volume Breakout/Breakdown Scanner...")
    global kite_client, option_contracts_cache
    token = load_access_token()
    if not token:
        print("[2-CANDLE] Access token missing. Scanner standby.")
        return

    try:
        kite_client = KiteConnect(api_key=env_config.API_KEY)
        kite_client.set_access_token(token)
    except Exception as e:
        print(f"[2-CANDLE] Kite init failed: {e}")
        return

    df = load_instruments()
    if df.empty:
        return

    global token_metadata
    target_tokens = []

    # 1. Register Index Futures (Futures Only - No Spot for Indices)
    for name in INDEX_NAMES:
        futs = df[(df["name"] == name) & (df["instrument_type"] == "FUT")]
        if not futs.empty:
            futs = futs.sort_values(by="expiry")
            fut = futs.iloc[0]
            lot_size = int(fut.get("lot_size", 10 if name == "SENSEX" else 25))
            fut_tkn = int(fut["instrument_token"])
            fut_symbol = fut["tradingsymbol"]

            target_tokens.append(fut_tkn)
            token_metadata[fut_tkn] = {
                "name": name,
                "display_label": fut_symbol,
                "lot_size": lot_size,
                "is_mcx": False,
                "is_spot": False
            }

    # 2. Register MCX Crude Oil (Futures Only - No Spot)
    for name in MCX_NAMES:
        futs = df[(df["name"] == name) & (df["instrument_type"] == "FUT")]
        if not futs.empty:
            futs = futs.sort_values(by="expiry")
            fut = futs.iloc[0]
            lot_size = 10 if name == "CRUDEOILM" else int(fut.get("lot_size", 1))
            fut_tkn = int(fut["instrument_token"])
            fut_symbol = fut["tradingsymbol"]

            target_tokens.append(fut_tkn)
            token_metadata[fut_tkn] = {
                "name": name,
                "display_label": fut_symbol,
                "lot_size": lot_size,
                "is_mcx": True,
                "is_spot": False
            }

    # 3. Register 32 Stocks (Both Spot & Future)
    for name in STOCK_NAMES:
        futs = df[(df["name"] == name) & (df["instrument_type"] == "FUT")]
        if not futs.empty:
            futs = futs.sort_values(by="expiry")
            fut = futs.iloc[0]
            lot_size = int(fut.get("lot_size", 1))
            fut_tkn = int(fut["instrument_token"])
            fut_symbol = fut["tradingsymbol"]

            # Future Token
            target_tokens.append(fut_tkn)
            token_metadata[fut_tkn] = {
                "name": name,
                "display_label": fut_symbol,
                "lot_size": lot_size,
                "is_mcx": False,
                "is_spot": False
            }

            # Spot Token
            spots = df[(df["tradingsymbol"] == name) & (df["segment"] == "NSE")]
            if not spots.empty:
                spot_tkn = int(spots.iloc[0]["instrument_token"])
                target_tokens.append(spot_tkn)
                token_metadata[spot_tkn] = {
                    "name": name,
                    "display_label": f"{name} SPOT",
                    "lot_size": lot_size,
                    "is_mcx": False,
                    "is_spot": True
                }

    # 4. Cache Active Monthly/Closest Options for All Underlyings (for Highest OI ATM+-1 Lookup)
    for name in ALL_UNDERLYINGS:
        opts = df[(df["name"] == name) & (df["instrument_type"].isin(["CE", "PE"]))]
        if not opts.empty:
            closest_expiry = opts["expiry"].min()
            option_contracts_cache[name] = opts[opts["expiry"] == closest_expiry].copy()

    print(f"[2-CANDLE] Tracking {len(target_tokens)} instruments (4 Index Futures, 1 MCX Future, 32 Stocks Spot+Future).")

    current_minute = {}
    minute_candles = {}

    def on_ticks(ws, ticks):
        now = datetime.datetime.now(IST)
        minute_str = now.strftime("%Y-%m-%d %H:%M")

        with state_lock:
            for tick in ticks:
                tkn = tick["instrument_token"]
                if tkn not in token_metadata:
                    continue

                ltp = tick["last_price"]
                vol = tick.get("volume_traded") or tick.get("volume", 0)
                meta = token_metadata[tkn]
                lot_size = meta["lot_size"]

                if tkn not in current_minute:
                    current_minute[tkn] = minute_str
                    minute_candles[tkn] = {
                        "open": ltp,
                        "high": ltp,
                        "low": ltp,
                        "close": ltp,
                        "start_vol": vol,
                        "current_vol": vol,
                    }

                if current_minute[tkn] != minute_str:
                    # Previous 1-minute candle completed
                    c = minute_candles[tkn]
                    candle_vol = max(0, c["current_vol"] - c["start_vol"])
                    closed_candle = {
                        "open": c["open"],
                        "high": c["high"],
                        "low": c["low"],
                        "close": c["close"],
                        "volume": candle_vol,
                        "lots": int(candle_vol / lot_size),
                        "minute": current_minute[tkn]
                    }

                    # Start next minute candle
                    current_minute[tkn] = minute_str
                    minute_candles[tkn] = {
                        "open": ltp,
                        "high": ltp,
                        "low": ltp,
                        "close": ltp,
                        "start_vol": vol,
                        "current_vol": vol,
                    }

                    # Dispatch candle evaluation asynchronously
                    threading.Thread(
                        target=process_completed_1m_candle,
                        args=(tkn, closed_candle),
                        daemon=True
                    ).start()
                else:
                    c = minute_candles[tkn]
                    c["close"] = ltp
                    c["high"] = max(c["high"], ltp)
                    c["low"] = min(c["low"], ltp)
                    c["current_vol"] = vol

    def on_connect(ws, response):
        print(f"[2-CANDLE] Subscribing {len(target_tokens)} Spot & Future tokens...")
        add_shared_tokens(target_tokens)

    register_ws_callbacks(on_connect, on_ticks)
    add_shared_tokens(target_tokens)
    print("[2-CANDLE] 2-Candle Breakout Scanner registered successfully.")


if __name__ == "__main__":
    start_rvol_2candle_breakout_scanner()
    while True:
        time.sleep(1)
