import os
import time
import datetime
import threading
import pandas as pd
from zoneinfo import ZoneInfo
from kiteconnect import KiteConnect

import env_config
from telegram_utils import send_telegram_message

IST = ZoneInfo("Asia/Kolkata")

# 110 Watchlist (4 Major Indices + 106 Top F&O Stocks)
WATCHLIST = [
    "NIFTY", "SENSEX", "BANKNIFTY", "MIDCPNIFTY",
    "360ONE", "ADANIENSOL", "ADANIENT", "ADANIGREEN", "ADANIPORTS",
    "APLAPOLLO", "ASIANPAINT", "ASTRAL", "AUROPHARMA", "AXISBANK",
    "ABB", "BAJAJ-AUTO", "BAJAJFINSV", "BAJFINANCE", "BDL",
    "BHARATFORG", "BHARTIARTL", "BLUESTARCO", "BSE", "BRITANNIA", "CDSL",
    "CGPOWER", "CHOLAFIN", "CIPLA", "COCHINSHIP", "COFORGE",
    "COLPAL", "CUMMINSIND", "DALBHARAT", "DMART", "DIVISLAB",
    "DRREDDY", "EICHERMOT", "GLENMARK", "GODFRYPHLP", "GODREJCP",
    "GODREJPROP", "GRASIM", "GVT&D", "HAL", "HAVELLS",
    "HCLTECH", "HDFCAMC", "HDFCBANK", "HEROMOTOCO", "HINDALCO",
    "HINDUNILVR", "HYUNDAI", "ICICIBANK", "ICICIGI", "INDUSINDBK",
    "JINDALSTEL", "JSWSTEEL", "KAYNES", "KPITTECH", "LAURUSLABS",
    "LODHA", "LT", "LTM", "LUPIN", "M&M",
    "MANKIND", "MARUTI", "MAXHEALTH", "MAZDOCK", "MCX",
    "MFSL", "MOTILALOFS", "MPHASIS", "MUTHOOTFIN", "NAM-INDIA",
    "NAUKRI", "NESTLEIND", "OBEROIRLTY", "OFSS", "PAYTM",
    "PERSISTENT", "PHOENIXLTD", "PIIND", "PNBHOUSING", "POLICYBZR",
    "PRESTIGE", "RADICO", "RELIANCE", "SBICARD", "SBILIFE",
    "SBIN", "SHRIRAMFIN", "SHREECEM", "SIEMENS", "SRF",
    "SUNPHARMA", "SUPREMEIND", "TATACONSUM", "TATAELXSI", "TCS",
    "TECHM", "TIINDIA", "TITAN", "TORNTPHARM", "TRENT",
    "TVSMOTOR", "UNITDSPR", "ULTRACEMCO", "UNOMINDA", "VOLTAS"
]

COMPRESSION_THRESHOLD_PCT = 10.0

# candidate_key -> candidate info dict
tracked_candidates = {}
state_lock = threading.Lock()
candidates_identified_date = None


def get_target_monthly_expiry(expiries, target_date):
    """
    Selects the nearest MONTHLY contract expiry (ignoring weekly contracts).
    For any given month, the monthly expiry is the latest expiry date in that month.
    """
    valid = [e for e in expiries if e >= target_date]
    if not valid:
        return None
    month_groups = {}
    for e in valid:
        key = (e.year, e.month)
        if key not in month_groups or e > month_groups[key]:
            month_groups[key] = e
    nearest_month_key = sorted(month_groups.keys())[0]
    return month_groups[nearest_month_key]


def load_access_token():
    token = os.getenv("KITE_ACCESS_TOKEN", "").strip()
    if not token and os.path.exists("access_token.txt"):
        with open("access_token.txt", "r") as f:
            token = f.read().strip()
    return token


def load_instruments():
    if not os.path.exists("instruments.csv"):
        print("[NR-1H] instruments.csv not found!")
        return pd.DataFrame()
    df = pd.read_csv("instruments.csv")
    if "expiry" in df.columns:
        df["expiry_dt"] = pd.to_datetime(df["expiry"], errors="coerce")
        today_date = datetime.datetime.now(IST).date()
        df = df[df["expiry_dt"].isna() | (df["expiry_dt"].dt.date >= today_date)].copy()
    return df


