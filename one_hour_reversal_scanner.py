"""
1-Hour 5-Candle Institutional Reversal Scanner (Nifty 500 Cash Stocks)
----------------------------------------------------------------------
Core Logic:
1. Bullish Reversal:
   - Prior Trend: 5 consecutive 1-Hour completed candles making Lower Lows:
     Low(C1) > Low(C2) > Low(C3) > Low(C4) > Low(C5).
   - Liquidity Filter: Average 1-Hour volume across the 5 candles >= MIN_1H_AVG_VOLUME (default 10,000).
   - Trigger A (Exhaustion on 5th Candle):
     5th candle is a Doji (body <= 15% range) or Hammer / Pull-up from back (lower wick >= 45% range).
     Alerts immediately upon 5th candle close.
   - Trigger B (Live 6th Candle Breakout):
     6th 1-Hour candle forms Green (LTP > Open6) and crosses above 5th candle's High (LTP > High5).
     Alerts in real-time via WebSocket.

2. Bearish Reversal (Reverse Direction):
   - Prior Trend: 5 consecutive 1-Hour completed candles making Higher Highs:
     High(C1) < High(C2) < High(C3) < High(C4) < High(C5).
   - Liquidity Filter: Average 1-Hour volume across the 5 candles >= MIN_1H_AVG_VOLUME (default 10,000).
   - Trigger A (Exhaustion on 5th Candle):
     5th candle is a Doji (body <= 15% range) or Shooting Star / Push-down from top (upper wick >= 45% range).
     Alerts immediately upon 5th candle close.
   - Trigger B (Live 6th Candle Breakdown):
     6th 1-Hour candle forms Red (LTP < Open6) and crosses below 5th candle's Low (LTP < Low5).
     Alerts in real-time via WebSocket.

3. Coverage:
   - All 500 Nifty 500 Cash stocks (NSE segment: EQ).
4. Telegram Routing:
   - Alerts sent to @zarodastock_bot (TELE_TOKEN_STOCKS, TELE_CHAT_ID_STOCKS: 530388484).
"""

import os
import time
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd

import env_config
from telegram_utils import send_telegram_message
from websocket_flow import register_ws_callbacks, add_shared_tokens

IST = ZoneInfo("Asia/Kolkata")
MIN_1H_AVG_VOLUME = int(os.getenv("MIN_1H_AVG_VOLUME", "10000"))

# Thread safety & State
_state_lock = threading.Lock()
_stock_metadata = {}          # token -> {"name": name, "symbol": symbol}
_active_breakout_watches = {} # token -> dict of active watch parameters
_current_1h_candle = {}     # token -> {"open": float, "high": float, "low": float, "close": float, "slot": str}
_alerted_keys = set()       # Deduplication set for alert keys
_scanner_started = False


def _get_access_token():
    for fpath in ["access_token.txt", os.path.join(os.path.dirname(__file__), "access_token.txt")]:
        if os.path.exists(fpath):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    t = f.read().strip()
                    if t:
                        return t
            except Exception:
                pass
    return None


def _load_instruments_df():
    csv_paths = [
        "instruments.csv",
        os.path.join(os.path.dirname(__file__), "instruments.csv"),
    ]
    for p in csv_paths:
        if os.path.exists(p):
            try:
                df = pd.read_csv(p)
                if "expiry" in df.columns:
                    df["expiry_dt"] = pd.to_datetime(df["expiry"], errors="coerce")
                return df
            except Exception:
                pass
    return pd.DataFrame()


def _load_nifty500_symbols():
    """Loads Nifty 500 stock symbols from local CSV or falls back to official NSE download."""
    fpaths = [
        "nifty500_symbols.csv",
        os.path.join(os.path.dirname(__file__), "nifty500_symbols.csv"),
    ]
    for p in fpaths:
        if os.path.exists(p):
            try:
                df_n500 = pd.read_csv(p)
                if "Symbol" in df_n500.columns:
                    return set(df_n500["Symbol"].dropna().unique())
            except Exception:
                pass

    try:
        import urllib.request
        import io
        url = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=10).read()
        df_n500 = pd.read_csv(io.BytesIO(data))
        if "Symbol" in df_n500.columns:
            return set(df_n500["Symbol"].dropna().unique())
    except Exception as e:
        print(f"[1H REVERSAL] Failed to fetch Nifty 500 list from NSE: {e}")

    return set()


