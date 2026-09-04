"""
Multi-Timeframe (1H, 1D, 1W) 5-Candle Institutional Reversal Scanner (Nifty 500 Cash Stocks)
---------------------------------------------------------------------------------------------
Core Strategy across 1-Hour (1H), Daily (1D), and Weekly (1W):

1. Bullish Reversal:
   - Prior Trend: 5 consecutive completed candles making Lower Lows:
     Low(C1) > Low(C2) > Low(C3) > Low(C4) > Low(C5).
   - Close Confirmation: Minimum 4 consecutive candles making Lower Closes:
     (Close1 > Close2 > Close3 > Close4) or (Close2 > Close3 > Close4 > Close5).
   - Liquidity Filter: Average volume across the 5 candles >= minimum threshold
     (1H: 10,000 | 1D: 50,000 | 1W: 250,000).
   - Trigger A (Exhaustion on 5th Candle):
     5th candle is a Doji (body <= 15% range) or Hammer / Pinbar (lower wick >= 45% range, close in upper half).
     Alerts immediately upon 5th candle close.
   - Trigger B (Live 6th Candle Breakout):
     6th candle forms Green (LTP > Open6) and crosses above 5th candle's High (LTP > High5).
     Alerts in real-time via WebSocket.

2. Bearish Reversal (Reverse Direction):
   - Prior Trend: 5 consecutive completed candles making Higher Highs:
     High(C1) < High(C2) < High(C3) < High(C4) < High(C5).
   - Close Confirmation: Minimum 4 consecutive candles making Higher Closes:
     (Close1 < Close2 < Close3 < Close4) or (Close2 < Close3 < Close4 < Close5).
   - Liquidity Filter: Average volume across the 5 candles >= minimum threshold
     (1H: 10,000 | 1D: 50,000 | 1W: 250,000).
   - Trigger A (Exhaustion on 5th Candle):
     5th candle is a Doji (body <= 15% range) or Shooting Star (upper wick >= 45% range, close in lower half).
     Alerts immediately upon 5th candle close.
   - Trigger B (Live 6th Candle Breakdown):
     6th candle forms Red (LTP < Open6) and crosses below 5th candle's Low (LTP < Low5).
     Alerts in real-time via WebSocket.

3. Timeframes Covered:
   - 1H : 60-Minute candles (Evaluated hourly, live 6th 1H candle tracked via WebSocket)
   - 1D : Daily candles (Evaluated daily/hourly, live today's candle tracked via WebSocket)
   - 1W : Weekly candles (Evaluated weekly/hourly, live this week's candle tracked via WebSocket)

4. Universe:
   - All 500 Nifty 500 Cash stocks (NSE segment: EQ).

5. Telegram Routing:
   - Alerts sent to channel -1004326717783 (Ai scanner allert) in place of @zarodastock_bot.
"""

import os
import time
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict
import pandas as pd

import env_config
from telegram_utils import send_telegram_message
from websocket_flow import register_ws_callbacks, add_shared_tokens

IST = ZoneInfo("Asia/Kolkata")
MIN_1H_AVG_VOLUME = int(os.getenv("MIN_1H_AVG_VOLUME", "10000"))
MIN_1D_AVG_VOLUME = int(os.getenv("MIN_1D_AVG_VOLUME", "50000"))
MIN_1W_AVG_VOLUME = int(os.getenv("MIN_1W_AVG_VOLUME", "250000"))
MIN_STOCK_PRICE = float(os.getenv("MIN_REVERSAL_STOCK_PRICE", "500.0"))

# Thread safety & State
_state_lock = threading.Lock()
_stock_metadata = {}          # token -> {"name": name, "symbol": symbol}
_active_breakout_watches = {  # timeframe -> token -> dict of watch parameters
    "1H": {},
    "1D": {},
    "1W": {},
}
_current_1h_candle = {}       # token -> {"open", "high", "low", "close", "slot"}
_current_1d_candle = {}       # token -> {"open", "high", "low", "close", "date"}
_week_first_day_open = {}     # token -> float (Monday's or week's opening price)
_alerted_keys = set()         # Deduplication set for alert keys
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
        print(f"[REVERSAL SCANNER] Failed to fetch Nifty 500 list from NSE: {e}")

    return set()


def _is_market_open(now):
    if now.weekday() > 4 or now.date().isoformat() in getattr(env_config, "NSE_HOLIDAYS", set()):
        return False
    t = now.time()
    return datetime.strptime("09:15", "%H:%M").time() <= t <= datetime.strptime("15:30", "%H:%M").time()


