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

# Excluded Indices from stock scanner (Only individual F&O Stocks)
INDEX_NAMES = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX", "SENSEX50"}

# Configuration Parameters
VOLUME_SHOCK_MULTIPLIER = 8.0     # 1-min volume >= 8x of 20-min average volume
MIN_VOLUME_LOTS = 250             # Absolute minimum lots in 1-min candle
BODY_RATIO_MIN = 0.55             # Real body >= 55% of candle range
ROLLING_WINDOW_MINUTES = 60       # 60-minute consolidation range

# State Management
future_metadata = {}              # token -> meta dict
rolling_candles = {}              # token -> list of completed 1-min candles
current_minute = {}               # token -> minute_str
minute_candles = {}               # token -> live candle dict
option_contracts_cache = {}       # name -> active options DataFrame
last_alert_times = {}             # alert_key -> timestamp
state_lock = threading.Lock()
kite_client = None


def load_access_token():
    token = os.getenv("KITE_ACCESS_TOKEN", "").strip()
    if not token and os.path.exists("access_token.txt"):
        try:
            with open("access_token.txt", "rb") as f:
                raw = f.read()
                if raw.startswith(b"\xff\xfe"):
                    token = raw.decode("utf-16le").strip().replace("\ufeff", "")
                else:
                    token = raw.decode("utf-8", errors="ignore").strip()
        except Exception:
            pass
    return token


def load_instruments():
    if not os.path.exists("instruments.csv"):
        print("[FO-SCANNER] instruments.csv not found!")
        return pd.DataFrame()
    df = pd.read_csv("instruments.csv")
    if "expiry" in df.columns:
        df["expiry_dt"] = pd.to_datetime(df["expiry"], errors="coerce")
        today_date = datetime.datetime.now(IST).date()
        df = df[df["expiry_dt"].isna() | (df["expiry_dt"].dt.date >= today_date)].copy()
    return df


def get_highest_oi_option(name, ref_price, direction):
    global kite_client, option_contracts_cache
    target_type = "CE" if direction == "BULLISH" else "PE"
    action_verb = "BUY CALL (CE)" if direction == "BULLISH" else "BUY PUT (PE)"

    if not kite_client or name not in option_contracts_cache or ref_price <= 0:
        return action_verb, "(ATM Strike)", 0.0

    opts = option_contracts_cache.get(name)
    if opts is None or opts.empty:
        return action_verb, f"(ATM {target_type} Strike)", 0.0

    opts_side = opts[opts["instrument_type"] == target_type]
    if opts_side.empty:
        return action_verb, f"(ATM {target_type} Strike)", 0.0

    unique_strikes = sorted(opts_side["strike"].unique())
    if not unique_strikes:
        return action_verb, f"(ATM {target_type} Strike)", 0.0

    atm_strike = min(unique_strikes, key=lambda x: abs(x - ref_price))
    idx = unique_strikes.index(atm_strike)

    selected_strikes = unique_strikes[max(0, idx - 1): min(len(unique_strikes), idx + 2)]
    target_opts = opts_side[opts_side["strike"].isin(selected_strikes)]
    if target_opts.empty:
        return action_verb, f"(ATM {target_type} Strike)", 0.0

    symbols_to_quote = [f"{r['exchange']}:{r['tradingsymbol']}" for _, r in target_opts.iterrows()]
    try:
        quotes = kite_client.quote(symbols_to_quote)
    except Exception:
        quotes = {}

    best_strike = None
    max_oi = -1
    best_ltp = 0.0
    best_symbol = ""

    for _, row in target_opts.iterrows():
        sym_key = f"{row['exchange']}:{row['tradingsymbol']}"
        q = quotes.get(sym_key, {})
        oi = q.get("oi", 0)
        ltp = float(q.get("last_price", 0.0))
        strike_val = float(row["strike"])

        if oi > max_oi or (oi == max_oi and strike_val == atm_strike):
            max_oi = oi
            best_strike = strike_val
            best_ltp = ltp
            best_symbol = row["tradingsymbol"]

    if best_symbol:
        return action_verb, best_symbol, best_ltp

    return action_verb, f"(ATM {target_type} Strike)", 0.0