def _is_market_open(now):
    if now.weekday() > 4 or now.date().isoformat() in getattr(env_config, "NSE_HOLIDAYS", set()):
        return False
    t = now.time()
    return datetime.strptime("09:15", "%H:%M").time() <= t <= datetime.strptime("15:30", "%H:%M").time()


def _get_current_1h_slot(now):
    """
    Returns a unique string identifying the current 1-Hour slot during NSE hours:
    09:15 - 10:15 -> slot_1
    10:15 - 11:15 -> slot_2
    11:15 - 12:15 -> slot_3
    12:15 - 13:15 -> slot_4
    13:15 - 14:15 -> slot_5
    14:15 - 15:15 -> slot_6
    15:15 - 15:30 -> slot_7
    """
    date_str = now.strftime("%Y-%m-%d")
    hour = now.hour
    minute = now.minute

    if hour == 9 or (hour == 10 and minute < 15):
        slot = "09:15-10:15"
    elif hour == 10 or (hour == 11 and minute < 15):
        slot = "10:15-11:15"
    elif hour == 11 or (hour == 12 and minute < 15):
        slot = "11:15-12:15"
    elif hour == 12 or (hour == 13 and minute < 15):
        slot = "12:15-13:15"
    elif hour == 13 or (hour == 14 and minute < 15):
        slot = "13:15-14:15"
    elif hour == 14 or (hour == 15 and minute < 15):
        slot = "14:15-15:15"
    else:
        slot = "15:15-15:30"

    return f"{date_str}_{slot}"


def _send_reversal_alert(message, alert_key):
    """Dispatches alert to @zarodastock_bot with strict deduplication."""
    with _state_lock:
        if alert_key in _alerted_keys:
            return
        _alerted_keys.add(alert_key)

    chat_id = getattr(env_config, "TELE_CHAT_ID_STOCKS", env_config.TELE_CHAT_ID)
    token = getattr(env_config, "TELE_TOKEN_STOCKS", env_config.TELE_TOKEN)
    try:
        send_telegram_message(message, chat_id=chat_id, token=token)
    except Exception as e:
        print(f"[1H REVERSAL] Telegram send error: {e}")


