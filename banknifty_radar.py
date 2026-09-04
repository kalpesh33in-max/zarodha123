import os
import time
import math
import threading
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import env_config
from kite_rate_limiter import kite_quote
from telegram_utils import (
    send_telegram_message,
    edit_telegram_message,
    pin_telegram_message
)
from websocket_flow import register_ws_callbacks, add_shared_tokens

IST = ZoneInfo("Asia/Kolkata")

# --- Target Weights (80.5% of Bank Nifty) ---
BANK_WEIGHTS = {
    "HDFCBANK": 0.290,
    "ICICIBANK": 0.235,
    "SBIN": 0.100,
    "AXISBANK": 0.092,
    "KOTAKBANK": 0.088
}
TOTAL_BANK_WEIGHT = sum(BANK_WEIGHTS.values())  # 0.805

# State tracking
_radar_lock = threading.Lock()
_radar_instruments = {}       # name -> meta dict
_radar_candle_minute = {}     # token -> minute str
_radar_current_candle = {}    # token -> candle dict
_radar_minute_history = {}    # token -> list of completed 1-min candle dicts
_radar_day_open = {}          # token -> {price, oi, vol}
_radar_options_df = None
_radar_pinned_msg_id = None
_radar_last_scenario = None
_radar_last_date = None
_radar_started = False


def _load_instruments_data():
    if not os.path.exists("instruments.csv"):
        return pd.DataFrame()
    df = pd.read_csv("instruments.csv", low_memory=False)
    if "expiry" in df.columns:
        df["expiry_dt"] = pd.to_datetime(df["expiry"], errors="coerce")
        today_date = datetime.now(IST).date()
        df = df[df["expiry_dt"].isna() | (df["expiry_dt"].dt.date >= today_date)].copy()
    return df


def _get_active_monthly_future(df, name):
    futs = df[(df["name"] == name) & (df["segment"].str.contains("-FUT", na=False))].copy()
    if futs.empty:
        return None
    futs = futs.sort_values(by="expiry")
    return futs.iloc[0]


def _classify_order_flow(dp, doi):
    """Classifies Price & OI delta into institutional action and numeric score (-1.0 to +1.0)."""
    if dp > 0 and doi > 0:
        return "🟢 Long", 1.0       # Long Build-Up
    elif dp < 0 and doi > 0:
        return "🔴 Short", -1.0     # Short Build-Up
    elif dp >= 0 and doi <= 0:
        return "↗️ Cover", 0.5      # Short Covering
    elif dp < 0 and doi < 0:
        return "↘️ Unwind", -0.5    # Long Unwinding
    return "🟡 Flat", 0.0


def _build_scenario_text(score, bn_ltp, bn_chg, bank_summaries, floor_strike, ceil_strike, hdfc_ltp, now):
    """Formats a punchy, short 4-line live scenario report."""
    if score >= 45:
        scenario_tag = "🚀 STRONG BULLISH (Trend Breakout)"
    elif score >= 15:
        scenario_tag = "🟢 MILD BULLISH (Ceiling Test)"
    elif score >= -14:
        scenario_tag = "🟡 RANGEBOUND (Consolidation)"
    elif score >= -44:
        scenario_tag = "🔴 MILD BEARISH (Floor Test)"
    else:
        scenario_tag = "🚨 STRONG BEARISH (Breakdown Risk)"

    # Actionable trade plan based on scenario
    p_sign = "+" if bn_chg >= 0 else ""
    if score >= 15:
        if hdfc_ltp >= 720:
            plan = f"Bulls dominating. Buy CE on dips above {floor_strike}. Target: {ceil_strike + 100}."
        else:
            plan = f"Buy CE if HDFCBANK breaks ₹720. Otherwise expect {floor_strike}–{ceil_strike} range."
    elif score <= -15:
        plan = f"Bears in control. Sell on rise / Buy PE below {ceil_strike}. Target: {floor_strike - 100}."
    else:
        plan = f"Rangebound. Fade extremes between {floor_strike} Support and {ceil_strike} Resistance."

    b_items = list(bank_summaries.items())
    b_part1 = " | ".join(f"{name.replace('BANK','')}({info['ltp']:.1f}): {info['action']}" for name, info in b_items[:3])
    b_part2 = " | ".join(f"{name.replace('BANK','')}({info['ltp']:.1f}): {info['action']}" for name, info in b_items[3:])

    msg = (
        f"⚡ *BANKNIFTY 1-MIN RADAR* ⚡\n"
        f"⏰ {now.strftime('%H:%M')} IST | Fut: *{bn_ltp:.1f}* ({p_sign}{bn_chg:.1f} pts)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"1️⃣ *SCENARIO*: {scenario_tag} (*{score:+d}/100*)\n"
        f"2️⃣ *TOP 5 BANKS (80% Wt)*:\n"
        f"• {b_part1}\n"
        f"• {b_part2}\n"
        f"3️⃣ *BOUNDS*: Support *{floor_strike}* | Resistance *{ceil_strike}*\n"
        f"4️⃣ *PLAN*: {plan}"
    )
    return msg, scenario_tag


