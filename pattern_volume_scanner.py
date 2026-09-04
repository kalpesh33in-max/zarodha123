import os
import time
import math
import threading
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import env_config
from kite_rate_limiter import kite_quote, kite_historical_data
from telegram_utils import send_telegram_message
from websocket_flow import register_ws_callbacks, add_shared_tokens

IST = ZoneInfo("Asia/Kolkata")

# --- Target Watchlists ---
PATTERN_INDICES = ["BANKNIFTY", "NIFTY", "SENSEX", "MIDCPNIFTY"]
PATTERN_COMMODITIES = ["CRUDEOILM"]
PATTERN_STOCKS = [
    "HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "KOTAKBANK",
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "BAJAJ-AUTO",
    "BAJAJFINSV", "BHARTIARTL", "BRITANNIA", "CIPLA", "EICHERMOT",
    "GRASIM", "HCLTECH", "HEROMOTOCO", "HINDUNILVR", "INFY",
    "LT", "M&M", "MARUTI", "NESTLEIND", "RELIANCE",
    "SBILIFE", "SUNPHARMA", "TATACONSUM", "TCS", "TITAN",
    "TRENT", "ULTRACEMCO"
]

ALL_TRACKED_NAMES = sorted(set(PATTERN_INDICES + PATTERN_COMMODITIES + PATTERN_STOCKS))

# Configuration
MIN_SCORE_THRESHOLD = int(os.getenv("PATTERN_MIN_SCORE", "65"))
ALERT_COOLDOWN_SECONDS = int(os.getenv("PATTERN_ALERT_COOLDOWN", "600"))

# State
_state_lock = threading.Lock()
_candle_history = {}          # token -> list of candle dicts (up to 60 candles)
_current_candle_state = {}    # token -> dict representing current open candle
_current_candle_minute = {}   # token -> string YYYY-MM-DD HH:MM
_token_meta = {}              # token -> meta dict
_symbol_to_token = {}         # name -> token
_options_cache = {}           # name -> options dataframe
_last_alert_times = {}        # alert_key -> timestamp
_scanner_started = False

# --- Helper Functions ---

def _get_access_token():
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


def _load_instruments_df():
    if not os.path.exists("instruments.csv"):
        return pd.DataFrame()
    df = pd.read_csv("instruments.csv", low_memory=False)
    if "expiry" in df.columns:
        df["expiry_dt"] = pd.to_datetime(df["expiry"], errors="coerce")
        today_date = datetime.now(IST).date()
        df = df[df["expiry_dt"].isna() | (df["expiry_dt"].dt.date >= today_date)].copy()
    return df