def identify_narrow_range_candidates(kite, df):
    """
    Runs at/after 10:15 AM to scan the first 1-Hour candle (09:15 to 10:15 IST)
    for ATM +/- 1 Strikes (CE & PE) of Monthly contracts and filter those with Range % < 10%.
    """
    global tracked_candidates, candidates_identified_date
    now = datetime.datetime.now(IST)
    today_date = now.date()

    print(f"[NR-1H] Scanning 1-Hour candle (09:15 - 10:15) across {len(WATCHLIST)} symbols...")

    from_time = datetime.datetime.combine(today_date, datetime.time(9, 15), tzinfo=IST)
    to_time = datetime.datetime.combine(today_date, datetime.time(10, 15), tzinfo=IST)

    new_candidates = {}

    for name in WATCHLIST:
        try:
            # 1. Get Future token to determine current underlying level
            futs = df[(df["name"] == name) & (df["instrument_type"] == "FUT")]
            if futs.empty:
                continue
            futs = futs.sort_values(by="expiry")
            fut_row = futs.iloc[0]
            fut_token = int(fut_row["instrument_token"])

            fut_candles = kite.historical_data(fut_token, from_time, to_time, "60minute")
            if not fut_candles:
                continue
            underlying_price = float(fut_candles[0].get("close", 0.0))
            if underlying_price <= 0:
                continue

            # 2. Get active options and resolve strict MONTHLY expiry (ignores weeklies for Nifty/Sensex)
            opts = df[(df["name"] == name) & (df["instrument_type"].isin(["CE", "PE"]))]
            if opts.empty:
                continue

            all_expiries = sorted(opts["expiry_dt"].dt.date.unique())
            target_monthly_expiry = get_target_monthly_expiry(all_expiries, today_date)
            if target_monthly_expiry is None:
                continue

            opts_active = opts[opts["expiry_dt"].dt.date == target_monthly_expiry]

            # 3. Find ATM and ATM +/- 1 Strike
            unique_strikes = sorted(opts_active["strike"].unique())
            if not unique_strikes:
                continue

            atm_strike = min(unique_strikes, key=lambda x: abs(x - underlying_price))
            atm_idx = unique_strikes.index(atm_strike)

            # Selected strikes: ATM-1, ATM, ATM+1
            start_idx = max(0, atm_idx - 1)
            end_idx = min(len(unique_strikes), atm_idx + 2)
            selected_strikes = unique_strikes[start_idx:end_idx]

            # 4. Check each Option contract for 1-Hour candle range < 10%
            target_opts = opts_active[opts_active["strike"].isin(selected_strikes)]
            for _, opt_row in target_opts.iterrows():
                opt_token = int(opt_row["instrument_token"])
                opt_symbol = opt_row["tradingsymbol"]
                opt_strike = float(opt_row["strike"])
                opt_type = opt_row["instrument_type"]

                try:
                    opt_candles = kite.historical_data(opt_token, from_time, to_time, "60minute")
                except Exception:
                    continue

                if not opt_candles:
                    continue

                c1h = opt_candles[0]
                h_1h = float(c1h.get("high", 0.0))
                l_1h = float(c1h.get("low", 0.0))

                if l_1h <= 0 or h_1h <= l_1h:
                    continue

                # Range % formula: ((High - Low) / Low) * 100
                range_pct = ((h_1h - l_1h) / l_1h) * 100.0

                if range_pct < COMPRESSION_THRESHOLD_PCT:
                    is_atm_str = " (ATM)" if opt_strike == atm_strike else ""
                    cand_key = f"{opt_token}"
                    new_candidates[cand_key] = {
                        "token": opt_token,
                        "symbol": opt_symbol,
                        "underlying": name,
                        "strike": opt_strike,
                        "type": opt_type,
                        "is_atm": is_atm_str,
                        "high_1h": h_1h,
                        "low_1h": l_1h,
                        "range_pct": range_pct,
                        "alerted": False
                    }
                    print(f"  [NR-1H CANDIDATE] {opt_symbol}: 1H High={h_1h:.2f}, Low={l_1h:.2f}, Range={range_pct:.2f}% (<10%)")
        except Exception as e:
            continue

    with state_lock:
        tracked_candidates = new_candidates
        candidates_identified_date = today_date

    print(f"[NR-1H] Total {len(new_candidates)} compressed option candidates (<10% 1H Range) registered.")