def _radar_evaluator_loop(kite):
    global _radar_pinned_msg_id, _radar_last_scenario, _radar_last_date
    print("[BANKNIFTY RADAR] Evaluator loop started (09:15 to 15:30 IST)...")

    chat_id = env_config.TELE_CHAT_ID_BN
    token = env_config.TELE_TOKEN_BN
    last_eval_minute = None

    while True:
        try:
            time.sleep(1)
            now = datetime.now(IST)

            # Skip weekends and holidays
            if now.weekday() > 4 or now.date().isoformat() in getattr(env_config, "NSE_HOLIDAYS", set()):
                time.sleep(60)
                continue

            t = now.time()
            is_market_open = (
                datetime.strptime("09:15", "%H:%M").time() <= t <= datetime.strptime("15:30", "%H:%M").time()
            )
            if not is_market_open:
                time.sleep(15)
                continue

            # Run 5 seconds after each completed minute
            current_min_str = now.strftime("%Y-%m-%d %H:%M")
            if now.second < 5 or current_min_str == last_eval_minute:
                continue
            last_eval_minute = current_min_str

            # New day reset
            if _radar_last_date != now.date():
                _radar_last_date = now.date()
                _radar_pinned_msg_id = None
                _radar_last_scenario = None

            with _radar_lock:
                if "BANKNIFTY" not in _radar_instruments:
                    continue

                bn_meta = _radar_instruments["BANKNIFTY"]
                bn_tkn = bn_meta["token"]
                bn_curr = _radar_current_candle.get(bn_tkn, {})
                bn_open_data = _radar_day_open.get(bn_tkn, {})

                bn_ltp = bn_curr.get("close", 0.0)
                if bn_ltp <= 0:
                    continue

                bn_open_price = bn_open_data.get("price", bn_ltp)
                bn_day_chg = bn_ltp - bn_open_price

                # Evaluate Bank Nifty Future flow
                bn_hist = _radar_minute_history.get(bn_tkn, [])
                if len(bn_hist) >= 2:
                    bn_dp_1m = bn_hist[-1]["close"] - bn_hist[-2]["close"]
                    bn_doi_1m = bn_hist[-1]["oi"] - bn_hist[-2]["oi"]
                else:
                    bn_dp_1m = bn_day_chg
                    bn_doi_1m = bn_curr.get("oi", 0) - bn_open_data.get("oi", 0)

                _, bn_flow_score = _classify_order_flow(bn_dp_1m, bn_doi_1m)

                # Evaluate Top 5 Banking stocks
                bank_scores = {}
                bank_summaries = {}
                hdfc_ltp = 0.0

                for name, wt in BANK_WEIGHTS.items():
                    b_meta = _radar_instruments.get(name)
                    if not b_meta:
                        continue
                    b_tkn = b_meta["token"]
                    b_curr = _radar_current_candle.get(b_tkn, {})
                    b_open_data = _radar_day_open.get(b_tkn, {})

                    b_ltp = b_curr.get("close", 0.0)
                    if name == "HDFCBANK":
                        hdfc_ltp = b_ltp

                    b_hist = _radar_minute_history.get(b_tkn, [])
                    if len(b_hist) >= 2:
                        dp_1m = b_hist[-1]["close"] - b_hist[-2]["close"]
                        doi_1m = b_hist[-1]["oi"] - b_hist[-2]["oi"]
                    else:
                        dp_1m = b_ltp - b_open_data.get("price", b_ltp)
                        doi_1m = b_curr.get("oi", 0) - b_open_data.get("oi", 0)

                    action_str, flow_score = _classify_order_flow(dp_1m, doi_1m)
                    bank_scores[name] = flow_score
                    bank_summaries[name] = {"ltp": b_ltp, "action": action_str}

                # Calculate Weighted Banks Score (-100 to +100)
                weighted_bank_score = sum(
                    BANK_WEIGHTS.get(k, 0.0) * bank_scores.get(k, 0.0) for k in BANK_WEIGHTS
                ) / TOTAL_BANK_WEIGHT * 100.0

                # Option Floor / Ceiling lookup around current ATM
                atm_strike = round(bn_ltp / 100.0) * 100
                floor_strike = atm_strike - 100
                ceil_strike = atm_strike + 100

                # Composite Power Score (-100 to +100)
                # 60% Banking Heavyweights + 40% Bank Nifty Future Flow
                composite_score = int(0.60 * weighted_bank_score + 0.40 * (bn_flow_score * 100.0))
                composite_score = max(-100, min(100, composite_score))

                msg_text, scenario_tag = _build_scenario_text(
                    composite_score, bn_ltp, bn_day_chg, bank_summaries,
                    floor_strike, ceil_strike, hdfc_ltp, now
                )

            # --- Telegram Delivery ---
            # 1. Update/Edit the Pinned Live Message
            edited = False
            if _radar_pinned_msg_id:
                res = edit_telegram_message(_radar_pinned_msg_id, msg_text, chat_id=chat_id, token=token)
                if res and res.get("ok"):
                    edited = True
                elif res and "message is not modified" in str(res.get("description", "")).lower():
                    edited = True

            if not edited:
                send_res = send_telegram_message(msg_text, chat_id=chat_id, token=token)
                if send_res and send_res.get("ok"):
                    new_id = send_res.get("result", {}).get("message_id")
                    if new_id:
                        _radar_pinned_msg_id = new_id
                        pin_telegram_message(new_id, chat_id=chat_id, token=token, disable_notification=True)

            # 2. Standalone Alert on Scenario Shift (e.g. from Rangebound to Strong Bullish)
            if _radar_last_scenario is not None and _radar_last_scenario != scenario_tag:
                shift_alert = (
                    f"🚨 *BANKNIFTY SCENARIO SHIFT* 🚨\n"
                    f"New State: *{scenario_tag}* (Score: *{composite_score:+d}*)\n"
                    f"LTP: *₹{bn_ltp:.1f}* | Support: *{floor_strike}* | Resistance: *{ceil_strike}*"
                )
                send_telegram_message(shift_alert, chat_id=chat_id, token=token)

            _radar_last_scenario = scenario_tag

        except Exception as e:
            print(f"[BANKNIFTY RADAR] Error: {e}")