def _calculate_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    diff = np.diff(closes)
    gains = np.where(diff > 0, diff, 0.0)
    losses = np.where(diff < 0, -diff, 0.0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for i in range(period, len(diff)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100.0 - (100.0 / (1.0 + rs)))


def _calculate_atr(highs, lows, closes, period=14):
    if len(closes) < 2:
        return 1.0
    trs = []
    for i in range(1, len(closes)):
        h = highs[i]
        l = lows[i]
        prev_c = closes[i - 1]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(tr)
    if not trs:
        return 1.0
    if len(trs) < period:
        return float(np.mean(trs))
    return float(np.mean(trs[-period:]))


def _calculate_vwap(candles):
    total_vp = 0.0
    total_vol = 0.0
    for c in candles:
        v = c.get("volume", 0)
        tp = (c["high"] + c["low"] + c["close"]) / 3.0
        total_vp += tp * v
        total_vol += v
    if total_vol <= 0:
        return candles[-1]["close"]
    return total_vp / total_vol


# --- 10 Pattern Detection Logic ---

def detect_patterns(candles):
    """
    Evaluates the last 15-20 candles for all 10 Price Action & Volume Breakout patterns.
    Returns a list of pattern match dictionaries.
    """
    if len(candles) < 10:
        return []

    c = candles[-1]       # Current completed candle
    prev = candles[-2]    # Previous completed candle
    closes = [x["close"] for x in candles]
    highs = [x["high"] for x in candles]
    lows = [x["low"] for x in candles]
    volumes = [x["volume"] for x in candles]

    # Indicator values
    avg_vol_20 = np.mean(volumes[-21:-1]) if len(volumes) >= 21 else (np.mean(volumes[:-1]) if len(volumes) > 1 else 1.0)
    avg_vol_20 = max(1.0, float(avg_vol_20))
    rvol = float(c["volume"]) / avg_vol_20
    candle_range = max(0.01, c["high"] - c["low"])
    body = abs(c["close"] - c["open"])
    upper_wick = c["high"] - max(c["open"], c["close"])
    lower_wick = min(c["open"], c["close"]) - c["low"]
    atr14 = _calculate_atr(highs, lows, closes, 14)
    vwap = _calculate_vwap(candles)
    rsi = _calculate_rsi(closes, 14)

    c_ranges = [max(0.01, x["high"] - x["low"]) for x in candles]

    detected = []

    # --------------------------------------------------------------------------
    # 1. NR7 + High Volume Breakout
    # NR7 = Narrowest range among last 7 candles.
    # Current or previous candle was NR7, and current candle breaks the range with high volume.
    # --------------------------------------------------------------------------
    if len(candles) >= 8:
        last_7_ranges = c_ranges[-8:-1]
        prev_is_nr7 = (c_ranges[-2] == min(last_7_ranges))
        curr_is_nr7 = (c_ranges[-1] == min(c_ranges[-7:]))
        
        if (prev_is_nr7 or curr_is_nr7) and rvol >= 1.8:
            high_6 = max(highs[-8:-1])
            low_6 = min(lows[-8:-1])
            if c["close"] > high_6:
                detected.append({
                    "id": 1,
                    "name": "NR7 + High Volume Breakout",
                    "direction": "BULLISH",
                    "base_score": 25,
                    "level_label": f"Broke 7-Candle High ₹{high_6:.2f}",
                    "level": high_6,
                    "note": f"Compression range broken with {rvol:.1f}x RVOL"
                })
            elif c["close"] < low_6:
                detected.append({
                    "id": 1,
                    "name": "NR7 + High Volume Breakdown",
                    "direction": "BEARISH",
                    "base_score": 25,
                    "level_label": f"Broke 7-Candle Low ₹{low_6:.2f}",
                    "level": low_6,
                    "note": f"Compression range broken with {rvol:.1f}x RVOL"
                })

    # --------------------------------------------------------------------------
    # 2. NR4 + Volume Expansion
    # NR4 = Narrowest range of last 4 candles.
    # Current candle expands > 1.4x previous 4-candle average range with RVOL >= 2.0.
    # --------------------------------------------------------------------------
    if len(candles) >= 6:
        prev_4_ranges = c_ranges[-5:-1]
        avg_range_4 = np.mean(prev_4_ranges)
        min_range_4 = min(prev_4_ranges)
        if c_ranges[-2] == min_range_4 and candle_range > 1.4 * avg_range_4 and rvol >= 2.0:
            if c["close"] > max(highs[-5:-1]):
                detected.append({
                    "id": 2,
                    "name": "NR4 + Volume Expansion",
                    "direction": "BULLISH",
                    "base_score": 20,
                    "level_label": f"Range Expansion ({candle_range:.2f} vs Avg {avg_range_4:.2f})",
                    "level": max(highs[-5:-1]),
                    "note": f"NR4 compression to high-volume expansion ({rvol:.1f}x Vol)"
                })
            elif c["close"] < min(lows[-5:-1]):
                detected.append({
                    "id": 2,
                    "name": "NR4 + Volume Expansion",
                    "direction": "BEARISH",
                    "base_score": 20,
                    "level_label": f"Range Expansion ({candle_range:.2f} vs Avg {avg_range_4:.2f})",
                    "level": min(lows[-5:-1]),
                    "note": f"NR4 compression to high-volume breakdown ({rvol:.1f}x Vol)"
                })

    # --------------------------------------------------------------------------
    # 3. Inside Bar + High Volume Breakout
    # Candle -2 is inside mother candle -3. Current candle -1 breaks out with RVOL >= 1.8.
    # --------------------------------------------------------------------------
    if len(candles) >= 4:
        mother = candles[-3]
        inside = candles[-2]
        is_inside = (inside["high"] <= mother["high"]) and (inside["low"] >= mother["low"])
        if is_inside and rvol >= 1.8:
            if c["close"] > mother["high"]:
                detected.append({
                    "id": 3,
                    "name": "Inside Bar + High Volume Breakout",
                    "direction": "BULLISH",
                    "base_score": 20,
                    "level_label": f"Broke Mother High ₹{mother['high']:.2f}",
                    "level": mother["high"],
                    "note": f"Inside bar contraction resolved to the upside with {rvol:.1f}x Vol"
                })
            elif c["close"] < mother["low"]:
                detected.append({
                    "id": 3,
                    "name": "Inside Bar + High Volume Breakdown",
                    "direction": "BEARISH",
                    "base_score": 20,
                    "level_label": f"Broke Mother Low ₹{mother['low']:.2f}",
                    "level": mother["low"],
                    "note": f"Inside bar contraction resolved to the downside with {rvol:.1f}x Vol"
                })

    # --------------------------------------------------------------------------
    # 4. Double Inside Bar Breakout
    # Two consecutive inside bars inside Mother candle -4. Breakout gives fast directional move.
    # --------------------------------------------------------------------------
    if len(candles) >= 5:
        mother = candles[-4]
        ib1 = candles[-3]
        ib2 = candles[-2]
        is_ib1 = (ib1["high"] <= mother["high"]) and (ib1["low"] >= mother["low"])
        is_ib2 = (ib2["high"] <= mother["high"]) and (ib2["low"] >= mother["low"])
        if is_ib1 and is_ib2 and rvol >= 1.8:
            if c["close"] > mother["high"]:
                detected.append({
                    "id": 4,
                    "name": "Double Inside Bar Breakout",
                    "direction": "BULLISH",
                    "base_score": 25,
                    "level_label": f"Broke Double-Inside High ₹{mother['high']:.2f}",
                    "level": mother["high"],
                    "note": f"High coiling compression released bullishly with {rvol:.1f}x Vol"
                })
            elif c["close"] < mother["low"]:
                detected.append({
                    "id": 4,
                    "name": "Double Inside Bar Breakdown",
                    "direction": "BEARISH",
                    "base_score": 25,
                    "level_label": f"Broke Double-Inside Low ₹{mother['low']:.2f}",
                    "level": mother["low"],
                    "note": f"High coiling compression released bearishly with {rvol:.1f}x Vol"
                })

    # --------------------------------------------------------------------------
    # 5. Two High-Volume Reversal Candles
    # Two consecutive high volume candles in opposite directions.
    # --------------------------------------------------------------------------
    if len(candles) >= 3:
        p_rvol = float(prev["volume"]) / avg_vol_20
        if p_rvol >= 1.8 and rvol >= 1.8:
            # Bullish Reversal: Red candle followed by strong Green candle
            if (prev["close"] < prev["open"]) and (c["close"] > c["open"]) and (c["close"] >= prev["open"] * 0.999):
                detected.append({
                    "id": 5,
                    "name": "Two High-Volume Reversal Candles",
                    "direction": "BULLISH",
                    "base_score": 25,
                    "level_label": f"Engulfed Prev Close (₹{c['close']:.2f} vs ₹{prev['open']:.2f})",
                    "level": prev["high"],
                    "note": f"Selling climax absorbed by immediate high-volume green candle"
                })
            # Bearish Reversal: Green candle followed by strong Red candle
            elif (prev["close"] > prev["open"]) and (c["close"] < c["open"]) and (c["close"] <= prev["open"] * 1.001):
                detected.append({
                    "id": 5,
                    "name": "Two High-Volume Reversal Candles",
                    "direction": "BEARISH",
                    "base_score": 25,
                    "level_label": f"Engulfed Prev Open (₹{c['close']:.2f} vs ₹{prev['open']:.2f})",
                    "level": prev["low"],
                    "note": f"Buying climax rejected by immediate high-volume red candle"
                })

    # --------------------------------------------------------------------------
    # 6. Volume Climax + Reversal Candle
    # Huge volume spike (RVOL >= 3.5) followed by reversal candle.
    # --------------------------------------------------------------------------
    if len(candles) >= 3:
        p_rvol = float(prev["volume"]) / avg_vol_20
        p_body = abs(prev["close"] - prev["open"])
        p_upper_wick = prev["high"] - max(prev["open"], prev["close"])
        p_lower_wick = min(prev["open"], prev["close"]) - prev["low"]
        if p_rvol >= 3.5:
            # Bearish reversal after buying climax
            if p_upper_wick >= p_body * 0.8 and c["close"] < c["open"] and c["close"] < prev["close"]:
                detected.append({
                    "id": 6,
                    "name": "Volume Climax + Reversal Candle",
                    "direction": "BEARISH",
                    "base_score": 25,
                    "level_label": f"Climax Top Rejection at ₹{prev['high']:.2f}",
                    "level": prev["high"],
                    "note": f"Extreme volume exhaustion ({p_rvol:.1f}x) followed by reversal candle"
                })
            # Bullish reversal after selling climax
            elif p_lower_wick >= p_body * 0.8 and c["close"] > c["open"] and c["close"] > prev["close"]:
                detected.append({
                    "id": 6,
                    "name": "Volume Climax + Reversal Candle",
                    "direction": "BULLISH",
                    "base_score": 25,
                    "level_label": f"Climax Bottom Absorption at ₹{prev['low']:.2f}",
                    "level": prev["low"],
                    "note": f"Extreme volume absorption ({p_rvol:.1f}x) followed by reversal candle"
                })

    # --------------------------------------------------------------------------
    # 7. High Volume + Price Compression
    # Tight range in last 5 candles (< 1.3 * ATR) with high volume (>= 2 candles have RVOL >= 1.6).
    # --------------------------------------------------------------------------
    if len(candles) >= 6:
        high_5 = max(highs[-6:-1])
        low_5 = min(lows[-6:-1])
        range_5 = high_5 - low_5
        high_vol_count = sum(1 for v in volumes[-6:-1] if (v / avg_vol_20) >= 1.6)
        if range_5 < 1.3 * atr14 and high_vol_count >= 2:
            if c["close"] > high_5 and rvol >= 1.8:
                detected.append({
                    "id": 7,
                    "name": "High Volume + Price Compression Breakout",
                    "direction": "BULLISH",
                    "base_score": 20,
                    "level_label": f"Broke Compression Range ₹{high_5:.2f}",
                    "level": high_5,
                    "note": f"Heavy institutional order absorption in tight zone resolved upward"
                })
            elif c["close"] < low_5 and rvol >= 1.8:
                detected.append({
                    "id": 7,
                    "name": "High Volume + Price Compression Breakdown",
                    "direction": "BEARISH",
                    "base_score": 20,
                    "level_label": f"Broke Compression Range ₹{low_5:.2f}",
                    "level": low_5,
                    "note": f"Heavy institutional order absorption in tight zone resolved downward"
                })

    # --------------------------------------------------------------------------
    # 8. Consecutive High Volume + Same Direction Move
    # 3 consecutive candles in same direction, each making HH/HL or LH/LL with RVOL >= 1.4.
    # --------------------------------------------------------------------------
    if len(candles) >= 4:
        c1, c2, c3 = candles[-3], candles[-2], candles[-1]
        v1, v2, v3 = c1["volume"] / avg_vol_20, c2["volume"] / avg_vol_20, c3["volume"] / avg_vol_20
        if v1 >= 1.4 and v2 >= 1.4 and v3 >= 1.4:
            # Bullish trend burst
            if (c1["close"] > c1["open"]) and (c2["close"] > c2["open"]) and (c3["close"] > c3["open"]):
                if (c3["high"] > c2["high"] > c1["high"]) and (c3["low"] > c2["low"] > c1["low"]):
                    detected.append({
                        "id": 8,
                        "name": "Consecutive High Volume Trend Burst",
                        "direction": "BULLISH",
                        "base_score": 20,
                        "level_label": f"3 Consecutive Green Candles with {v3:.1f}x RVOL",
                        "level": c3["close"],
                        "note": f"Strong aggressive market order flow driving trend continuation"
                    })
            # Bearish trend burst
            elif (c1["close"] < c1["open"]) and (c2["close"] < c2["open"]) and (c3["close"] < c3["open"]):
                if (c3["high"] < c2["high"] < c1["high"]) and (c3["low"] < c2["low"] < c1["low"]):
                    detected.append({
                        "id": 8,
                        "name": "Consecutive High Volume Trend Breakdown",
                        "direction": "BEARISH",
                        "base_score": 20,
                        "level_label": f"3 Consecutive Red Candles with {v3:.1f}x RVOL",
                        "level": c3["close"],
                        "note": f"Strong aggressive market order selling driving breakdown continuation"
                    })

    # --------------------------------------------------------------------------
    # 9. High Volume Rejection + Reversal
    # Price touches a 20-candle extreme with long wick and RVOL >= 1.8, confirmed by next candle.
    # --------------------------------------------------------------------------
    if len(candles) >= 4:
        p_rvol = float(prev["volume"]) / avg_vol_20
        p_body = max(0.01, abs(prev["close"] - prev["open"]))
        p_upper_wick = prev["high"] - max(prev["open"], prev["close"])
        p_lower_wick = min(prev["open"], prev["close"]) - prev["low"]
        if p_rvol >= 1.8:
            # Bearish rejection at resistance
            if p_upper_wick >= 1.4 * p_body and c["close"] < prev["low"]:
                detected.append({
                    "id": 9,
                    "name": "High Volume Rejection + Reversal",
                    "direction": "BEARISH",
                    "base_score": 20,
                    "level_label": f"Rejected High at ₹{prev['high']:.2f}",
                    "level": prev["high"],
                    "note": f"Long upper wick rejection with {p_rvol:.1f}x RVOL confirmed by red close"
                })
            # Bullish rejection at support
            elif p_lower_wick >= 1.4 * p_body and c["close"] > prev["high"]:
                detected.append({
                    "id": 9,
                    "name": "High Volume Rejection + Reversal",
                    "direction": "BULLISH",
                    "base_score": 20,
                    "level_label": f"Rejected Low at ₹{prev['low']:.2f}",
                    "level": prev["low"],
                    "note": f"Long lower wick rejection with {p_rvol:.1f}x RVOL confirmed by green close"
                })

    # --------------------------------------------------------------------------
    # 10. Breakout -> Retest -> Volume Confirmation
    # Prior breakout happened 2-8 candles ago, price retested and held, now bounces with volume.
    # --------------------------------------------------------------------------
    if len(candles) >= 8:
        for i in range(len(candles) - 6, len(candles) - 2):
            b_candle = candles[i]
            b_rvol = b_candle["volume"] / avg_vol_20
            if b_rvol >= 1.8 and b_candle["close"] > b_candle["open"]:
                level = b_candle["high"]
                retest_held = all(candles[k]["low"] >= level * 0.998 for k in range(i + 1, len(candles) - 1))
                if retest_held and c["close"] > c["open"] and rvol >= 1.5 and c["close"] > level:
                    detected.append({
                        "id": 10,
                        "name": "Breakout → Retest → Volume Confirmation",
                        "direction": "BULLISH",
                        "base_score": 25,
                        "level_label": f"Retested & Bounced from ₹{level:.2f}",
                        "level": level,
                        "note": f"Breakout level held cleanly; confirmed by high-volume follow-through"
                    })
                    break

    # Calculate final scores with indicators
    results = []
    for pat in detected:
        score = pat["base_score"]
        confirmations = [f"Pattern: {pat['name']} (+{pat['base_score']})"]

        # Volume / RVOL bonus
        if rvol >= 4.0:
            score += 25
            confirmations.append(f"Extreme Volume: {rvol:.1f}x RVOL (+25)")
        elif rvol >= 2.5:
            score += 20
            confirmations.append(f"High Volume: {rvol:.1f}x RVOL (+20)")
        elif rvol >= 1.8:
            score += 15
            confirmations.append(f"Above Average Volume: {rvol:.1f}x RVOL (+15)")

        # VWAP confirmation
        if pat["direction"] == "BULLISH" and c["close"] > vwap:
            score += 15
            confirmations.append(f"VWAP: Trading Above VWAP ₹{vwap:.2f} (+15)")
        elif pat["direction"] == "BEARISH" and c["close"] < vwap:
            score += 15
            confirmations.append(f"VWAP: Trading Below VWAP ₹{vwap:.2f} (+15)")

        # RSI confirmation
        if pat["direction"] == "BULLISH" and 52 <= rsi <= 75:
            score += 15
            confirmations.append(f"RSI: Strong Bullish Momentum ({rsi:.1f}) (+15)")
        elif pat["direction"] == "BEARISH" and 25 <= rsi <= 48:
            score += 15
            confirmations.append(f"RSI: Strong Bearish Momentum ({rsi:.1f}) (+15)")

        # Candle Body Expansion
        if body / candle_range >= 0.60:
            score += 10
            confirmations.append(f"Body: Decisive Candle Body ({int(body/candle_range*100)}%) (+10)")

        final_score = min(100, score)
        results.append({
            "pattern_id": pat["id"],
            "pattern_name": pat["name"],
            "direction": pat["direction"],
            "score": final_score,
            "level_label": pat["level_label"],
            "note": pat["note"],
            "confirmations": confirmations,
            "price": c["close"],
            "volume": c["volume"],
            "avg_volume": avg_vol_20,
            "rvol": rvol,
            "vwap": vwap,
            "rsi": rsi,
        })

    return results


# --- Option Recommendation Helper ---

def _get_recommended_option(kite, name, ref_price, direction, df_opts):
    target_type = "CE" if direction == "BULLISH" else "PE"
    action_verb = "BUY CALL (CE)" if direction == "BULLISH" else "BUY PUT (PE)"

    if df_opts is None or df_opts.empty or ref_price <= 0:
        return action_verb, f"(ATM {target_type} Strike)", 0.0, 0

    opts_side = df_opts[df_opts["instrument_type"] == target_type]
    if opts_side.empty:
        return action_verb, f"(ATM {target_type} Strike)", 0.0, 0

    unique_strikes = sorted(opts_side["strike"].unique())
    if not unique_strikes:
        return action_verb, f"(ATM {target_type} Strike)", 0.0, 0

    atm_strike = min(unique_strikes, key=lambda x: abs(x - ref_price))
    idx = unique_strikes.index(atm_strike)
    selected_strikes = unique_strikes[max(0, idx - 1): min(len(unique_strikes), idx + 2)]
    target_opts = opts_side[opts_side["strike"].isin(selected_strikes)]
    if target_opts.empty:
        return action_verb, f"(ATM {target_type} Strike)", 0.0, 0

    symbols_to_quote = [f"{r['exchange']}:{r['tradingsymbol']}" for _, r in target_opts.iterrows()]
    quotes = {}
    if kite:
        try:
            quotes = kite_quote(kite, symbols_to_quote)
        except Exception:
            quotes = {}

    best_symbol = ""
    max_oi = -1
    best_ltp = 0.0
    best_strike = None

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
        atm_tag = " (ATM)" if best_strike == atm_strike else ""
        return action_verb, f"{best_symbol}{atm_tag}", best_ltp, max_oi
    return action_verb, f"(ATM {target_type} Strike)", 0.0, 0


# --- Alert Formatting & Dispatch ---

def _dispatch_pattern_alert(kite, name, match_data, df_opts):
    score = match_data["score"]
    if score < MIN_SCORE_THRESHOLD:
        return

    direction = match_data["direction"]
    pattern_name = match_data["pattern_name"]
    alert_key = f"{name}_{pattern_name}_{direction}"
    now = datetime.now(IST)
    now_ts = now.timestamp()

    with _state_lock:
        last_time = _last_alert_times.get(alert_key, 0.0)
        if now_ts - last_time < ALERT_COOLDOWN_SECONDS:
            return
        _last_alert_times[alert_key] = now_ts

    # Quality Badge
    if score >= 90:
        badge = "🔥 HIGH CONVICTION SETUP"
    elif score >= 75:
        badge = "🚀 STRONG SETUP"
    else:
        badge = "✅ GOOD SETUP"

    dir_icon = "🟢" if direction == "BULLISH" else "🔴"

    action_verb, opt_symbol, opt_ltp, opt_oi = _get_recommended_option(
        kite, name, match_data["price"], direction, df_opts
    )

    # Clean short option string
    opt_detail = f"*{opt_symbol}*"
    if opt_ltp > 0:
        opt_detail += f" @ *₹{opt_ltp:.2f}*"
    if opt_oi > 0:
        if opt_oi >= 1_000_000:
            opt_detail += f" (OI: {opt_oi/1_000_000:.2f}M)"
        elif opt_oi >= 1_000:
            opt_detail += f" (OI: {opt_oi/1_000:.1f}K)"
        else:
            opt_detail += f" (OI: {opt_oi})"

    msg = (
        f"{dir_icon} *{name}* (₹{match_data['price']:.2f}) — *{action_verb}*\n"
        f"Pattern: *{pattern_name}* (Score: *{score}/100*)\n"
        f"Level: {match_data['level_label']} | RVOL: *{match_data['rvol']:.1f}x*\n"
        f"Option: {opt_detail}\n"
        f"⏰ {now.strftime('%H:%M:%S')} IST"
    )

    chat_id = env_config.TELE_CHAT_ID_STOCKS or env_config.TELE_CHAT_ID
    token = env_config.TELE_TOKEN_STOCKS or env_config.TELE_TOKEN
    print(f"[PATTERN SCANNER] Triggered {badge} for {name} ({pattern_name}, Score={score})")
    send_telegram_message(msg, chat_id=chat_id, token=token)


# --- Candle Processing Engine ---

def _process_completed_candle(kite, token, closed_candle):
    with _state_lock:
        meta = _token_meta.get(token)
        if not meta:
            return
        if token not in _candle_history:
            _candle_history[token] = []
        hist = _candle_history[token]
        hist.append(closed_candle)
        if len(hist) > 60:
            hist.pop(0)
        if len(hist) < 8:
            return
        eval_candles = list(hist)
        name = meta["name"]
        df_opts = _options_cache.get(name)

    matches = detect_patterns(eval_candles)
    for m in matches:
        _dispatch_pattern_alert(kite, name, m, df_opts)


# --- REST Fallback Historical Candle Loader ---

def _rest_historical_polling_loop(kite):
    """
    Every 60s during trading sessions, polls 1-minute historical candles directly via Kite REST API.
    Acts as an infallible fallback if WebSocket is ever disconnected.
    """
    print("[PATTERN SCANNER] Starting 1-Minute Historical REST Fallback Engine...")
    today_date = datetime.now(IST).date()
    from_time = datetime.combine(today_date, datetime.strptime("09:15", "%H:%M").time(), tzinfo=IST)

    while True:
        try:
            now = datetime.now(IST)
            if now.weekday() > 4 or now.date().isoformat() in getattr(env_config, "NSE_HOLIDAYS", set()):
                time.sleep(60)
                continue

            t = now.time()
            is_open = (datetime.strptime("09:15", "%H:%M").time() <= t <= datetime.strptime("15:30", "%H:%M").time())
            if not is_open:
                time.sleep(30)
                continue

            with _state_lock:
                tokens_to_poll = list(_token_meta.items())

            for token, meta in tokens_to_poll:
                try:
                    # If live WebSocket ticks are actively updating this token, we don't need to hammer REST
                    hist = _candle_history.get(token, [])
                    if hist and (now - hist[-1].get("time", now)).total_seconds() < 90:
                        continue

                    candles = kite_historical_data(kite, token, from_time, now, "minute")
                    if not candles or len(candles) < 8:
                        continue

                    # Filter out partial current minute candle
                    completed = candles[:-1] if len(candles) > 1 else candles
                    norm_candles = []
                    for c in completed[-30:]:
                        norm_candles.append({
                            "open": float(c["open"]),
                            "high": float(c["high"]),
                            "low": float(c["low"]),
                            "close": float(c["close"]),
                            "volume": float(c["volume"]),
                            "time": c["date"]
                        })

                    if norm_candles:
                        with _state_lock:
                            _candle_history[token] = norm_candles
                            name = meta["name"]
                            df_opts = _options_cache.get(name)

                        matches = detect_patterns(norm_candles)
                        for m in matches:
                            _dispatch_pattern_alert(kite, name, m, df_opts)
                except Exception:
                    pass
                time.sleep(0.3)

        except Exception as e:
            print(f"[PATTERN REST ERROR] {e}")

        time.sleep(45)


# --- Master Scanner Initializer ---

def start_pattern_volume_scanner(kite=None):
    """
    Initializes the 10-Pattern Price Action & Volume Breakout Scoring Scanner.
    Registers hooks with single shared WebSocket feed and starts REST fallback loop.
    """
    global _scanner_started, _token_meta, _symbol_to_token, _options_cache
    with _state_lock:
        if _scanner_started:
            print("[PATTERN SCANNER] Scanner already active. Skipping duplicate start.")
            return
        _scanner_started = True

    print("🚀 [PATTERN SCANNER] Initializing 10-Pattern Price Action & Volume Engine...")
    import env_config
    from kiteconnect import KiteConnect

    token = _get_access_token()
    if not kite and token:
        try:
            kite = KiteConnect(api_key=env_config.API_KEY)
            kite.set_access_token(token)
        except Exception as e:
            print(f"[PATTERN SCANNER] Failed to initialize Kite: {e}")

    df = _load_instruments_df()
    if df.empty:
        print("[PATTERN SCANNER] instruments.csv empty or missing.")
        return

    today_date = datetime.now(IST).date()
    target_tokens = []

    # 1. Register Index Futures
    for name in PATTERN_INDICES:
        futs = df[(df["name"] == name) & (df["instrument_type"] == "FUT")]
        if not futs.empty:
            futs = futs.sort_values(by="expiry")
            fut = futs.iloc[0]
            fut_tkn = int(fut["instrument_token"])
            target_tokens.append(fut_tkn)
            _token_meta[fut_tkn] = {"name": name, "symbol": fut["tradingsymbol"], "is_future": True}
            _symbol_to_token[name] = fut_tkn

            opts = df[(df["name"] == name) & (df["instrument_type"].isin(["CE", "PE"]))]
            if not opts.empty:
                closest_exp = opts["expiry"].min()
                _options_cache[name] = opts[opts["expiry"] == closest_exp].copy()

    # 2. Register Commodity Futures (MCX)
    for name in PATTERN_COMMODITIES:
        futs = df[(df["name"] == name) & (df["instrument_type"] == "FUT")]
        if not futs.empty:
            futs = futs.sort_values(by="expiry")
            fut = futs.iloc[0]
            fut_tkn = int(fut["instrument_token"])
            target_tokens.append(fut_tkn)
            _token_meta[fut_tkn] = {"name": name, "symbol": fut["tradingsymbol"], "is_future": True}
            _symbol_to_token[name] = fut_tkn

            opts = df[(df["name"] == name) & (df["instrument_type"].isin(["CE", "PE"]))]
            if not opts.empty:
                closest_exp = opts["expiry"].min()
                _options_cache[name] = opts[opts["expiry"] == closest_exp].copy()

    # 3. Register Core Focus Stocks (Futures or Spot)
    for name in PATTERN_STOCKS:
        futs = df[(df["name"] == name) & (df["instrument_type"] == "FUT")]
        if not futs.empty:
            futs = futs.sort_values(by="expiry")
            fut = futs.iloc[0]
            fut_tkn = int(fut["instrument_token"])
            target_tokens.append(fut_tkn)
            _token_meta[fut_tkn] = {"name": name, "symbol": fut["tradingsymbol"], "is_future": True}
            _symbol_to_token[name] = fut_tkn
        else:
            spots = df[(df["tradingsymbol"] == name) & (df["segment"] == "NSE")]
            if not spots.empty:
                spot_tkn = int(spots.iloc[0]["instrument_token"])
                target_tokens.append(spot_tkn)
                _token_meta[spot_tkn] = {"name": name, "symbol": name, "is_future": False}
                _symbol_to_token[name] = spot_tkn

        opts = df[(df["name"] == name) & (df["instrument_type"].isin(["CE", "PE"]))]
        if not opts.empty:
            closest_exp = opts["expiry"].min()
            _options_cache[name] = opts[opts["expiry"] == closest_exp].copy()

    # WebSocket tick callback
    def on_ticks(ws, ticks):
        now = datetime.now(IST)
        minute_str = now.strftime("%Y-%m-%d %H:%M")

        with _state_lock:
            for tick in ticks:
                tkn = tick.get("instrument_token")
                if tkn not in _token_meta:
                    continue

                ltp = tick.get("last_price", 0.0)
                vol = tick.get("volume_traded") or tick.get("volume", 0)

                if tkn not in _current_candle_minute:
                    _current_candle_minute[tkn] = minute_str
                    _current_candle_state[tkn] = {
                        "open": ltp, "high": ltp, "low": ltp, "close": ltp,
                        "start_vol": vol, "current_vol": vol
                    }

                # Minute rolled over
                if _current_candle_minute[tkn] != minute_str:
                    c = _current_candle_state[tkn]
                    candle_vol = max(0, c["current_vol"] - c["start_vol"])
                    closed_candle = {
                        "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"],
                        "volume": candle_vol, "time": now
                    }
                    _current_candle_minute[tkn] = minute_str
                    _current_candle_state[tkn] = {
                        "open": ltp, "high": ltp, "low": ltp, "close": ltp,
                        "start_vol": vol, "current_vol": vol
                    }
                    threading.Thread(
                        target=_process_completed_candle,
                        args=(kite, tkn, closed_candle),
                        daemon=True
                    ).start()
                else:
                    c = _current_candle_state[tkn]
                    c["close"] = ltp
                    c["high"] = max(c["high"], ltp)
                    c["low"] = min(c["low"], ltp)
                    c["current_vol"] = vol

    def on_connect(ws, response):
        add_shared_tokens(target_tokens)

    register_ws_callbacks(on_connect, on_ticks)
    add_shared_tokens(target_tokens)

    # Start the REST historical fallback loop
    if kite:
        threading.Thread(target=_rest_historical_polling_loop, args=(kite,), daemon=True).start()

    print(f"✅ [PATTERN SCANNER] Tracking {len(target_tokens)} assets across 10 Price Action & Volume patterns.")