def _get_current_1h_slot(now):
    """
    Returns a unique string identifying the current 1-Hour slot during NSE hours.
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
    """
    Dispatches alert to channel -1004326717783 (Ai scanner allert) with strict deduplication.
    Tries candidate bot tokens (STOCKS, MAIN, BN) to ensure delivery regardless of which bot is administrator.
    """
    with _state_lock:
        if alert_key in _alerted_keys:
            return
        _alerted_keys.add(alert_key)

    target_chat = getattr(
        env_config,
        "TELE_CHAT_ID_REVERSAL",
        getattr(env_config, "TELE_CHAT_ID_AI_SCANNER", "-1004326717783"),
    )

    candidate_tokens = []
    for tok in [
        getattr(env_config, "TELE_TOKEN_STOCKS", None),
        getattr(env_config, "TELE_TOKEN", None),
        getattr(env_config, "TELE_TOKEN_BN", None),
    ]:
        if tok and tok not in candidate_tokens:
            candidate_tokens.append(tok)

    sent = False
    last_err = None
    for tok in candidate_tokens:
        try:
            res = send_telegram_message(message, chat_id=target_chat, token=tok)
            if res and res.get("ok"):
                sent = True
                print(f"[REVERSAL SCANNER] Alert delivered to {target_chat} (Ai scanner allert)")
                break
            elif res and not res.get("ok"):
                last_err = res.get("description", "Unknown error")
        except Exception as e:
            last_err = str(e)

    if not sent:
        print(
            f"[REVERSAL SCANNER] ⚠️ Could not deliver alert to channel {target_chat} (Ai scanner allert): {last_err}. "
            f"Please ensure @zarodastock_bot (or your Telegram bot) is added as Administrator with 'Post Messages' permission in channel {target_chat}."
        )
        # Fallback to private chat so setup alert is not missed if channel bot admin setup is pending
        fallback_chat = getattr(env_config, "TELE_CHAT_ID_STOCKS", env_config.TELE_CHAT_ID)
        stocks_tok = getattr(env_config, "TELE_TOKEN_STOCKS", env_config.TELE_TOKEN)
        if fallback_chat and str(fallback_chat) != str(target_chat):
            try:
                send_telegram_message(
                    f"⚠️ [Ai scanner allert channel {target_chat} admin pending]\n\n{message}",
                    chat_id=fallback_chat,
                    token=stocks_tok,
                )
            except Exception:
                pass


def _evaluate_5_candles(completed, now, min_avg_vol):
    """
    Core Evaluation Engine for 5-Candle Reversal across any timeframe (1H, 1D, 1W):
    1. Checks for 5 consecutive Lower Lows (Bullish) or Higher Highs (Bearish).
    2. Checks for minimum 4 consecutive Lower Closes (Bullish) or Higher Closes (Bearish).
    3. Checks liquidity (avg volume across 5 candles >= min_avg_vol).
    4. Identifies Exhaustion patterns on the 5th candle (Doji, Hammer, Shooting Star).
    Returns (bullish_setup, bearish_setup).
    """
    if len(completed) < 5:
        return None, None

    c1, c2, c3, c4, c5 = completed[-5:]
    o5, c5_val = float(c5["open"]), float(c5["close"])

    # Price Filter: Strictly ignore stocks below ₹500
    if c5_val < MIN_STOCK_PRICE:
        return None, None

    total_vol = sum(c.get("volume", 0) for c in (c1, c2, c3, c4, c5))
    avg_vol = total_vol / 5.0
    if avg_vol < min_avg_vol:
        return None, None

    l1, l2, l3, l4, l5 = float(c1["low"]), float(c2["low"]), float(c3["low"]), float(c4["low"]), float(c5["low"])
    h1, h2, h3, h4, h5 = float(c1["high"]), float(c2["high"]), float(c3["high"]), float(c4["high"]), float(c5["high"])

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


def _aggregate_weekly_candles(daily_candles, now):
    """
    Groups daily candles into completed ISO calendar weeks.
    Returns (completed_weeks, current_week_open).
    """
    if not daily_candles:
        return [], None

    curr_year, curr_week, _ = now.isocalendar()
    weeks_map = defaultdict(list)
    current_week_days = []

    for c in daily_candles:
        c_date = c.get("date")
        if not c_date:
            continue
        iso_year, iso_week, _ = c_date.isocalendar()
        key = (iso_year, iso_week)
        if key < (curr_year, curr_week):
            weeks_map[key].append(c)
        elif key == (curr_year, curr_week):
            current_week_days.append(c)

    completed_weeks = []
    for key in sorted(weeks_map.keys()):
        days = weeks_map[key]
        if not days:
            continue
        completed_weeks.append({
            "date": days[0]["date"],
            "open": float(days[0]["open"]),
            "high": max(float(d["high"]) for d in days),
            "low": min(float(d["low"]) for d in days),
            "close": float(days[-1]["close"]),
            "volume": sum(int(d.get("volume", 0)) for d in days),
        })

    current_week_open = float(current_week_days[0]["open"]) if current_week_days else None
    return completed_weeks, current_week_open


def _get_completed_daily_candles(daily_candles, now):
    """
    Returns daily candles completed strictly before today.
    """
    if not daily_candles:
        return []

    today_date = now.date()
    completed = []
    for c in daily_candles:
        c_date = c.get("date")
        if not c_date:
            continue
        c_d = c_date.date() if hasattr(c_date, "date") else c_date
        if c_d < today_date:
            completed.append(c)
    return completed


def _run_hourly_historical_evaluation(kite):
    """
    Scans 1-Hour completed candles across all Nifty 500 stocks.
    Identifies 5-candle setups, sends exhaustion alerts if C5 is Doji/Hammer,
    and registers active breakout watches for the 6th 1H candle.
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

            completed = []
            for c in candles:
                c_date = c.get("date")
                if c_date and c_date + timedelta(minutes=60) <= now:
                    completed.append(c)

            if len(completed) < 5:
                continue

            bullish_setup, bearish_setup = _evaluate_5_candles(completed, now, MIN_1H_AVG_VOLUME)

            if bullish_setup:
                sym = meta["symbol"]
                h5 = bullish_setup["c5_high"]
                l5 = bullish_setup["c5_low"]
                c5 = bullish_setup["c5_close"]
                avg_vol = bullish_setup["avg_vol"]
                pattern = bullish_setup["exhaustion_pattern"]

                # Trigger A: 5th Candle Exhaustion Alert (Bottom)
                if pattern:
                    alert_key = f"EXHAUSTION_1H_{current_slot}_{token}_BULLISH"
                    msg = (
                        f"🏷 *Ai scanner allert* 📢\n"
                        f"⚡ *1H REVERSAL SIGNAL: BOTTOM EXHAUSTION (NIFTY 500)*\n"
                        f"Stock       : *{sym}* (₹{c5:.2f})\n"
                        f"Timeframe   : *1-Hour (1H)*\n"
                        f"Setup       : *5 Lower Lows + ≥4 Lower Closes*\n"
                        f"Pattern     : *{pattern}* (5th 1H Candle)\n"
                        f"━━━━━━━━━━━━━━━━━━━\n"
                        f"5th 1H High : *₹{h5:.2f}*\n"
                        f"5th 1H Low  : *₹{l5:.2f}*\n"
                        f"Close Price : *₹{c5:.2f}*\n"
                        f"Avg 1H Vol  : *{int(avg_vol):,}*\n"
                        f"Plan        : *Watch For Breakout Above ₹{h5:.2f}*\n"
                        f"⏰ Time     : {now.strftime('%H:%M:%S')} IST"
                    )
                    _send_reversal_alert(msg, alert_key)

                # Register active watch for 6th 1H candle breakout
                with _state_lock:
                    _active_breakout_watches["1H"][token] = {
                        "direction": "BULLISH",
                        "c5_high": h5,
                        "c5_low": l5,
                        "c5_close": c5,
                        "avg_vol": avg_vol,
                        "symbol": sym,
                        "name": meta["name"],
                        "period_key": current_slot,
                        "timeframe": "1H",
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
                    alert_key = f"EXHAUSTION_1H_{current_slot}_{token}_BEARISH"
                    msg = (
                        f"🏷 *Ai scanner allert* 📢\n"
                        f"⚡ *1H REVERSAL SIGNAL: TOP EXHAUSTION (NIFTY 500)*\n"
                        f"Stock       : *{sym}* (₹{c5:.2f})\n"
                        f"Timeframe   : *1-Hour (1H)*\n"
                        f"Setup       : *5 Higher Highs + ≥4 Higher Closes*\n"
                        f"Pattern     : *{pattern}* (5th 1H Candle)\n"
                        f"━━━━━━━━━━━━━━━━━━━\n"
                        f"5th 1H High : *₹{h5:.2f}*\n"
                        f"5th 1H Low  : *₹{l5:.2f}*\n"
                        f"Close Price : *₹{c5:.2f}*\n"
                        f"Avg 1H Vol  : *{int(avg_vol):,}*\n"
                        f"Plan        : *Watch For Breakdown Below ₹{l5:.2f}*\n"
                        f"⏰ Time     : {now.strftime('%H:%M:%S')} IST"
                    )
                    _send_reversal_alert(msg, alert_key)

                # Register active watch for 6th 1H candle breakdown
                with _state_lock:
                    _active_breakout_watches["1H"][token] = {
                        "direction": "BEARISH",
                        "c5_high": h5,
                        "c5_low": l5,
                        "c5_close": c5,
                        "avg_vol": avg_vol,
                        "symbol": sym,
                        "name": meta["name"],
                        "period_key": current_slot,
                        "timeframe": "1H",
                        "alerted": False,
                    }
                new_watches_count += 1

            time.sleep(0.05)

        except Exception:
            continue

    print(f"[1H REVERSAL] Completed hourly scan. Active 1H Breakout Watches: {new_watches_count}")


def _run_daily_weekly_historical_evaluation(kite):
    """
    Scans Daily (1D) and Weekly (1W) completed candles across all Nifty 500 stocks.
    Reuses a single daily historical query to simultaneously compute:
    1. 1D Reversals (last 5 completed trading days).
    2. 1W Reversals (last 5 completed trading weeks).
    """
    now = datetime.now(IST)
    today_str = now.strftime("%Y-%m-%d")
    curr_year, curr_week, _ = now.isocalendar()
    week_str = f"{curr_year}_W{curr_week:02d}"

    print(f"[1D/1W REVERSAL] Scanning Daily & Weekly candles for {len(_stock_metadata)} Nifty 500 stocks...")

    with _state_lock:
        tokens_to_scan = list(_stock_metadata.items())

    from_time_daily = now - timedelta(days=120)
    to_time_daily = now

    count_1d = 0
    count_1w = 0

    for token, meta in tokens_to_scan:
        try:
            daily_candles = kite.historical_data(token, from_time_daily, to_time_daily, "day")
            if not daily_candles or len(daily_candles) < 5:
                continue

            sym = meta["symbol"]

            # ==========================================
            # 1. DAILY (1D) 5-CANDLE REVERSAL EVALUATION
            # ==========================================
            completed_days = _get_completed_daily_candles(daily_candles, now)
            if len(completed_days) >= 5:
                bullish_1d, bearish_1d = _evaluate_5_candles(completed_days, now, MIN_1D_AVG_VOLUME)
                if bullish_1d:
                    h5 = bullish_1d["c5_high"]
                    l5 = bullish_1d["c5_low"]
                    c5 = bullish_1d["c5_close"]
                    avg_vol = bullish_1d["avg_vol"]
                    pattern = bullish_1d["exhaustion_pattern"]

                    if pattern:
                        alert_key = f"EXHAUSTION_1D_{today_str}_{token}_BULLISH"
                        msg = (
                            f"🏷 *Ai scanner allert* 📢\n"
                            f"⚡ *1D REVERSAL SIGNAL: DAILY BOTTOM EXHAUSTION (NIFTY 500)*\n"
                            f"Stock       : *{sym}* (₹{c5:.2f})\n"
                            f"Timeframe   : *Daily (1D)*\n"
                            f"Setup       : *5 Daily Lower Lows + ≥4 Lower Closes*\n"
                            f"Pattern     : *{pattern}* (5th Day)\n"
                            f"━━━━━━━━━━━━━━━━━━━\n"
                            f"5th Day High: *₹{h5:.2f}*\n"
                            f"5th Day Low : *₹{l5:.2f}*\n"
                            f"Close Price : *₹{c5:.2f}*\n"
                            f"Avg Day Vol : *{int(avg_vol):,}*\n"
                            f"Plan        : *Watch For Daily Breakout Above ₹{h5:.2f}*\n"
                            f"⏰ Time     : {now.strftime('%H:%M:%S')} IST"
                        )
                        _send_reversal_alert(msg, alert_key)

                    with _state_lock:
                        _active_breakout_watches["1D"][token] = {
                            "direction": "BULLISH",
                            "c5_high": h5,
                            "c5_low": l5,
                            "c5_close": c5,
                            "avg_vol": avg_vol,
                            "symbol": sym,
                            "name": meta["name"],
                            "period_key": today_str,
                            "timeframe": "1D",
                            "alerted": False,
                        }
                    count_1d += 1

                elif bearish_1d:
                    h5 = bearish_1d["c5_high"]
                    l5 = bearish_1d["c5_low"]
                    c5 = bearish_1d["c5_close"]
                    avg_vol = bearish_1d["avg_vol"]
                    pattern = bearish_1d["exhaustion_pattern"]

                    if pattern:
                        alert_key = f"EXHAUSTION_1D_{today_str}_{token}_BEARISH"
                        msg = (
                            f"🏷 *Ai scanner allert* 📢\n"
                            f"⚡ *1D REVERSAL SIGNAL: DAILY TOP EXHAUSTION (NIFTY 500)*\n"
                            f"Stock       : *{sym}* (₹{c5:.2f})\n"
                            f"Timeframe   : *Daily (1D)*\n"
                            f"Setup       : *5 Daily Higher Highs + ≥4 Higher Closes*\n"
                            f"Pattern     : *{pattern}* (5th Day)\n"
                            f"━━━━━━━━━━━━━━━━━━━\n"
                            f"5th Day High: *₹{h5:.2f}*\n"
                            f"5th Day Low : *₹{l5:.2f}*\n"
                            f"Close Price : *₹{c5:.2f}*\n"
                            f"Avg Day Vol : *{int(avg_vol):,}*\n"
                            f"Plan        : *Watch For Daily Breakdown Below ₹{l5:.2f}*\n"
                            f"⏰ Time     : {now.strftime('%H:%M:%S')} IST"
                        )
                        _send_reversal_alert(msg, alert_key)

                    with _state_lock:
                        _active_breakout_watches["1D"][token] = {
                            "direction": "BEARISH",
                            "c5_high": h5,
                            "c5_low": l5,
                            "c5_close": c5,
                            "avg_vol": avg_vol,
                            "symbol": sym,
                            "name": meta["name"],
                            "period_key": today_str,
                            "timeframe": "1D",
                            "alerted": False,
                        }
                    count_1d += 1

            # ===========================================
            # 2. WEEKLY (1W) 5-CANDLE REVERSAL EVALUATION
            # ===========================================
            completed_weeks, curr_w_open = _aggregate_weekly_candles(daily_candles, now)
            if curr_w_open:
                with _state_lock:
                    _week_first_day_open[token] = curr_w_open

            if len(completed_weeks) >= 5:
                bullish_1w, bearish_1w = _evaluate_5_candles(completed_weeks, now, MIN_1W_AVG_VOLUME)
                if bullish_1w:
                    h5 = bullish_1w["c5_high"]
                    l5 = bullish_1w["c5_low"]
                    c5 = bullish_1w["c5_close"]
                    avg_vol = bullish_1w["avg_vol"]
                    pattern = bullish_1w["exhaustion_pattern"]

                    if pattern:
                        alert_key = f"EXHAUSTION_1W_{week_str}_{token}_BULLISH"
                        msg = (
                            f"🏷 *Ai scanner allert* 📢\n"
                            f"⚡ *1W REVERSAL SIGNAL: WEEKLY BOTTOM EXHAUSTION (NIFTY 500)*\n"
                            f"Stock       : *{sym}* (₹{c5:.2f})\n"
                            f"Timeframe   : *Weekly (1W)*\n"
                            f"Setup       : *5 Weekly Lower Lows + ≥4 Lower Closes*\n"
                            f"Pattern     : *{pattern}* (5th Week)\n"
                            f"━━━━━━━━━━━━━━━━━━━\n"
                            f"5th Wk High : *₹{h5:.2f}*\n"
                            f"5th Wk Low  : *₹{l5:.2f}*\n"
                            f"Close Price : *₹{c5:.2f}*\n"
                            f"Avg Wk Vol  : *{int(avg_vol):,}*\n"
                            f"Plan        : *Watch For Weekly Breakout Above ₹{h5:.2f}*\n"
                            f"⏰ Time     : {now.strftime('%H:%M:%S')} IST"
                        )
                        _send_reversal_alert(msg, alert_key)

                    with _state_lock:
                        _active_breakout_watches["1W"][token] = {
                            "direction": "BULLISH",
                            "c5_high": h5,
                            "c5_low": l5,
                            "c5_close": c5,
                            "avg_vol": avg_vol,
                            "symbol": sym,
                            "name": meta["name"],
                            "period_key": week_str,
                            "timeframe": "1W",
                            "alerted": False,
                        }
                    count_1w += 1

                elif bearish_1w:
                    h5 = bearish_1w["c5_high"]
                    l5 = bearish_1w["c5_low"]
                    c5 = bearish_1w["c5_close"]
                    avg_vol = bearish_1w["avg_vol"]
                    pattern = bearish_1w["exhaustion_pattern"]

                    if pattern:
                        alert_key = f"EXHAUSTION_1W_{week_str}_{token}_BEARISH"
                        msg = (
                            f"🏷 *Ai scanner allert* 📢\n"
                            f"⚡ *1W REVERSAL SIGNAL: WEEKLY TOP EXHAUSTION (NIFTY 500)*\n"
                            f"Stock       : *{sym}* (₹{c5:.2f})\n"
                            f"Timeframe   : *Weekly (1W)*\n"
                            f"Setup       : *5 Weekly Higher Highs + ≥4 Higher Closes*\n"
                            f"Pattern     : *{pattern}* (5th Week)\n"
                            f"━━━━━━━━━━━━━━━━━━━\n"
                            f"5th Wk High : *₹{h5:.2f}*\n"
                            f"5th Wk Low  : *₹{l5:.2f}*\n"
                            f"Close Price : *₹{c5:.2f}*\n"
                            f"Avg Wk Vol  : *{int(avg_vol):,}*\n"
                            f"Plan        : *Watch For Weekly Breakdown Below ₹{l5:.2f}*\n"
                            f"⏰ Time     : {now.strftime('%H:%M:%S')} IST"
                        )
                        _send_reversal_alert(msg, alert_key)

                    with _state_lock:
                        _active_breakout_watches["1W"][token] = {
                            "direction": "BEARISH",
                            "c5_high": h5,
                            "c5_low": l5,
                            "c5_close": c5,
                            "avg_vol": avg_vol,
                            "symbol": sym,
                            "name": meta["name"],
                            "period_key": week_str,
                            "timeframe": "1W",
                            "alerted": False,
                        }
                    count_1w += 1

            time.sleep(0.05)

        except Exception:
            continue

    print(f"[1D/1W REVERSAL] Scan complete. Active 1D Watches: {count_1d}, Active 1W Watches: {count_1w}")


def _reversal_scheduler_loop(kite):
    """
    Background scheduler for historical scans:
    - 1H scan: runs at startup and after each completed 1-Hour candle (10:15, 11:15, 12:15, 13:15, 14:15, 15:15).
    - 1D & 1W scan: runs at startup, at market open (09:16), and hourly.
    """
    last_evaluated_1h_slot = None
    last_evaluated_day = None

    while True:
        try:
            now = datetime.now(IST)
            if not _is_market_open(now):
                time.sleep(60)
                continue

            current_1h_slot = _get_current_1h_slot(now)
            today_str = now.strftime("%Y-%m-%d")

            # 1. Run 1D & 1W Evaluation on Startup and Daily at Market Open (09:16)
            if last_evaluated_day != today_str:
                last_evaluated_day = today_str
                _run_daily_weekly_historical_evaluation(kite)

            # 2. Run 1H Evaluation at 10:15, 11:15, 12:15, 13:15, 14:15, 15:15
            if last_evaluated_1h_slot is None or (now.minute >= 15 and current_1h_slot != last_evaluated_1h_slot):
                last_evaluated_1h_slot = current_1h_slot
                _run_hourly_historical_evaluation(kite)

            time.sleep(10)

        except Exception as e:
            print(f"[REVERSAL SCANNER] Scheduler loop error: {e}")
            time.sleep(30)


def _check_live_tick_reversal(timeframe, token, ltp, now):
    """
    Evaluates real-time WebSocket tick against active breakout/breakdown watches for the specified timeframe (1H, 1D, 1W).
    Triggers immediately when 6th candle crosses 5th candle High/Low.
    """
    if ltp < MIN_STOCK_PRICE:
        return

    with _state_lock:
        watch = _active_breakout_watches.get(timeframe, {}).get(token)
        if not watch or watch.get("alerted", False):
            return

        direction = watch["direction"]
        c5_high = watch["c5_high"]
        c5_low = watch["c5_low"]
        sym = watch["symbol"]
        period_key = watch["period_key"]
        avg_vol = watch.get("avg_vol", 0.0)

        if timeframe == "1H":
            c_state = _current_1h_candle.get(token, {})
            c6_open = c_state.get("open", 0.0)
        elif timeframe == "1D":
            c_state = _current_1d_candle.get(token, {})
            c6_open = c_state.get("open", 0.0)
        elif timeframe == "1W":
            c6_open = _week_first_day_open.get(token, 0.0)
            if not c6_open or c6_open <= 0:
                c_state = _current_1d_candle.get(token, {})
                c6_open = c_state.get("open", 0.0)

    if not c6_open or c6_open <= 0:
        return

    tf_title = "1-Hour (1H)" if timeframe == "1H" else ("Daily (1D)" if timeframe == "1D" else "Weekly (1W)")
    tf_label = "1H" if timeframe == "1H" else ("Day" if timeframe == "1D" else "Wk")

    # 1. Bullish Breakout: 6th candle is Green (ltp > c6_open) and crosses above 5th High
    if direction == "BULLISH":
        if ltp > c6_open and ltp > c5_high:
            with _state_lock:
                watch["alerted"] = True

            alert_key = f"BREAKOUT_{timeframe}_{period_key}_{token}_BULLISH"
            setup_text = f"5 {tf_title} Lower Lows + ≥4 Lower Closes Broken"

            msg = (
                f"🏷 *Ai scanner allert* 📢\n"
                f"🚀 *{timeframe} 5-CANDLE REVERSAL BREAKOUT (NIFTY 500)*\n"
                f"Stock       : *{sym}* (₹{ltp:.2f})\n"
                f"Timeframe   : *{tf_title}*\n"
                f"Setup       : *{setup_text}*\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"5th {tf_label} High : *₹{c5_high:.2f}* (Crossed Above ▲)\n"
                f"Current LTP : *₹{ltp:.2f}* (Green {timeframe} Candle)\n"
                f"Pattern SL  : *₹{c5_low:.2f}* (5th {tf_label} Low)\n"
                f"Avg {timeframe} Vol  : *{int(avg_vol):,}*\n"
                f"Action      : *BUY / LONG (NIFTY 500 CASH)*\n"
                f"⏰ Time     : {now.strftime('%H:%M:%S')} IST"
            )
            print(f"[{timeframe} REVERSAL BREAKOUT] Triggered {sym} BULLISH at ₹{ltp:.2f}")
            _send_reversal_alert(msg, alert_key)

    # 2. Bearish Breakdown: 6th candle is Red (ltp < c6_open) and crosses below 5th Low
    elif direction == "BEARISH":
        if ltp < c6_open and ltp < c5_low:
            with _state_lock:
                watch["alerted"] = True

            alert_key = f"BREAKDOWN_{timeframe}_{period_key}_{token}_BEARISH"
            setup_text = f"5 {tf_title} Higher Highs + ≥4 Higher Closes Broken"

            msg = (
                f"🏷 *Ai scanner allert* 📢\n"
                f"🚨 *{timeframe} 5-CANDLE REVERSAL BREAKDOWN (NIFTY 500)*\n"
                f"Stock       : *{sym}* (₹{ltp:.2f})\n"
                f"Timeframe   : *{tf_title}*\n"
                f"Setup       : *{setup_text}*\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"5th {tf_label} Low  : *₹{c5_low:.2f}* (Crossed Below ▼)\n"
                f"Current LTP : *₹{ltp:.2f}* (Red {timeframe} Candle)\n"
                f"Pattern SL  : *₹{c5_high:.2f}* (5th {tf_label} High)\n"
                f"Avg {timeframe} Vol  : *{int(avg_vol):,}*\n"
                f"Action      : *SELL / SHORT (NIFTY 500 CASH)*\n"
                f"⏰ Time     : {now.strftime('%H:%M:%S')} IST"
            )
            print(f"[{timeframe} REVERSAL BREAKDOWN] Triggered {sym} BEARISH at ₹{ltp:.2f}")
            _send_reversal_alert(msg, alert_key)


def start_one_hour_reversal_scanner(kite=None):
    """Initializes the Multi-Timeframe (1H, 1D, 1W) 5-Candle Reversal Scanner for all 500 Nifty 500 Cash stocks."""
    global _scanner_started
    with _state_lock:
        if _scanner_started:
            return
        _scanner_started = True

    print("🚀 [REVERSAL SCANNER] Initializing Multi-Timeframe (1H, 1D, 1W) 5-Candle Reversal Engine (Nifty 500 Cash Stocks)...")
    from kiteconnect import KiteConnect

    token = _get_access_token()
    if not kite and token:
        try:
            kite = KiteConnect(api_key=env_config.API_KEY)
            kite.set_access_token(token)
        except Exception as e:
            print(f"[REVERSAL SCANNER] KiteConnect initialization error: {e}")

    df = _load_instruments_df()
    if df.empty:
        print("[REVERSAL SCANNER] instruments.csv missing or empty. Scanner cannot start.")
        return

    n500_symbols = _load_nifty500_symbols()
    if not n500_symbols:
        print("[REVERSAL SCANNER] Nifty 500 symbols list empty or could not be loaded.")
        return

    # Filter all Nifty 500 Cash stocks on NSE
    eq_stocks = df[
        (df["segment"] == "NSE") &
        (df["instrument_type"] == "EQ") &
        (df["tradingsymbol"].isin(n500_symbols))
    ].copy()

    if eq_stocks.empty:
        print("[REVERSAL SCANNER] No matching Nifty 500 Cash instruments found in instruments.csv.")
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

    print(f"[REVERSAL SCANNER] Subscribed to {len(target_tokens)} Nifty 500 Cash Stocks on NSE (1H, 1D, 1W).")

    # WebSocket tick callback for 1H, 1D, 1W live candle tracking and instant breakout triggers
    def on_ticks(ws, ticks):
        now = datetime.now(IST)
        current_1h_slot = _get_current_1h_slot(now)
        today_date = now.strftime("%Y-%m-%d")

        for tick in ticks:
            tkn = tick.get("instrument_token")
            ltp = tick.get("last_price")
            if not tkn or not ltp or tkn not in _stock_metadata:
                continue

            with _state_lock:
                # 1. Update 1-Hour candle state
                c1h = _current_1h_candle.get(tkn)
                if not c1h or c1h.get("slot") != current_1h_slot:
                    _current_1h_candle[tkn] = {
                        "open": ltp,
                        "high": ltp,
                        "low": ltp,
                        "close": ltp,
                        "slot": current_1h_slot,
                    }
                else:
                    c1h["close"] = ltp
                    if ltp > c1h["high"]:
                        c1h["high"] = ltp
                    if ltp < c1h["low"]:
                        c1h["low"] = ltp

                # 2. Update Daily (1D) candle state
                c1d = _current_1d_candle.get(tkn)
                tick_open = tick.get("ohlc", {}).get("open") or ltp
                if not c1d or c1d.get("date") != today_date:
                    _current_1d_candle[tkn] = {
                        "open": tick_open,
                        "high": ltp,
                        "low": ltp,
                        "close": ltp,
                        "date": today_date,
                    }
                else:
                    c1d["close"] = ltp
                    if ltp > c1d["high"]:
                        c1d["high"] = ltp
                    if ltp < c1d["low"]:
                        c1d["low"] = ltp
                    if c1d["open"] <= 0 and tick_open > 0:
                        c1d["open"] = tick_open

                # 3. Update Weekly (1W) opening baseline if missing
                if tkn not in _week_first_day_open or _week_first_day_open[tkn] <= 0:
                    _week_first_day_open[tkn] = tick_open

            # Check real-time breakout triggers across 1H, 1D, and 1W
            if tkn in _active_breakout_watches["1H"]:
                _check_live_tick_reversal("1H", tkn, ltp, now)

            if tkn in _active_breakout_watches["1D"]:
                _check_live_tick_reversal("1D", tkn, ltp, now)

            if tkn in _active_breakout_watches["1W"]:
                _check_live_tick_reversal("1W", tkn, ltp, now)

    def on_connect(ws, response):
        add_shared_tokens(target_tokens)

    register_ws_callbacks(on_connect, on_ticks)
    add_shared_tokens(target_tokens)

    # Launch Reversal Scheduler background thread for 1H, 1D, and 1W
    if kite:
        threading.Thread(target=_reversal_scheduler_loop, args=(kite,), daemon=True).start()
    else:
        print("[REVERSAL SCANNER] Warning: Kite client not provided. Scheduler waiting for token.")


# Alias for backward compatibility
start_reversal_scanner = start_one_hour_reversal_scanner


if __name__ == "__main__":
    print("Testing Multi-Timeframe 5-Candle Reversal Scanner (1H, 1D, 1W) (Nifty 500) standalone...")
    from kiteconnect import KiteConnect
    tok = _get_access_token()
    k = None
    if tok:
        k = KiteConnect(api_key=env_config.API_KEY)
        k.set_access_token(tok)
    start_one_hour_reversal_scanner(k)
    while True:
        time.sleep(1)