def _evaluate_5_candles_reversal(candles, now, meta):
    """
    Evaluates completed 1-Hour candles for 5 consecutive Lower Lows or Higher Highs.
    Checks liquidity (avg volume >= MIN_1H_AVG_VOLUME).
    Returns (bullish_setup, bearish_setup).
    """
    if not candles:
        return None, None

    # Filter strictly completed 60-minute candles
    completed = []
    for c in candles:
        c_date = c.get("date")
        if not c_date:
            continue
        # In Zerodha, 60min candle date is start time. Completed when start + 60 min <= now
        if c_date + timedelta(minutes=60) <= now:
            completed.append(c)

    if len(completed) < 5:
        return None, None

    c1, c2, c3, c4, c5 = completed[-5:]

    # Liquidity check: Ensure average 1H volume across the 5 candles meets threshold
    total_vol = sum(c.get("volume", 0) for c in (c1, c2, c3, c4, c5))
    avg_vol = total_vol / 5.0
    if avg_vol < MIN_1H_AVG_VOLUME:
        return None, None

    l1, l2, l3, l4, l5 = float(c1["low"]), float(c2["low"]), float(c3["low"]), float(c4["low"]), float(c5["low"])
    h1, h2, h3, h4, h5 = float(c1["high"]), float(c2["high"]), float(c3["high"]), float(c4["high"]), float(c5["high"])
    o5, c5_val = float(c5["open"]), float(c5["close"])

    c1_val = float(c1["close"])
    c2_val = float(c2["close"])
    c3_val = float(c3["close"])
    c4_val = float(c4["close"])

    # Check closes: minimum 4 candles must make lower close for bullish (or higher close for bearish)
    is_4_lower_closes = (
        (c1_val > c2_val > c3_val > c4_val) or
        (c2_val > c3_val > c4_val > c5_val)
    )
    is_4_higher_closes = (
        (c1_val < c2_val < c3_val < c4_val) or
        (c2_val < c3_val < c4_val < c5_val)
    )

    r5 = h5 - l5
    if r5 <= 0:
        return None, None

    body5 = abs(c5_val - o5)
    lower_wick5 = min(o5, c5_val) - l5
    upper_wick5 = h5 - max(o5, c5_val)

    is_doji5 = (body5 <= 0.15 * r5)
    # Hammer / Pull-up from back: lower shadow at least 45% of range and close in upper half
    is_hammer5 = (lower_wick5 >= 0.45 * r5) and (c5_val >= l5 + 0.35 * r5)
    # Shooting Star / Push-down from top: upper shadow at least 45% of range and close in lower half
    is_shooting_star5 = (upper_wick5 >= 0.45 * r5) and (c5_val <= h5 - 0.35 * r5)

    bullish_setup = None
    bearish_setup = None

    # Check 5 Consecutive Lower Lows AND minimum 4 candles Lower Close
    if (l1 > l2 > l3 > l4 > l5) and is_4_lower_closes:
        exhaustion_pattern = None
        if is_doji5:
            exhaustion_pattern = "Doji (Exhaustion at Low)"
        elif is_hammer5:
            exhaustion_pattern = "Hammer / Pinbar (Pull Up From Low)"

        bullish_setup = {
            "direction": "BULLISH",
            "c5_high": h5,
            "c5_low": l5,
            "c5_close": c5_val,
            "c5_open": o5,
            "c5_date": c5.get("date"),
            "avg_vol": avg_vol,
            "exhaustion_pattern": exhaustion_pattern,
            "lows": [l1, l2, l3, l4, l5],
            "highs": [h1, h2, h3, h4, h5],
        }

    # Check 5 Consecutive Higher Highs AND minimum 4 candles Higher Close
    elif (h1 < h2 < h3 < h4 < h5) and is_4_higher_closes:
        exhaustion_pattern = None
        if is_doji5:
            exhaustion_pattern = "Doji (Exhaustion at High)"
        elif is_shooting_star5:
            exhaustion_pattern = "Shooting Star (Push Down From High)"

        bearish_setup = {
            "direction": "BEARISH",
            "c5_high": h5,
            "c5_low": l5,
            "c5_close": c5_val,
            "c5_open": o5,
            "c5_date": c5.get("date"),
            "avg_vol": avg_vol,
            "exhaustion_pattern": exhaustion_pattern,
            "lows": [l1, l2, l3, l4, l5],
            "highs": [h1, h2, h3, h4, h5],
        }

    return bullish_setup, bearish_setup