def start_banknifty_radar(kite=None):
    """
    Initializes the Bank Nifty 1-Minute Live Institutional Radar Scanner.
    Subscribes Bank Nifty Future and the Top 5 Banks (80% weight) to the shared WebSocket.
    """
    global _radar_started, _radar_instruments
    with _radar_lock:
        if _radar_started:
            return
        _radar_started = True

    print("🚀 [BANKNIFTY RADAR] Initializing Bank Nifty Institutional Radar Engine...")
    df = _load_instruments_data()
    if df.empty:
        print("[BANKNIFTY RADAR] instruments.csv missing or empty.")
        return

    target_tokens = []
    instruments_to_find = ["BANKNIFTY"] + list(BANK_WEIGHTS.keys())

    for name in instruments_to_find:
        fut_row = _get_active_monthly_future(df, name)
        if fut_row is not None:
            tkn = int(fut_row["instrument_token"])
            target_tokens.append(tkn)
            _radar_instruments[name] = {
                "name": name,
                "token": tkn,
                "symbol": fut_row["tradingsymbol"],
                "lot_size": int(fut_row.get("lot_size", 1))
            }

    print(f"[BANKNIFTY RADAR] Subscribed to {len(target_tokens)} Futures for Bank Nifty & Top 5 Banks.")

    # WebSocket Tick Callback
    def on_ticks(ws, ticks):
        now = datetime.now(IST)
        current_min = now.strftime("%Y-%m-%d %H:%M")

        with _radar_lock:
            for tick in ticks:
                tkn = tick.get("instrument_token")
                if tkn not in _radar_candle_minute and not any(m["token"] == tkn for m in _radar_instruments.values()):
                    continue

                ltp = tick.get("last_price", 0.0)
                vol = tick.get("volume_traded") or tick.get("volume", 0)
                oi = tick.get("oi", 0)

                # Initialize Day Open baseline on first tick
                if tkn not in _radar_day_open and ltp > 0:
                    _radar_day_open[tkn] = {"price": ltp, "oi": oi, "vol": vol}

                if tkn not in _radar_candle_minute:
                    _radar_candle_minute[tkn] = current_min
                    _radar_current_candle[tkn] = {
                        "open": ltp, "high": ltp, "low": ltp, "close": ltp,
                        "volume": vol, "oi": oi
                    }

                # 1-Minute Candle Roll-Over
                if _radar_candle_minute[tkn] != current_min:
                    c = _radar_current_candle[tkn]
                    if tkn not in _radar_minute_history:
                        _radar_minute_history[tkn] = []
                    _radar_minute_history[tkn].append(dict(c))
                    if len(_radar_minute_history[tkn]) > 30:
                        _radar_minute_history[tkn].pop(0)

                    _radar_candle_minute[tkn] = current_min
                    _radar_current_candle[tkn] = {
                        "open": ltp, "high": ltp, "low": ltp, "close": ltp,
                        "volume": vol, "oi": oi
                    }
                else:
                    c = _radar_current_candle[tkn]
                    c["close"] = ltp
                    c["high"] = max(c["high"], ltp)
                    c["low"] = min(c["low"], ltp)
                    c["volume"] = vol
                    c["oi"] = oi

    def on_connect(ws, response):
        add_shared_tokens(target_tokens)

    register_ws_callbacks(on_connect, on_ticks)
    add_shared_tokens(target_tokens)

    # Start the 1-Minute Evaluator Loop
    threading.Thread(target=_radar_evaluator_loop, args=(kite,), daemon=True).start()
    print("✅ [BANKNIFTY RADAR] Bank Nifty Institutional Radar Active.")