def scan_15m_breakouts(kite):
    """
    Runs after each completed 15-minute candle and fires an alert if candle closed above 1H High.
    """
    global tracked_candidates
    now = datetime.datetime.now(IST)
    today_date = now.date()

    with state_lock:
        active_list = [v for v in tracked_candidates.values() if not v["alerted"]]

    if not active_list:
        return

    from_time = datetime.datetime.combine(today_date, datetime.time(9, 15), tzinfo=IST)
    to_time = now

    for cand in active_list:
        token = cand["token"]
        high_1h = cand["high_1h"]

        try:
            candles_15m = kite.historical_data(token, from_time, to_time, "15minute")
        except Exception:
            continue

        if not candles_15m or len(candles_15m) < 5:
            continue

        # Check the latest completed 15-minute candle
        last_completed = candles_15m[-1]
        candle_close = float(last_completed.get("close", 0.0))
        candle_high = float(last_completed.get("high", 0.0))
        candle_time = last_completed.get("date")

        if candle_close > high_1h:
            with state_lock:
                cand["alerted"] = True

            action_type = "BUY CALL (CE)" if cand["type"] == "CE" else "BUY PUT (PE)"
            direction_label = "CALL (Bullish Expansion)" if cand["type"] == "CE" else "PUT (Bearish Expansion)"
            time_str = candle_time.strftime("%H:%M") if hasattr(candle_time, "strftime") else now.strftime("%H:%M")

            msg = (
                f"🚀 *15-MIN OPTION BREAKOUT (NR-1H)* 🚀\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Asset: *{cand['underlying']}*\n"
                f"Option Contract: *{cand['symbol']}{cand['is_atm']}*\n"
                f"1-Hour Compression (09:15 - 10:15):\n"
                f"  • 1H High: ₹{cand['high_1h']:.2f}\n"
                f"  • 1H Low : ₹{cand['low_1h']:.2f}\n"
                f"  • 1H Range: {cand['range_pct']:.2f}% (< 10% Compressed)\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⚡ *15-MIN CANDLE BREAKOUT CONFIRMED*:\n"
                f"  • 15M Close: *₹{candle_close:.2f}* (Closed ABOVE ₹{high_1h:.2f})\n"
                f"  • Candle High: ₹{candle_high:.2f}\n"
                f"  • Time: {now.strftime('%H:%M:%S')} IST (15M Candle @ {time_str})\n"
                f"💡 *Action*: {action_type} - {direction_label}"
            )

            print(f"[NR-1H BREAKOUT] Triggered for {cand['symbol']} (Close {candle_close:.2f} > 1H High {high_1h:.2f})")
            send_telegram_message(msg, chat_id=env_config.TELE_CHAT_ID, token=env_config.TELE_TOKEN)


def start_nr_option_breakout_scanner():
    print("Initializing 1-Hour Narrow Range (NR-1H) 15-Minute Option Breakout Scanner...")
    token = load_access_token()
    if not token:
        print("[NR-1H] Access token missing. Scanner standby.")
        return

    try:
        kite = KiteConnect(api_key=env_config.API_KEY)
        kite.set_access_token(token)
    except Exception as e:
        print(f"[NR-1H] Kite init failed: {e}")
        return

    df = load_instruments()
    if df.empty:
        return

    last_scanned_slot = None

    def worker_loop():
        nonlocal df, last_scanned_slot
        while True:
            try:
                now = datetime.datetime.now(IST)
                if now.weekday() > 4:
                    time.sleep(60)
                    continue

                t = now.time()
                market_start = datetime.time(9, 15)
                market_end = datetime.time(15, 30)

                if not (market_start <= t <= market_end):
                    time.sleep(30)
                    continue

                global candidates_identified_date
                # 1. At 10:15 AM, identify candidates once per day
                if t >= datetime.time(10, 15) and candidates_identified_date != now.date():
                    if df.empty:
                        df = load_instruments()
                    identify_narrow_range_candidates(kite, df)

                # 2. Check 15-minute candle breakouts starting at 10:30, 10:45, 11:00...
                if t >= datetime.time(10, 30):
                    current_slot = (now.hour, (now.minute // 15) * 15)
                    if current_slot != last_scanned_slot and (now.minute % 15 == 0 and now.second >= 10 or now.minute % 15 > 0):
                        last_scanned_slot = current_slot
                        scan_15m_breakouts(kite)

            except Exception as e:
                print(f"[NR-1H] Scanner loop error: {e}")

            time.sleep(10)

    threading.Thread(target=worker_loop, daemon=True).start()


if __name__ == "__main__":
    start_nr_option_breakout_scanner()
    while True:
        time.sleep(1)