def _run_hourly_historical_evaluation(kite):
    """
    Runs after each completed 1-Hour candle to scan all Nifty 500 Cash stocks.
    Identifies 5-candle setups, sends exhaustion alerts if C5 is Doji/Hammer,
    and registers active breakout watches for the 6th candle.
    """
    now = datetime.now(IST)
    if not _is_market_open(now):
        return

    current_slot = _get_current_1h_slot(now)
    from_time = now - timedelta(days=6)
    to_time = now

    print(f"[1H REVERSAL] Scanning 1-Hour candles for {len(_stock_metadata)} Nifty 500 stocks ({current_slot})...")

    with _state_lock:
        tokens_to_scan = list(_stock_metadata.items())

    new_watches_count = 0

    for token, meta in tokens_to_scan:
        try:
            candles = kite.historical_data(token, from_time, to_time, "60minute")
            if not candles or len(candles) < 5:
                continue

            bullish_setup, bearish_setup = _evaluate_5_candles_reversal(candles, now, meta)

            if bullish_setup:
                sym = meta["symbol"]
                h5 = bullish_setup["c5_high"]
                l5 = bullish_setup["c5_low"]
                c5 = bullish_setup["c5_close"]
                avg_vol = bullish_setup["avg_vol"]
                pattern = bullish_setup["exhaustion_pattern"]

                # Trigger A: 5th Candle Exhaustion Alert (Bottom)
                if pattern:
                    alert_key = f"EXHAUSTION_{current_slot}_{token}_BULLISH"
                    msg = (
                        f"⚡ *1H REVERSAL SIGNAL: BOTTOM EXHAUSTION (NIFTY 500)*\n"
                        f"Stock       : *{sym}* (₹{c5:.2f})\n"
                        f"Setup       : *5 Lower Lows + ≥4 Lower Closes*\n"
                        f"Pattern     : *{pattern}* (5th Candle)\n"
                        f"━━━━━━━━━━━━━━━━━━━\n"
                        f"5th 1H High : *₹{h5:.2f}*\n"
                        f"5th 1H Low  : *₹{l5:.2f}*\n"
                        f"Close Price : *₹{c5:.2f}*\n"
                        f"Avg 1H Vol  : *{int(avg_vol):,}*\n"
                        f"Plan        : *Watch For Breakout Above ₹{h5:.2f}*\n"
                        f"⏰ Time     : {now.strftime('%H:%M:%S')} IST"
                    )
                    _send_reversal_alert(msg, alert_key)

                # Register active watch for 6th candle breakout
                with _state_lock:
                    _active_breakout_watches[token] = {
                        "direction": "BULLISH",
                        "c5_high": h5,
                        "c5_low": l5,
                        "c5_close": c5,
                        "avg_vol": avg_vol,
                        "symbol": sym,
                        "name": meta["name"],
                        "slot": current_slot,
                        "alerted": False,
                    }
                new_watches_count += 1

            elif bearish_setup:
                sym = meta["symbol"]
                h5 = bearish_setup["c5_high"]
                l5 = bearish_setup["c5_low"]
                c5 = bearish_setup["c5_close"]
                avg_vol = bearish_setup["avg_vol"]
                pattern = bearish_setup["exhaustion_pattern"]

                # Trigger A: 5th Candle Exhaustion Alert (Top)
                if pattern:
                    alert_key = f"EXHAUSTION_{current_slot}_{token}_BEARISH"
                    msg = (
                        f"⚡ *1H REVERSAL SIGNAL: TOP EXHAUSTION (NIFTY 500)*\n"
                        f"Stock       : *{sym}* (₹{c5:.2f})\n"
                        f"Setup       : *5 Higher Highs + ≥4 Higher Closes*\n"
                        f"Pattern     : *{pattern}* (5th Candle)\n"
                        f"━━━━━━━━━━━━━━━━━━━\n"
                        f"5th 1H High : *₹{h5:.2f}*\n"
                        f"5th 1H Low  : *₹{l5:.2f}*\n"
                        f"Close Price : *₹{c5:.2f}*\n"
                        f"Avg 1H Vol  : *{int(avg_vol):,}*\n"
                        f"Plan        : *Watch For Breakdown Below ₹{l5:.2f}*\n"
                        f"⏰ Time     : {now.strftime('%H:%M:%S')} IST"
                    )
                    _send_reversal_alert(msg, alert_key)

                # Register active watch for 6th candle breakdown
                with _state_lock:
                    _active_breakout_watches[token] = {
                        "direction": "BEARISH",
                        "c5_high": h5,
                        "c5_low": l5,
                        "c5_close": c5,
                        "avg_vol": avg_vol,
                        "symbol": sym,
                        "name": meta["name"],
                        "slot": current_slot,
                        "alerted": False,
                    }
                new_watches_count += 1

            # Micro-throttle to respect Kite REST API limits smoothly
            time.sleep(0.06)

        except Exception:
            continue

    print(f"[1H REVERSAL] Completed hourly scan. Active Breakout Watches: {new_watches_count}")