def process_completed_1m_candle(token, closed_candle):
    with state_lock:
        meta = future_metadata.get(token)
        if not meta:
            return

        if token not in rolling_candles:
            rolling_candles[token] = []

        history = rolling_candles[token]
        history.append(closed_candle)

        # Keep rolling 60 minutes of history
        if len(history) > ROLLING_WINDOW_MINUTES + 1:
            history.pop(0)

        if len(history) < 15:
            return

        # Past 20-period volume average (excluding current completed candle)
        past_volumes = [c["volume"] for c in history[:-1][-20:]]
        avg_vol_20m = sum(past_volumes) / len(past_volumes) if past_volumes else 1.0

        # Past 60-minute High & Low (excluding current candle)
        past_highs = [c["high"] for c in history[:-1]]
        past_lows = [c["low"] for c in history[:-1]]
        high_60m = max(past_highs)
        low_60m = min(past_lows)

    c_o = closed_candle["open"]
    c_h = closed_candle["high"]
    c_l = closed_candle["low"]
    c_c = closed_candle["close"]
    c_vol = closed_candle["volume"]
    c_lots = closed_candle["lots"]

    c_range = max(0.05, c_h - c_l)
    c_body = abs(c_c - c_o)
    body_ratio = c_body / c_range

    rvol = c_vol / max(1.0, avg_vol_20m)

    # Volume Shock filter
    if rvol < VOLUME_SHOCK_MULTIPLIER or c_lots < MIN_VOLUME_LOTS or body_ratio < BODY_RATIO_MIN:
        return

    # 1. Bullish Breakout Check
    is_bullish = (c_c > c_o) and (c_c > high_60m)
    # 2. Bearish Breakdown Check
    is_bearish = (c_c < c_o) and (c_c < low_60m)

    if not (is_bullish or is_bearish):
        return

    direction = "BULLISH" if is_bullish else "BEARISH"
    name = meta["name"]
    symbol = meta["symbol"]
    lot_size = meta["lot_size"]

    alert_key = f"{name}_{direction}"
    with state_lock:
        last_sent = last_alert_times.get(alert_key, 0.0)
        if time.time() - last_sent < 900:  # 15-minute cooldown per asset
            return
        last_alert_times[alert_key] = time.time()

    now = datetime.datetime.now(IST)
    action_verb, opt_symbol, opt_ltp = get_highest_oi_option(name, c_c, direction)

    opt_line = f"Option: *{opt_symbol}*"
    if opt_ltp > 0:
        opt_line += f" (LTP: ₹{opt_ltp:.2f})"

    if is_bullish:
        header = "🚀 *INSTITUTIONAL VOLUME BREAKOUT (ALL F&O)*"
        level_line = f"Broke 60M High: *₹{high_60m:.2f}*"
    else:
        header = "🚨 *INSTITUTIONAL VOLUME BREAKDOWN (ALL F&O)*"
        level_line = f"Broke 60M Low: *₹{low_60m:.2f}*"

    msg = (
        f"{header}\n"
        f"Stock: *{name}* (₹{c_c:.2f})\n"
        f"Volume: *{c_lots} Lots* ({rvol:.1f}x of 20M Avg)\n"
        f"{level_line}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Action: *{action_verb}*\n"
        f"{opt_line}\n"
        f"Time: {now.strftime('%H:%M:%S')} IST"
    )

    print(f"[FO-INSTITUTIONAL] {name} {direction} Triggered: Vol={c_lots} Lots ({rvol:.1f}x RVOL)")
    target_chat = getattr(env_config, "TELE_CHAT_ID_STOCKS", env_config.TELE_CHAT_ID)
    target_token = getattr(env_config, "TELE_TOKEN_STOCKS", env_config.TELE_TOKEN)
    send_telegram_message(msg, chat_id=target_chat, token=target_token)


def start_fo_institutional_breakout_scanner():
    print("Initializing All-F&O Institutional Volume Shock & Breakout Scanner...")
    global kite_client, option_contracts_cache, future_metadata
    token = load_access_token()
    if not token:
        print("[FO-SCANNER] Access token missing. Standby.")
        return

    try:
        kite_client = KiteConnect(api_key=env_config.API_KEY)
        kite_client.set_access_token(token)
    except Exception as e:
        print(f"[FO-SCANNER] Kite init failed: {e}")
        return

    df = load_instruments()
    if df.empty:
        return

    # Discover ALL active F&O stock futures dynamically
    stock_futs = df[
        (df["segment"] == "NFO-FUT") &
        (~df["name"].isin(INDEX_NAMES)) &
        (df["name"].notna())
    ].copy()

    if stock_futs.empty:
        print("[FO-SCANNER] No stock futures found.")
        return

    target_tokens = []
    for name, rows in stock_futs.groupby("name"):
        rows_sorted = rows.sort_values(by="expiry")
        near_fut = rows_sorted.iloc[0]
        fut_tkn = int(near_fut["instrument_token"])
        lot_size = int(near_fut.get("lot_size", 1))
        fut_sym = near_fut["tradingsymbol"]

        target_tokens.append(fut_tkn)
        future_metadata[fut_tkn] = {
            "name": name,
            "symbol": fut_sym,
            "lot_size": lot_size
        }

        # Cache active monthly options for highest OI strike recommendations
        opts = df[(df["name"] == name) & (df["segment"] == "NFO-OPT")]
        if not opts.empty:
            closest_exp = opts["expiry"].min()
            option_contracts_cache[name] = opts[opts["expiry"] == closest_exp].copy()

    print(f"[FO-SCANNER] Registered {len(target_tokens)} F&O Stock Futures for Real-time Institutional Tracking.")

    def on_ticks(ws, ticks):
        now = datetime.datetime.now(IST)
        minute_str = now.strftime("%Y-%m-%d %H:%M")

        with state_lock:
            for tick in ticks:
                tkn = tick["instrument_token"]
                if tkn not in future_metadata:
                    continue

                ltp = tick["last_price"]
                vol = tick.get("volume_traded") or tick.get("volume", 0)
                lot_size = future_metadata[tkn]["lot_size"]

                if tkn not in current_minute:
                    current_minute[tkn] = minute_str
                    minute_candles[tkn] = {
                        "open": ltp,
                        "high": ltp,
                        "low": ltp,
                        "close": ltp,
                        "start_vol": vol,
                        "current_vol": vol
                    }

                if current_minute[tkn] != minute_str:
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

                    current_minute[tkn] = minute_str
                    minute_candles[tkn] = {
                        "open": ltp,
                        "high": ltp,
                        "low": ltp,
                        "close": ltp,
                        "start_vol": vol,
                        "current_vol": vol
                    }

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
        print(f"[FO-SCANNER] Subscribing {len(target_tokens)} F&O Stock Futures...")
        add_shared_tokens(target_tokens)

    register_ws_callbacks(on_connect, on_ticks)
    add_shared_tokens(target_tokens)
    print("[FO-SCANNER] All-F&O Institutional Scanner Active.")


if __name__ == "__main__":
    start_fo_institutional_breakout_scanner()
    while True:
        time.sleep(1)
