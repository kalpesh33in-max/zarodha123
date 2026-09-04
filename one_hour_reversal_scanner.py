"""
1-Hour 5-Candle Institutional Reversal Scanner (All F&O Futures)
---------------------------------------------------------------
Core Logic:
1. Bullish Reversal:
   - Prior Trend: 5 consecutive 1-Hour completed candles making Lower Lows:
     Low(C1) > Low(C2) > Low(C3) > Low(C4) > Low(C5).
   - Trigger A (Exhaustion on 5th Candle):
     5th candle is a Doji (body <= 15% range) or Hammer / Pull-up from back (lower wick >= 45% range).
     Alerts immediately upon 5th candle close.
   - Trigger B (Live 6th Candle Breakout):
     6th 1-Hour candle forms Green (LTP > Open6) and crosses above 5th candle's High (LTP > High5).
     Alerts in real-time via WebSocket.

2. Bearish Reversal (Reverse Direction):
   - Prior Trend: 5 consecutive 1-Hour completed candles making Higher Highs:
     High(C1) < High(C2) < High(C3) < High(C4) < High(C5).
   - Trigger A (Exhaustion on 5th Candle):
     5th candle is a Doji (body <= 15% range) or Shooting Star / Push-down from top (upper wick >= 45% range).
     Alerts immediately upon 5th candle close.
   - Trigger B (Live 6th Candle Breakdown):
     6th 1-Hour candle forms Red (LTP < Open6) and crosses below 5th candle's Low (LTP < Low5).
     Alerts in real-time via WebSocket.

3. Coverage:
   - All F&O Future contracts (Index + Stock Futures).
4. Telegram Routing:
   - Alerts sent to @zarodastock_bot (TELE_TOKEN_STOCKS, TELE_CHAT_ID_STOCKS).
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

# Thread safety & State
_state_lock = threading.Lock()
_fut_metadata = {}          # token -> {"name": name, "symbol": symbol, "lot_size": lot_size}
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

    l1, l2, l3, l4, l5 = float(c1["low"]), float(c2["low"]), float(c3["low"]), float(c4["low"]), float(c5["low"])
    h1, h2, h3, h4, h5 = float(c1["high"]), float(c2["high"]), float(c3["high"]), float(c4["high"]), float(c5["high"])
    o5, c5_val = float(c5["open"]), float(c5["close"])

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

    # Check 5 Consecutive Lower Lows
    if l1 > l2 > l3 > l4 > l5:
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
            "exhaustion_pattern": exhaustion_pattern,
            "lows": [l1, l2, l3, l4, l5],
            "highs": [h1, h2, h3, h4, h5],
        }

    # Check 5 Consecutive Higher Highs
    elif h1 < h2 < h3 < h4 < h5:
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
            "exhaustion_pattern": exhaustion_pattern,
            "lows": [l1, l2, l3, l4, l5],
            "highs": [h1, h2, h3, h4, h5],
        }

    return bullish_setup, bearish_setup


def _run_hourly_historical_evaluation(kite):
    """
    Runs after each completed 1-Hour candle to scan all F&O Futures.
    Identifies 5-candle setups, sends exhaustion alerts if C5 is Doji/Hammer,
    and registers active breakout watches for the 6th candle.
    """
    now = datetime.now(IST)
    if not _is_market_open(now):
        return

    current_slot = _get_current_1h_slot(now)
    from_time = now - timedelta(days=6)
    to_time = now

    print(f"[1H REVERSAL] Scanning 1-Hour candles for {len(_fut_metadata)} F&O Futures ({current_slot})...")

    with _state_lock:
        tokens_to_scan = list(_fut_metadata.items())

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
                pattern = bullish_setup["exhaustion_pattern"]

                # Trigger A: 5th Candle Exhaustion Alert
                if pattern:
                    alert_key = f"EXHAUSTION_{current_slot}_{token}_BULLISH"
                    msg = (
                        f"⚡ *1H REVERSAL SIGNAL: BOTTOM EXHAUSTION*\n"
                        f"Future: *{sym}* (₹{c5:.2f})\n"
                        f"Setup: *5 Consecutive 1H Lower Lows*\n"
                        f"Pattern: *{pattern}* (5th Candle)\n"
                        f"━━━━━━━━━━━━━━━━━━━\n"
                        f"5th 1H High : *₹{h5:.2f}*\n"
                        f"5th 1H Low  : *₹{l5:.2f}*\n"
                        f"Close Price : *₹{c5:.2f}*\n"
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
                pattern = bearish_setup["exhaustion_pattern"]

                # Trigger A: 5th Candle Exhaustion Alert (Top)
                if pattern:
                    alert_key = f"EXHAUSTION_{current_slot}_{token}_BEARISH"
                    msg = (
                        f"⚡ *1H REVERSAL SIGNAL: TOP EXHAUSTION*\n"
                        f"Future: *{sym}* (₹{c5:.2f})\n"
                        f"Setup: *5 Consecutive 1H Higher Highs*\n"
                        f"Pattern: *{pattern}* (5th Candle)\n"
                        f"━━━━━━━━━━━━━━━━━━━\n"
                        f"5th 1H High : *₹{h5:.2f}*\n"
                        f"5th 1H Low  : *₹{l5:.2f}*\n"
                        f"Close Price : *₹{c5:.2f}*\n"
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
                        "symbol": sym,
                        "name": meta["name"],
                        "slot": current_slot,
                        "alerted": False,
                    }
                new_watches_count += 1

            # Micro-throttle to stay well under Kite limits
            time.sleep(0.08)

        except Exception as e:
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

    # 1. Bullish Breakout: 6th candle is Green (ltp > c6_open) and crosses above 5th High
    if direction == "BULLISH":
        if ltp > c6_open and ltp > c5_high:
            with _state_lock:
                watch["alerted"] = True

            alert_key = f"BREAKOUT_{slot}_{token}_BULLISH"
            msg = (
                f"🚀 *1H 5-CANDLE REVERSAL BREAKOUT*\n"
                f"Future: *{sym}* (₹{ltp:.2f})\n"
                f"Setup: *5 Consecutive Lower Lows Broken*\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"5th 1H High : *₹{c5_high:.2f}* (Crossed Above ▲)\n"
                f"Current LTP : *₹{ltp:.2f}* (Green 1H Candle)\n"
                f"Pattern SL  : *₹{c5_low:.2f}* (5th Low)\n"
                f"Action      : *BUY / LONG FUTURE*\n"
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
                f"🚨 *1H 5-CANDLE REVERSAL BREAKDOWN*\n"
                f"Future: *{sym}* (₹{ltp:.2f})\n"
                f"Setup: *5 Consecutive Higher Highs Broken*\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"5th 1H Low  : *₹{c5_low:.2f}* (Crossed Below ▼)\n"
                f"Current LTP : *₹{ltp:.2f}* (Red 1H Candle)\n"
                f"Pattern SL  : *₹{c5_high:.2f}* (5th High)\n"
                f"Action      : *SELL / SHORT FUTURE*\n"
                f"⏰ Time     : {now.strftime('%H:%M:%S')} IST"
            )
            print(f"[1H REVERSAL BREAKDOWN] Triggered {sym} BEARISH at ₹{ltp:.2f}")
            _send_reversal_alert(msg, alert_key)


def start_one_hour_reversal_scanner(kite=None):
    """Initializes the 1-Hour 5-Candle Reversal Scanner for all F&O Futures."""
    global _scanner_started
    with _state_lock:
        if _scanner_started:
            return
        _scanner_started = True

    print("🚀 [1H REVERSAL] Initializing 1-Hour 5-Candle Reversal Engine (All F&O Futures)...")
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

    # Filter all near-month F&O Futures
    futs = df[(df["segment"] == "NFO-FUT") & (df["name"].notna())].copy()
    if futs.empty:
        print("[1H REVERSAL] No NFO-FUT instruments found.")
        return

    target_tokens = []
    with _state_lock:
        for name, rows in futs.groupby("name"):
            rows_sorted = rows.sort_values(by="expiry")
            near_fut = rows_sorted.iloc[0]
            tkn = int(near_fut["instrument_token"])
            target_tokens.append(tkn)
            _fut_metadata[tkn] = {
                "name": name,
                "symbol": near_fut["tradingsymbol"],
                "lot_size": int(near_fut.get("lot_size", 1)),
            }

    print(f"[1H REVERSAL] Subscribed to {len(target_tokens)} Near-Month Futures across all F&O.")

    # WebSocket tick callback for 1-Hour live candle tracking and instant breakout triggers
    def on_ticks(ws, ticks):
        now = datetime.now(IST)
        current_slot = _get_current_1h_slot(now)

        for tick in ticks:
            tkn = tick.get("instrument_token")
            ltp = tick.get("last_price")
            if not tkn or not ltp or tkn not in _fut_metadata:
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
    print("Testing 1-Hour 5-Candle Reversal Scanner standalone...")
    from kiteconnect import KiteConnect
    tok = _get_access_token()
    k = None
    if tok:
        k = KiteConnect(api_key=env_config.API_KEY)
        k.set_access_token(tok)
    start_one_hour_reversal_scanner(k)
    while True:
        time.sleep(1)