def _reversal_hourly_scheduler_loop(kite):
    """
    Triggers hourly evaluation at completed candle marks:
    10:15:30, 11:15:30, 12:15:30, 13:15:30, 14:15:30, 15:15:30.
    """
    last_evaluated_slot = None

    while True:
        try:
            now = datetime.now(IST)
            if not _is_market_open(now):
                time.sleep(60)
                continue

            current_slot = _get_current_1h_slot(now)

            # Trigger check right after completed hour candle (at minute 15, e.g. 10:15, 11:15, etc.)
            # Also run initial scan upon startup
            if last_evaluated_slot is None or (now.minute >= 15 and current_slot != last_evaluated_slot):
                last_evaluated_slot = current_slot
                _run_hourly_historical_evaluation(kite)

            time.sleep(10)

        except Exception as e:
            print(f"[1H REVERSAL] Scheduler loop error: {e}")
            time.sleep(30)


def _check_live_tick_reversal(token, ltp, now):
    """
    Evaluates real-time WebSocket tick against active breakout/breakdown watches.
    Triggers immediately when 6th candle crosses 5th candle High/Low.
    """
    with _state_lock:
        watch = _active_breakout_watches.get(token)
        if not watch or watch.get("alerted", False):
            return

        candle_state = _current_1h_candle.get(token)
        if not candle_state:
            return

        c6_open = candle_state.get("open", 0.0)
        if c6_open <= 0:
            return

        direction = watch["direction"]
        c5_high = watch["c5_high"]
        c5_low = watch["c5_low"]
        sym = watch["symbol"]
        slot = watch["slot"]
        avg_vol = watch.get("avg_vol", 0.0)

    # 1. Bullish Breakout: 6th candle is Green (ltp > c6_open) and crosses above 5th High
    if direction == "BULLISH":
        if ltp > c6_open and ltp > c5_high:
            with _state_lock:
                watch["alerted"] = True

            alert_key = f"BREAKOUT_{slot}_{token}_BULLISH"
            msg = (
                f"🚀 *1H 5-CANDLE REVERSAL BREAKOUT (NIFTY 500)*\n"
                f"Stock       : *{sym}* (₹{ltp:.2f})\n"
                f"Setup       : *5 Lower Lows + ≥4 Lower Closes Broken*\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"5th 1H High : *₹{c5_high:.2f}* (Crossed Above ▲)\n"
                f"Current LTP : *₹{ltp:.2f}* (Green 1H Candle)\n"
                f"Pattern SL  : *₹{c5_low:.2f}* (5th Low)\n"
                f"Avg 1H Vol  : *{int(avg_vol):,}*\n"
                f"Action      : *BUY / LONG (NIFTY 500 CASH)*\n"
                f"⏰ Time     : {now.strftime('%H:%M:%S')} IST"
            )
            print(f"[1H REVERSAL BREAKOUT] Triggered {sym} BULLISH at ₹{ltp:.2f}")
            _send_reversal_alert(msg, alert_key)

    # 2. Bearish Breakdown: 6th candle is Red (ltp < c6_open) and crosses below 5th Low
    elif direction == "BEARISH":
        if ltp < c6_open and ltp < c5_low:
            with _state_lock:
                watch["alerted"] = True

            alert_key = f"BREAKDOWN_{slot}_{token}_BEARISH"
            msg = (
                f"🚨 *1H 5-CANDLE REVERSAL BREAKDOWN (NIFTY 500)*\n"
                f"Stock       : *{sym}* (₹{ltp:.2f})\n"
                f"Setup       : *5 Higher Highs + ≥4 Higher Closes Broken*\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"5th 1H Low  : *₹{c5_low:.2f}* (Crossed Below ▼)\n"
                f"Current LTP : *₹{ltp:.2f}* (Red 1H Candle)\n"
                f"Pattern SL  : *₹{c5_high:.2f}* (5th High)\n"
                f"Avg 1H Vol  : *{int(avg_vol):,}*\n"
                f"Action      : *SELL / SHORT (NIFTY 500 CASH)*\n"
                f"⏰ Time     : {now.strftime('%H:%M:%S')} IST"
            )
            print(f"[1H REVERSAL BREAKDOWN] Triggered {sym} BEARISH at ₹{ltp:.2f}")
            _send_reversal_alert(msg, alert_key)


def start_one_hour_reversal_scanner(kite=None):
    """Initializes the 1-Hour 5-Candle Reversal Scanner for all 500 Nifty 500 Cash stocks."""
    global _scanner_started
    with _state_lock:
        if _scanner_started:
            return
        _scanner_started = True

    print("🚀 [1H REVERSAL] Initializing 1-Hour 5-Candle Reversal Engine (Nifty 500 Cash Stocks)...")
    from kiteconnect import KiteConnect

    token = _get_access_token()
    if not kite and token:
        try:
            kite = KiteConnect(api_key=env_config.API_KEY)
            kite.set_access_token(token)
        except Exception as e:
            print(f"[1H REVERSAL] KiteConnect initialization error: {e}")

    df = _load_instruments_df()
    if df.empty:
        print("[1H REVERSAL] instruments.csv missing or empty. Scanner cannot start.")
        return

    n500_symbols = _load_nifty500_symbols()
    if not n500_symbols:
        print("[1H REVERSAL] Nifty 500 symbols list empty or could not be loaded.")
        return

    # Filter all Nifty 500 Cash stocks on NSE
    eq_stocks = df[
        (df["segment"] == "NSE") &
        (df["instrument_type"] == "EQ") &
        (df["tradingsymbol"].isin(n500_symbols))
    ].copy()

    if eq_stocks.empty:
        print("[1H REVERSAL] No matching Nifty 500 Cash instruments found in instruments.csv.")
        return

    target_tokens = []
    with _state_lock:
        for _, row in eq_stocks.iterrows():
            tkn = int(row["instrument_token"])
            target_tokens.append(tkn)
            _stock_metadata[tkn] = {
                "name": str(row.get("name", row["tradingsymbol"])),
                "symbol": row["tradingsymbol"],
            }

    print(f"[1H REVERSAL] Subscribed to {len(target_tokens)} Nifty 500 Cash Stocks on NSE.")

    # WebSocket tick callback for 1-Hour live candle tracking and instant breakout triggers
    def on_ticks(ws, ticks):
        now = datetime.now(IST)
        current_slot = _get_current_1h_slot(now)

        for tick in ticks:
            tkn = tick.get("instrument_token")
            ltp = tick.get("last_price")
            if not tkn or not ltp or tkn not in _stock_metadata:
                continue

            with _state_lock:
                c_state = _current_1h_candle.get(tkn)
                if not c_state or c_state.get("slot") != current_slot:
                    _current_1h_candle[tkn] = {
                        "open": ltp,
                        "high": ltp,
                        "low": ltp,
                        "close": ltp,
                        "slot": current_slot,
                    }
                else:
                    c_state["close"] = ltp
                    if ltp > c_state["high"]:
                        c_state["high"] = ltp
                    if ltp < c_state["low"]:
                        c_state["low"] = ltp

            # Check if this tick triggers an active watch
            if tkn in _active_breakout_watches:
                _check_live_tick_reversal(tkn, ltp, now)

    def on_connect(ws, response):
        add_shared_tokens(target_tokens)

    register_ws_callbacks(on_connect, on_ticks)
    add_shared_tokens(target_tokens)

    # Launch Hourly Scheduler background thread
    if kite:
        threading.Thread(target=_reversal_hourly_scheduler_loop, args=(kite,), daemon=True).start()
    else:
        print("[1H REVERSAL] Warning: Kite client not provided. Hourly historical scanner waiting for token.")


if __name__ == "__main__":
    print("Testing 1-Hour 5-Candle Reversal Scanner (Nifty 500) standalone...")
    from kiteconnect import KiteConnect
    tok = _get_access_token()
    k = None
    if tok:
        k = KiteConnect(api_key=env_config.API_KEY)
        k.set_access_token(tok)
    start_one_hour_reversal_scanner(k)
    while True:
        time.sleep(1)
