import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from websocket_flow import get_symbol_quotes, get_token_quotes

# ================= CONFIG =================

BANK_WEIGHTS = {
    "HDFCBANK": 19.7,
    "ICICIBANK": 16.1,
    "SBIN": 10.7,
    "AXISBANK": 9.9,
    "KOTAKBANK": 9.2,
    "FEDERALBNK": 5.6,
    "INDUSINDBK": 4.7,
    "BANKBARODA": 4.5,
    "AUBANK": 4.0,
    "CANBK": 3.9,
    "PNB": 3.5,
    "IDFCFIRSTB": 3.2,
    "YESBANK": 2.5,
    "UNIONBANK": 2.5
}

LOT_SIZES = {
    "HDFCBANK": 550,
    "ICICIBANK": 700,
    "SBIN": 750,
    "AXISBANK": 625,
    "KOTAKBANK": 2000,
    "FEDERALBNK": 5000,
    "INDUSINDBK": 500,
    "BANKBARODA": 4850,
    "AUBANK": 1000,
    "CANBK": 2250,
    "PNB": 4000,
    "IDFCFIRSTB": 7500,
    "YESBANK": 8000,
    "UNIONBANK": 5000,
    "BANKNIFTY": 30
}

BANK_NAMES = list(BANK_WEIGHTS.keys())
INDEX_SYMBOL = "NSE:NIFTY BANK"

# State Tracking
last_oi_store = {}
last_strike_store = {} # {name: {'mc': x, 'mp': y, 'cc': x, 'cp': y}}
day_open_oi_store = {} # {token: open_oi}
option_history = {} 
active_watches = {} 
accum_history = {}
price_velocity_store = {}
high_conviction_store = {}

# Shift Strings for Categorized Alerts
max_shift_text = {} # {name: {'pe': text, 'ce': text}}
chg_shift_text = {} # {name: {'pe': text, 'ce': text}}

_options_df = None
_futures_df = None
_index_df = None
_equity_df = None
_history_cache = {}
IST = ZoneInfo("Asia/Kolkata")


# ================= HELPERS =================

def add_global_alert(msg, name=None):
    # Burst alerts logic if still needed, currently suppressed in report
    pass

def load_options_data():
    global _options_df
    if _options_df is None:
        try:
            df = pd.read_csv("instruments.csv")
            _options_df = df[df['segment'].isin(['NFO-OPT'])].copy()
            _options_df['expiry'] = pd.to_datetime(_options_df['expiry'], dayfirst=True)
        except Exception as e: print(f"Error loading Options: {e}")
    return _options_df

def load_futures_data():
    global _futures_df
    if _futures_df is None:
        try:
            df = pd.read_csv("instruments.csv")
            _futures_df = df[df['segment'].str.contains('-FUT', na=False)].copy()
            _futures_df['expiry'] = pd.to_datetime(_futures_df['expiry'], dayfirst=True)
        except Exception as e: print(f"Error loading Futures: {e}")
    return _futures_df

def load_index_data():
    global _index_df
    if _index_df is None:
        try:
            df = pd.read_csv("instruments.csv")
            _index_df = df[(df["segment"] == "INDICES") & (df["exchange"] == "NSE")].copy()
        except Exception as e: print(f"Error loading Indices: {e}")
    return _index_df

def load_equity_data():
    global _equity_df
    if _equity_df is None:
        try:
            df = pd.read_csv("instruments.csv")
            _equity_df = df[(df["segment"] == "NSE") & (df["exchange"] == "NSE")].copy()
        except Exception as e: print(f"Error loading Equities: {e}")
    return _equity_df

def get_spot_symbol(name):
    if name == "BANKNIFTY":
        return INDEX_SYMBOL
    return f"NSE:{name}"

def get_active_future(name):
    df = load_futures_data()
    if df is None or df.empty: return None
    futures = df[df['name'] == name]
    if futures.empty: return None
    nearest_expiry = futures['expiry'].min()
    return "NFO:" + futures[futures['expiry'] == nearest_expiry].iloc[0]['tradingsymbol']

def get_symbol_token(symbol):
    if symbol == INDEX_SYMBOL:
        df = load_index_data()
        if df is None or df.empty:
            return None
        rows = df[df["tradingsymbol"] == "NIFTY BANK"]
        if rows.empty:
            return None
        return int(rows.iloc[0]["instrument_token"])

    if ":" not in symbol:
        return None

    tradingsymbol = symbol.split(":", 1)[1]
    if symbol.startswith("NSE:"):
        df = load_equity_data()
        if df is None or df.empty:
            return None
        rows = df[df["tradingsymbol"] == tradingsymbol]
        if rows.empty:
            return None
        return int(rows.iloc[0]["instrument_token"])

    df = load_futures_data()
    if df is None or df.empty:
        return None
    rows = df[df["tradingsymbol"] == tradingsymbol]
    if rows.empty:
        return None
    return int(rows.iloc[0]["instrument_token"])

def get_symbol_quotes_with_fallback(kite, symbols, max_age_seconds=15):
    data = get_symbol_quotes(symbols, max_age_seconds=max_age_seconds)
    missing = [symbol for symbol in symbols if symbol not in data]
    if missing:
        try:
            data.update(kite.quote(missing))
        except Exception as e:
            print(f"Fallback symbol quote error: {e}")
    return data

def get_option_quotes_with_fallback(kite, tokens, max_age_seconds=15):
    token_strings = [str(int(token)) for token in tokens]
    data = get_token_quotes(token_strings, max_age_seconds=max_age_seconds)
    missing = [int(token) for token in token_strings if token not in data]
    for i in range(0, len(missing), 400):
        chunk = missing[i:i + 400]
        if not chunk:
            continue
        try:
            fresh = kite.quote(chunk)
            data.update({str(key): value for key, value in fresh.items()})
        except Exception as e:
            print(f"Fallback option quote error: {e}")
    return data

def get_bank_futures(kite):
    symbols = []
    # Include Bank Nifty and selected bank stocks.
    for name in ["HDFCBANK", "ICICIBANK", "BANKNIFTY"]:
        sym = get_active_future(name)
        if sym: symbols.append(sym)
    return symbols

def get_relevant_options(name, ltp):
    df = load_options_data()
    if df is None or df.empty: return pd.DataFrame()
    
    stock_ref = df[df['name'] == 'HDFCBANK']
    if stock_ref.empty:
        options = df[df['name'] == name]
        if options.empty: return pd.DataFrame()
        expiry = sorted(options['expiry'].unique())[0]
    else:
        expiry = sorted(stock_ref['expiry'].unique())[0]
    
    options = df[df['name'] == name]
    options = options[options['expiry'] == expiry]
    if options.empty: return pd.DataFrame()
    
    strikes = sorted(options['strike'].unique())
    atm = min(strikes, key=lambda x: abs(x - ltp))
    idx = strikes.index(atm)
    # Range: 25 for BANKNIFTY, 10 for selected bank stocks.
    rng = 25 if name == "BANKNIFTY" else 10
    selected = strikes[max(0, idx - rng): idx + rng + 1]
    return options[options['strike'].isin(selected)]

def get_strength_label(lots, name="BANKNIFTY"):
    if name == "BANKNIFTY":
        if lots >= 400: return "🚀 BLAST 🚀"
        elif lots >= 300: return "🌟 AWESOME"
        elif lots >= 200: return "✅ VERY GOOD"
        else: return "⚡ GOOD"
    else:
        if lots >= 150: return "🚀 BLAST 🚀"
        elif lots >= 100: return "🌟 AWESOME"
        elif lots >= 75: return "✅ VERY GOOD"
        else: return "⚡ GOOD"

def format_oi_delta(oi_delta):
    value = abs(oi_delta or 0)
    if value >= 10000000:
        return f"{value/10000000:.1f}Cr"
    if value >= 100000:
        return f"{value/100000:.1f}L"
    if value >= 1000:
        return f"{value/1000:.1f}K"
    return f"{value:.0f}"

def format_shift_strike(strike, changed, suffix, oi_delta=None):
    if strike and strike > 0:
        arrow = "▲" if (oi_delta is None or oi_delta >= 0) else "▼"
        marker = "★" if changed else ""
        return f"{int(strike)}{suffix}({format_oi_delta(oi_delta)}{arrow}){marker}"
    return f"No Data{suffix}"

def format_oi_pair(label, put_text, call_text):
    return f"{label}:{put_text} | {call_text}"

def resolve_display_shift(prev, current):
    checks = [
        ("MAX_P", "mp"),
        ("MAX_C", "mc"),
        ("CHG_P", "cp"),
        ("CHG_C", "cc"),
    ]
    changed_parts = []

    for label, key in checks:
        old_value = prev.get(key, 0)
        new_value = current.get(key, 0)
        if old_value > 0 and new_value > 0 and old_value != new_value:
            changed_parts.append(label)

    return ",".join(changed_parts) if changed_parts else "NO CHANGE"

def determine_shift_label(prev, current, price_change_pct):
    max_pe_up = current["mp"] > 0 and prev["mp"] > 0 and current["mp"] > prev["mp"]
    max_ce_up = current["mc"] > 0 and prev["mc"] > 0 and current["mc"] > prev["mc"]
    chg_pe_up = current["cp"] > 0 and prev["cp"] > 0 and current["cp"] > prev["cp"]
    chg_ce_up = current["cc"] > 0 and prev["cc"] > 0 and current["cc"] > prev["cc"]

    max_pe_down = current["mp"] > 0 and prev["mp"] > 0 and current["mp"] < prev["mp"]
    max_ce_down = current["mc"] > 0 and prev["mc"] > 0 and current["mc"] < prev["mc"]
    chg_pe_down = current["cp"] > 0 and prev["cp"] > 0 and current["cp"] < prev["cp"]
    chg_ce_down = current["cc"] > 0 and prev["cc"] > 0 and current["cc"] < prev["cc"]

    max_shifted = current["mp"] != prev["mp"] or current["mc"] != prev["mc"]
    chg_shifted = current["cp"] != prev["cp"] or current["cc"] != prev["cc"]

    if (max_pe_up and max_ce_up) and (chg_pe_up and chg_ce_up):
        return "STRONG BULLISH SHIFT"
    if (max_pe_down and max_ce_down) and (chg_pe_down and chg_ce_down):
        return "STRONG BEARISH SHIFT"
    if not max_shifted and chg_shifted:
        return "EARLY SHIFT"
    if chg_shifted and abs(price_change_pct) <= 0.15:
        return "ABSORPTION WATCH"
    return "NO MAJOR SHIFT"

def determine_direction_and_hedge(shift_label, price_change_pct, pcr, max_p_delta, max_c_delta, chg_p_delta, chg_c_delta):
    pe_build = max_p_delta > 0 or chg_p_delta > 0
    ce_build = max_c_delta > 0 or chg_c_delta > 0

    if shift_label == "STRONG BULLISH SHIFT" and price_change_pct >= 0 and pcr >= 1:
        direction = "BULLISH"
    elif shift_label == "STRONG BEARISH SHIFT" and price_change_pct <= 0 and pcr <= 1:
        direction = "BEARISH"
    else:
        direction = "SIDEWAYS"

    if price_change_pct > 0 and ce_build:
        hedge = "⛔ UPSIDE CAPPED"
    elif price_change_pct < 0 and pe_build:
        hedge = "🛡 DOWNSIDE PROTECTED"
    elif pe_build and ce_build:
        hedge = "⚖ BOTH SIDE HEDGE"
    else:
        hedge = "NEUTRAL"

    return direction, hedge

def format_level(value):
    if value is None or pd.isna(value):
        return "NA"
    return f"{round(float(value))}"

def calculate_standard_pivots(high_value, low_value, close_value):
    pivot = (high_value + low_value + close_value) / 3
    r1 = (2 * pivot) - low_value
    s1 = (2 * pivot) - high_value
    r2 = pivot + (high_value - low_value)
    s2 = pivot - (high_value - low_value)
    return {
        "P": pivot,
        "R1": r1,
        "S1": s1,
        "S2": s2,
        "R2": r2,
    }

def fetch_historical_frame(kite, instrument_token, interval, from_dt, to_dt, cache_key, continuous=False):
    now_ts = datetime.now(IST).timestamp()
    cached = _history_cache.get(cache_key)
    if cached and now_ts - cached["ts"] <= 60:
        return cached["data"]

    try:
        records = kite.historical_data(instrument_token, from_dt, to_dt, interval, continuous=continuous, oi=False)
        frame = pd.DataFrame(records)
        if not frame.empty and "date" in frame.columns:
            frame["date"] = pd.to_datetime(frame["date"])
        _history_cache[cache_key] = {"ts": now_ts, "data": frame}
        return frame
    except Exception as e:
        print(f"Historical data fetch failed for token {instrument_token} interval {interval}: {e}")
        return pd.DataFrame()

def get_symbol_analytics(kite, symbol):
    instrument_token = get_symbol_token(symbol)
    if instrument_token is None:
        return None

    now_ist = datetime.now(IST)
    session_start = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    if now_ist < session_start:
        session_start = session_start - timedelta(days=1)

    five_minute = fetch_historical_frame(
        kite,
        instrument_token,
        "5minute",
        session_start.replace(tzinfo=None),
        now_ist.replace(tzinfo=None),
        ("5minute", instrument_token, session_start.date().isoformat()),
    )

    vwap_value = None
    if not five_minute.empty and {"high", "low", "close", "volume"}.issubset(five_minute.columns):
        valid = five_minute[five_minute["volume"] > 0].copy()
        if not valid.empty:
            typical_price = (valid["high"] + valid["low"] + valid["close"]) / 3
            vwap_value = (typical_price * valid["volume"]).sum() / valid["volume"].sum()

    daily_frame = fetch_historical_frame(
        kite,
        instrument_token,
        "day",
        (now_ist - timedelta(days=420)).replace(tzinfo=None),
        now_ist.replace(tzinfo=None),
        ("day", instrument_token),
    )
    hourly_frame = fetch_historical_frame(
        kite,
        instrument_token,
        "60minute",
        (now_ist - timedelta(days=30)).replace(tzinfo=None),
        now_ist.replace(tzinfo=None),
        ("60minute", instrument_token),
    )
    fifteen_frame = fetch_historical_frame(
        kite,
        instrument_token,
        "15minute",
        (now_ist - timedelta(days=10)).replace(tzinfo=None),
        now_ist.replace(tzinfo=None),
        ("15minute", instrument_token),
    )

    analytics = {
        "vwap": vwap_value,
        "sma200": {"D": None, "1H": None, "15M": None},
        "pivot": {"D": None, "1H": None, "15M": None},
    }

    if not daily_frame.empty and len(daily_frame) >= 200:
        analytics["sma200"]["D"] = daily_frame["close"].tail(200).mean()
    if not hourly_frame.empty and len(hourly_frame) >= 200:
        analytics["sma200"]["1H"] = hourly_frame["close"].tail(200).mean()
    if not fifteen_frame.empty and len(fifteen_frame) >= 200:
        analytics["sma200"]["15M"] = fifteen_frame["close"].tail(200).mean()

    if not daily_frame.empty and len(daily_frame) >= 2:
        prev_day = daily_frame.iloc[-2]
        analytics["pivot"]["D"] = calculate_standard_pivots(prev_day["high"], prev_day["low"], prev_day["close"])
    if not hourly_frame.empty and len(hourly_frame) >= 2:
        prev_hour = hourly_frame.iloc[-2]
        analytics["pivot"]["1H"] = calculate_standard_pivots(prev_hour["high"], prev_hour["low"], prev_hour["close"])
    if not fifteen_frame.empty and len(fifteen_frame) >= 2:
        prev_15m = fifteen_frame.iloc[-2]
        analytics["pivot"]["15M"] = calculate_standard_pivots(prev_15m["high"], prev_15m["low"], prev_15m["close"])

    return analytics

def format_pivot_line(label, pivot_values):
    if not pivot_values:
        return f"PIVOT {label}:P:NA,R1:NA,S1:NA,S2:NA,R2:NA"
    return (
        f"PIVOT {label}:"
        f"P:{format_level(pivot_values['P'])},"
        f"R1:{format_level(pivot_values['R1'])},"
        f"S1:{format_level(pivot_values['S1'])},"
        f"S2:{format_level(pivot_values['S2'])},"
        f"R2:{format_level(pivot_values['R2'])}"
    )

def classify_level_position(price, level, near_threshold):
    if level is None or pd.isna(level):
        return None
    if abs(float(price) - float(level)) <= near_threshold:
        return "NEAR"
    return "ABOVE" if float(price) > float(level) else "BELOW"

def format_pivot_tag(timeframe, level_name, level_value):
    return f"PIVOT-{timeframe}-{level_name}({format_level(level_value)})"

def build_levels_line(name, price, analytics):
    if not analytics:
        return "LEVELS:NA"

    near_threshold = 25 if name == "BANKNIFTY" else 2
    grouped = {"NEAR": [], "ABOVE": [], "BELOW": []}

    pivot_checks_by_timeframe = {
        "D": ["P", "R1", "R2", "S1", "S2"],
        "1H": ["P", "R1", "R2", "S1", "S2"],
        "15M": ["P", "R1", "R2", "S1", "S2"],
    }
    for timeframe, level_names in pivot_checks_by_timeframe.items():
        pivot_bucket = analytics["pivot"].get(timeframe)
        if not pivot_bucket:
            continue

        nearest_by_state = {}
        for level_name in level_names:
            level_value = pivot_bucket.get(level_name)
            pivot_state = classify_level_position(price, level_value, near_threshold)
            if pivot_state is None:
                continue

            distance = abs(float(price) - float(level_value))
            previous = nearest_by_state.get(pivot_state)
            if previous is None or distance < previous[1]:
                nearest_by_state[pivot_state] = (level_name, distance)

        if "NEAR" in nearest_by_state:
            level_name = nearest_by_state["NEAR"][0]
            grouped["NEAR"].append(format_pivot_tag(timeframe, level_name, pivot_bucket.get(level_name)))
            continue

        for state in ["ABOVE", "BELOW"]:
            if state in nearest_by_state:
                level_name = nearest_by_state[state][0]
                grouped[state].append(format_pivot_tag(timeframe, level_name, pivot_bucket.get(level_name)))

    lines = []
    for label in ["NEAR", "ABOVE", "BELOW"]:
        if grouped[label]:
            lines.append(f"{label}:{','.join(grouped[label])}")

    if not lines:
        return "LEVELS:NA"
    return "\n".join(lines)

def build_read_line(chg_p_oi, chg_c_oi, levels_line):
    if chg_c_oi > chg_p_oi:
        flow = "CALL_BLD▲"
    elif chg_p_oi > chg_c_oi:
        flow = "PUT_BLD▲"
    else:
        flow = "BAL_OI"

    parts = [flow]
    label_map = {"ABOVE": "ABO", "BELOW": "BEL", "NEAR": "NR"}
    for state in ["ABOVE", "BELOW", "NEAR"]:
        prefix = f"{state}:"
        line = next((item for item in levels_line.splitlines() if item.startswith(prefix)), "")
        if not line:
            continue
        first_tag = line[len(prefix):].split(",", 1)[0]
        first_tag = first_tag.split("(", 1)[0]
        if first_tag.startswith("PIVOT-"):
            first_tag = first_tag.replace("PIVOT-", "P_", 1)
        parts.append(f"{label_map[state]}_{first_tag}")

    return "READ:" + "/".join(parts)

def get_level_state(analytics, timeframe, level_name, price, near_threshold):
    if not analytics:
        return None
    pivot_bucket = analytics.get("pivot", {}).get(timeframe)
    if not pivot_bucket:
        return None
    level_value = pivot_bucket.get(level_name)
    return classify_level_position(price, level_value, near_threshold)

def format_sma200_line(analytics):
    if not analytics:
        return "SMA200:D:NA,1H:NA,15M:NA"

    sma = analytics.get("sma200", {})
    return (
        f"SMA200:D:{format_level(sma.get('D'))},"
        f"1H:{format_level(sma.get('1H'))},"
        f"15M:{format_level(sma.get('15M'))}"
    )

def format_vwap_line(name, price, analytics):
    if not analytics:
        return "VWAP:NA"

    vwap_value = analytics.get("vwap")
    if vwap_value is None or pd.isna(vwap_value):
        return "VWAP:NA"

    near_threshold = 25 if name == "BANKNIFTY" else 2
    state = classify_level_position(price, vwap_value, near_threshold)
    if state == "NEAR":
        return f"VWAP:{format_level(vwap_value)} Price NEAR VWAP"
    return f"VWAP:{format_level(vwap_value)} Price {state} VWAP"

def classify_action(symbol, oi_change, price_change):
    if any(x in symbol for x in ["-FUT", "FUT", "-I"]):
        if oi_change > 0: return "FUTURE BUY (LONG) 📈" if price_change >= 0 else "FUTURE SELL (SHORT) 📉"
        else: return "SHORT COVERING ↗️" if price_change >= 0 else "LONG UNWINDING ↘️"
    is_call = symbol.endswith("CE")
    if oi_change > 0:
        if price_change >= 0: return "CALL BUY 🔵" if is_call else "PUT BUY 🔴"
        else: return "CALL WRITER ✍️" if is_call else "PUT WRITER ✍️"
    else:
        if price_change >= 0: return "SHORT COVERING (CE) ⤴️" if is_call else "SHORT COVERING (PE) ⤴️"
        else: return "LONG UNWINDING (CE) ⤵️" if is_call else "LONG UNWINDING (PE) ⤵️"

def build_high_conviction_alert(name, bias, fut_price, strike, option_type, price_change_pct, max_delta, chg_delta, analytics):
    vwap_value = analytics.get("vwap") if analytics else None
    pivot_value = analytics.get("pivot", {}).get("D", {}).get("P") if analytics else None
    emoji = "🟢" if bias == "BULLISH" else "🔴"
    writer_side = "PUT WRITER" if bias == "BULLISH" else "CALL WRITER"
    return "\n".join([
        "🔥 HIGH-CONVICTION ALERT 🔥",
        f"{emoji} {name} {bias} CONFIRMATION",
        "",
        f"ACTION: BUY {name} {int(strike)} {option_type}",
        f"FUTURE PRICE: {fut_price:.2f}",
        f"PRICE CHANGE: {price_change_pct:+.2f}%",
        "",
        "CONFIRMATIONS:",
        f"1. {writer_side} buildup is dominant",
        f"2. MAX OI build: {format_oi_delta(max_delta)}",
        f"3. CHG OI build: {format_oi_delta(chg_delta)}",
        f"4. VWAP: {format_level(vwap_value)}",
        f"5. Daily Pivot P: {format_level(pivot_value)}",
        "",
        "RISK:",
        "SL: 30 pts",
        "TARGET: 60 pts",
        "",
        f"TIME: {datetime.now().strftime('%H:%M:%S')}",
    ])

def evaluate_high_conviction(
    name,
    fut_price,
    price_change_pct,
    pcr,
    max_c,
    max_p,
    chg_c,
    chg_p,
    shift_label,
    max_p_delta,
    max_c_delta,
    chg_p_delta,
    chg_c_delta,
    analytics,
):
    if not analytics:
        return None

    near_threshold = 25 if name == "BANKNIFTY" else 2
    vwap_state = classify_level_position(fut_price, analytics.get("vwap"), near_threshold)
    pivot_state = get_level_state(analytics, "D", "P", fut_price, near_threshold)

    bullish_threshold = 0.20 if name == "BANKNIFTY" else 0.12
    bearish_threshold = -0.20 if name == "BANKNIFTY" else -0.12

    bullish = (
        shift_label == "STRONG BULLISH SHIFT"
        and price_change_pct >= bullish_threshold
        and pcr >= 1.0
        and max_p_delta > max_c_delta
        and chg_p_delta > chg_c_delta
        and vwap_state == "ABOVE"
        and pivot_state in {"ABOVE", "NEAR"}
        and (chg_p or max_p) > 0
    )
    bearish = (
        shift_label == "STRONG BEARISH SHIFT"
        and price_change_pct <= bearish_threshold
        and pcr <= 1.0
        and max_c_delta > max_p_delta
        and chg_c_delta > chg_p_delta
        and vwap_state == "BELOW"
        and pivot_state in {"BELOW", "NEAR"}
        and (chg_c or max_c) > 0
    )

    if not bullish and not bearish:
        return None

    bias = "BULLISH" if bullish else "BEARISH"
    strike = (chg_p or max_p) if bullish else (chg_c or max_c)
    option_type = "CE" if bullish else "PE"
    cooldown_key = f"{name}:{bias}"
    now = datetime.now()
    last_sent = high_conviction_store.get(cooldown_key)
    if last_sent and (now - last_sent).total_seconds() < 300:
        return None

    high_conviction_store[cooldown_key] = now
    return build_high_conviction_alert(
        name,
        bias,
        fut_price,
        strike,
        option_type,
        price_change_pct,
        max_p_delta if bullish else max_c_delta,
        chg_p_delta if bullish else chg_c_delta,
        analytics,
    )


# ================= DETECTION LOGIC =================

def process_future_burst(symbol, name, ltp, oi, alerts_list):
    if name not in ["HDFCBANK", "ICICIBANK", "BANKNIFTY"]:
        return

    threshold = 100 if name == "BANKNIFTY" else 50
    lot_size = LOT_SIZES.get(name, 1)
    now = datetime.now()
    key = f"FUT_{symbol}"
    if key not in option_history: option_history[key] = []
    history = option_history[key]
    prev_oi = history[-1]['oi'] if history else 0
    prev_price = history[-1]['price'] if history else 0
    if prev_oi > 0:
        tick_lots = int(abs(oi - prev_oi) / lot_size)
        if tick_lots >= threshold and key not in active_watches:
            active_watches[key] = {"start_oi": prev_oi, "start_price": prev_price, "end_time": now + timedelta(seconds=15), "symbol": symbol, "name": name}
    if key in active_watches:
        watch = active_watches[key]
        if now >= watch["end_time"]:
            oi_chg = oi - watch["start_oi"]
            p_chg = ltp - watch["start_price"]
            final_lots = int(abs(oi_chg) / lot_size)
            if final_lots >= threshold:
                strength = get_strength_label(final_lots, watch['name'])
                action = classify_action(watch['symbol'], oi_chg, p_chg)
                p_icon = "▲" if p_chg >= 0 else "▼"
                alerts_list.append(f"{strength}\n🚨 {action}\nSymbol: {watch['symbol']}\n━━━━━━━━━━━━━━━\nLOTS: {final_lots}\nPRICE: {ltp:.2f} ({p_icon})\nFUTURE PRICE: {ltp:.2f}\n━━━━━━━━━━━━━━━\nEXISTING OI: {watch['start_oi']:,}\nOI CHANGE  : {oi_chg:+,d}\nNEW OI     : {oi:,}\nTIME: {now.strftime('%H:%M:%S')}")
            del active_watches[key]
    history.append({'time': now, 'oi': oi, 'price': ltp})
    if len(history) > 20: history.pop(0)

def process_option_logic(name, underlying_data, option_quotes, alerts_list, price_change_pct=0):
    if name not in ["HDFCBANK", "ICICIBANK", "BANKNIFTY"]:
        return 1.0, 0, 0, 0, 0, False, False, False, False, "MAXOI_P AND CHGOI_P", "NO MAJOR SHIFT", 0, 0, 0, 0

    threshold = 100 if name == "BANKNIFTY" else 50
    opt_df, u_ltp = underlying_data
    if opt_df.empty: return 1.0, 0, 0, 0, 0, False, False, False, False, "MAXOI_P AND CHGOI_P", "NO MAJOR SHIFT", 0, 0, 0, 0
    total_call = total_put = 0
    max_c_oi = max_p_oi = chg_c_oi = chg_p_oi = 0
    max_c = max_p = chg_c = chg_p = 0
    max_c_delta = max_p_delta = chg_c_delta = chg_p_delta = 0
    lot_size = LOT_SIZES.get(name, 1)
    now = datetime.now()
    for _, row in opt_df.iterrows():
        t_str = str(int(row['instrument_token']))
        if t_str not in option_quotes: continue
        q = option_quotes[t_str]
        curr_oi, ltp = q.get('oi', 0), q.get('last_price', 0)
        t_int = int(row['instrument_token'])
        
        # Track Day Baseline for Cumulative Buildup
        if t_int not in day_open_oi_store:
            day_open_oi_store[t_int] = curr_oi
        
        cumulative_oi_chg = curr_oi - day_open_oi_store[t_int]
        
        if t_int not in option_history: option_history[t_int] = []
        history = option_history[t_int]
        prev_oi = history[-1]['oi'] if history else 0
        prev_price = history[-1]['price'] if history else 0
        oi_chg_tick = curr_oi - prev_oi
        
        if row['instrument_type'] == 'CE':
            total_call += curr_oi
            if curr_oi > max_c_oi:
                max_c_oi, max_c, max_c_delta = curr_oi, row['strike'], cumulative_oi_chg
            if cumulative_oi_chg > chg_c_oi:
                chg_c_oi, chg_c, chg_c_delta = cumulative_oi_chg, row['strike'], cumulative_oi_chg
        else:
            total_put += curr_oi
            if curr_oi > max_p_oi:
                max_p_oi, max_p, max_p_delta = curr_oi, row['strike'], cumulative_oi_chg
            if cumulative_oi_chg > chg_p_oi:
                chg_p_oi, chg_p, chg_p_delta = cumulative_oi_chg, row['strike'], cumulative_oi_chg
        
        if prev_oi > 0:
            tick_lots = int(abs(curr_oi - prev_oi) / lot_size)
            if tick_lots >= threshold and t_int not in active_watches:
                active_watches[t_int] = {"start_oi": prev_oi, "start_price": prev_price, "end_time": now + timedelta(seconds=15), "symbol": row['tradingsymbol'], "underlying": name}
        
        if t_int in active_watches:
            watch = active_watches[t_int]
            if now >= watch["end_time"]:
                oi_chg = curr_oi - watch["start_oi"]
                p_chg = ltp - watch["start_price"]
                final_lots = int(abs(oi_chg) / lot_size)
                if final_lots >= threshold:
                    strength = get_strength_label(final_lots, watch['underlying'])
                    action = classify_action(watch['symbol'], oi_chg, p_chg)
                    p_icon = "▲" if p_chg >= 0 else "▼"
                    alerts_list.append(f"{strength}\n🚨 {action}\nSymbol: {watch['symbol']}\n━━━━━━━━━━━━━━━\nLOTS: {final_lots}\nPRICE: {ltp:.2f} ({p_icon})\nFUTURE PRICE: {u_ltp:.2f}\n━━━━━━━━━━━━━━━\nEXISTING OI: {watch['start_oi']:,}\nOI CHANGE  : {oi_chg:+,d}\nNEW OI     : {curr_oi:,}\nTIME: {now.strftime('%H:%M:%S')}")
                del active_watches[t_int]
        
        history.append({'time': now, 'oi': curr_oi, 'price': ltp})
        if len(history) > 20: history.pop(0)

    # Track Shift Logic [NEW REQ per report1.pdf]
    max_p_changed = max_c_changed = chg_p_changed = chg_c_changed = False

    if name not in last_strike_store: 
        last_strike_store[name] = {'mc': max_c, 'mp': max_p, 'cc': chg_c, 'cp': chg_p}
        max_shift_text[name] = {'pe': f"{int(max_p)}", 'ce': f"{int(max_c)}"}
        chg_shift_text[name] = {'pe': f"{int(chg_p)}", 'ce': f"{int(chg_c)}"}
        
    prev = last_strike_store[name]

    max_p_changed = max_p > 0 and prev['mp'] != 0 and max_p != prev['mp']
    max_c_changed = max_c > 0 and prev['mc'] != 0 and max_c != prev['mc']
    chg_p_changed = chg_p > 0 and prev['cp'] != 0 and chg_p != prev['cp']
    chg_c_changed = chg_c > 0 and prev['cc'] != 0 and chg_c != prev['cc']
    
    # MAX SHIFT Formatting
    if max_c > 0 and max_c != prev['mc'] and prev['mc'] != 0: 
        max_shift_text[name]['ce'] = f"{int(prev['mc'])}→{int(max_c)}"
    else: max_shift_text[name]['ce'] = f"{int(max_c)}" if max_c > 0 else "No Data"

    if max_p > 0 and max_p != prev['mp'] and prev['mp'] != 0: 
        max_shift_text[name]['pe'] = f"{int(prev['mp'])}→{int(max_p)}"
    else: max_shift_text[name]['pe'] = f"{int(max_p)}" if max_p > 0 else "No Data"

    # CHG SHIFT Formatting
    if chg_c > 0 and chg_c != prev['cc'] and prev['cc'] != 0: 
        chg_shift_text[name]['ce'] = f"{int(prev['cc'])}→{int(chg_c)}"
    else: chg_shift_text[name]['ce'] = f"{int(chg_c)}" if chg_c > 0 else "No Data"

    if chg_p > 0 and chg_p != prev['cp'] and prev['cp'] != 0: 
        chg_shift_text[name]['pe'] = f"{int(prev['cp'])}→{int(chg_p)}"
    else: chg_shift_text[name]['pe'] = f"{int(chg_p)}" if chg_p > 0 else "No Data"

    current = {'mc': max_c, 'mp': max_p, 'cc': chg_c, 'cp': chg_p}
    display_shift = resolve_display_shift(prev, current)
    shift_label = determine_shift_label(prev, current, price_change_pct)

    if max_c > 0: last_strike_store[name]['mc'] = max_c
    if max_p > 0: last_strike_store[name]['mp'] = max_p
    if chg_c > 0: last_strike_store[name]['cc'] = chg_c
    if chg_p > 0: last_strike_store[name]['cp'] = chg_p

    return (
        (total_put / total_call if total_call > 0 else 1.0),
        max_c,
        max_p,
        chg_c,
        chg_p,
        max_p_changed,
        max_c_changed,
        chg_p_changed,
        chg_c_changed,
        display_shift,
        shift_label,
        max_p_delta,
        max_c_delta,
        chg_p_delta,
        chg_c_delta,
    )


# ================= MAIN =================

def calculate_heatmap(kite):
    fut_symbols = get_bank_futures(kite)
    spot_symbols = [get_spot_symbol(name) for name in ["HDFCBANK", "ICICIBANK"]]
    symbols = fut_symbols + [INDEX_SYMBOL] + spot_symbols
    data = get_symbol_quotes_with_fallback(kite, symbols)
    if not data:
        return 0, "", [], [], []
    
    score = 0
    report = ""
    
    alias = {"BANKNIFTY": "BNF", "HDFCBANK": "HDBFU", "ICICIBANK": "ICIBFU", "SBIN": "SBINFU", "AXISBANK": "AXISFU"}
    REPORT_BANKS = ["BANKNIFTY", "HDFCBANK", "ICICIBANK"]
    DISPLAY_BANKS = {"BANKNIFTY", "HDFCBANK", "ICICIBANK"}
    bn_alerts = []; stock_alerts = []; high_conviction_alerts = []; bank_signals = {}
    
    # Pre-collect data for all entities
    all_opt_tokens = []; underlying_map = {}
    bnf_future_symbol = next((s for s in fut_symbols if "BANKNIFTY" in s), "")
    for name in REPORT_BANKS:
        base_symbol = bnf_future_symbol if name == "BANKNIFTY" else next((s for s in fut_symbols if name in s), "")
        u_ltp = data.get(base_symbol, {}).get("last_price", 0)
        if u_ltp > 0:
            df = get_relevant_options(name, u_ltp)
            if not df.empty: underlying_map[name] = (df, u_ltp); all_opt_tokens.extend(df['instrument_token'].tolist())
    
    opt_quotes = get_option_quotes_with_fallback(kite, all_opt_tokens)

    # [NEW] Process BNF First for Report Header Alignment
    bn_ltp = data.get(bnf_future_symbol, {}).get("last_price", 0)
    bn_open = data.get(bnf_future_symbol, {}).get("ohlc", {}).get("open", 0)
    bn_change = ((bn_ltp - bn_open) / bn_open) * 100 if bn_open > 0 else 0
    (
        pcr_bn,
        max_c_bn,
        max_p_bn,
        chg_c_bn,
        chg_p_bn,
        max_p_bn_changed,
        max_c_bn_changed,
        chg_p_bn_changed,
        chg_c_bn_changed,
        display_shift_bn,
        shift_bn,
        max_p_bn_delta,
        max_c_bn_delta,
        chg_p_bn_delta,
        chg_c_bn_delta,
    ) = process_option_logic("BANKNIFTY", underlying_map.get("BANKNIFTY", (pd.DataFrame(),0)), opt_quotes, bn_alerts, bn_change)
    direction_bn, hedge_bn = determine_direction_and_hedge(
        shift_bn, bn_change, pcr_bn, max_p_bn_delta, max_c_bn_delta, chg_p_bn_delta, chg_c_bn_delta
    )
    bn_spot_symbol = get_spot_symbol("BANKNIFTY")
    bn_spot_ltp = data.get(bn_spot_symbol, {}).get("last_price", bn_ltp)
    analytics_bn = get_symbol_analytics(kite, bn_spot_symbol)
    levels_bn = build_levels_line("BANKNIFTY", bn_spot_ltp, analytics_bn)
    vwap_line_bn = format_vwap_line("BANKNIFTY", bn_spot_ltp, analytics_bn)
    sma200_line_bn = format_sma200_line(analytics_bn)
    read_line_bn = build_read_line(chg_p_bn, chg_c_bn, levels_bn)
    high_conviction_bn = evaluate_high_conviction(
        "BANKNIFTY",
        bn_ltp,
        bn_change,
        pcr_bn,
        max_c_bn,
        max_p_bn,
        chg_c_bn,
        chg_p_bn,
        shift_bn,
        max_p_bn_delta,
        max_c_bn_delta,
        chg_p_bn_delta,
        chg_c_bn_delta,
        analytics_bn,
    )
    if high_conviction_bn:
        high_conviction_alerts.append(high_conviction_bn)
    
    arrow = "⬆️" if bn_change > 0 else "⬇️"
    report += (
        f"BNF={bn_ltp:.1f} {arrow},SHIFT:{display_shift_bn}\n"
        f"{format_oi_pair('MAX_OI', format_shift_strike(max_p_bn, max_p_bn_changed, 'P', max_p_bn_delta), format_shift_strike(max_c_bn, max_c_bn_changed, 'C', max_c_bn_delta))}\n"
        f"{format_oi_pair('CHG_OI', format_shift_strike(chg_p_bn, chg_p_bn_changed, 'P', chg_p_bn_delta), format_shift_strike(chg_c_bn, chg_c_bn_changed, 'C', chg_c_bn_delta))}\n"
        f"{vwap_line_bn}\n"
        f"{sma200_line_bn}\n"
    )
    report += (
        f"{levels_bn}\n"
        f"{read_line_bn}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

    for name in REPORT_BANKS:
        sym = next((s for s in fut_symbols if name in s), None)
        if not sym or sym not in data: continue
        d = data[sym]; ltp, open_p, oi = d["last_price"], d["ohlc"]["open"], d.get("oi", 0)
        change = ((ltp - open_p) / open_p) * 100 if open_p > 0 else 0
        
        # Don't add BNF to the score calculation if it's the index itself, or handle weight
        if name != "BANKNIFTY":
            score += change * BANK_WEIGHTS.get(name, 0)
            bank_signals[name] = "BUY" if change > 0.3 else "SELL" if change < -0.3 else "NEUTRAL"
        
        # Determine which alert list to use (BNF future bursts go to bn_alerts)
        target_alerts = bn_alerts if name == "BANKNIFTY" else stock_alerts
        process_future_burst(sym, name, ltp, oi, target_alerts)
        
        # PCR and Max OI reporting (Skip re-processing BNF options since done above, but still need the string for other banks)
        if name != "BANKNIFTY":
            (
                pcr,
                max_c,
                max_p,
                chg_c,
                chg_p,
                max_p_changed,
                max_c_changed,
                chg_p_changed,
                chg_c_changed,
                display_shift,
                shift_label,
                max_p_delta,
                max_c_delta,
                chg_p_delta,
                chg_c_delta,
            ) = process_option_logic(name, underlying_map.get(name, (pd.DataFrame(),0)), opt_quotes, stock_alerts, change)
            spot_symbol = get_spot_symbol(name)
            spot_ltp = data.get(spot_symbol, {}).get("last_price", ltp)
            analytics = get_symbol_analytics(kite, spot_symbol)
            levels_line = build_levels_line(name, spot_ltp, analytics)
            vwap_line = format_vwap_line(name, spot_ltp, analytics)
            sma200_line = format_sma200_line(analytics)
            read_line = build_read_line(chg_p, chg_c, levels_line)
            direction_label, hedge_label = determine_direction_and_hedge(
                shift_label, change, pcr, max_p_delta, max_c_delta, chg_p_delta, chg_c_delta
            )
            high_conviction = evaluate_high_conviction(
                name,
                ltp,
                change,
                pcr,
                max_c,
                max_p,
                chg_c,
                chg_p,
                shift_label,
                max_p_delta,
                max_c_delta,
                chg_p_delta,
                chg_c_delta,
                analytics,
            )
            if high_conviction:
                high_conviction_alerts.append(high_conviction)
            if name in DISPLAY_BANKS:
                arrow = "⬆️" if change > 0 else "⬇️"
                report += (
                    f"{alias[name]}={ltp:.1f} {arrow},SHIFT:{display_shift}\n"
                    f"{format_oi_pair('MAX_OI', format_shift_strike(max_p, max_p_changed, 'P', max_p_delta), format_shift_strike(max_c, max_c_changed, 'C', max_c_delta))}\n"
                    f"{format_oi_pair('CHG_OI', format_shift_strike(chg_p, chg_p_changed, 'P', chg_p_delta), format_shift_strike(chg_c, chg_c_changed, 'C', chg_c_delta))}\n"
                    f"{vwap_line}\n"
                    f"{sma200_line}\n"
                )
                report += (
                    f"{levels_line}\n"
                    f"{read_line}\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                )

    h, i = bank_signals.get("HDFCBANK"), bank_signals.get("ICICIBANK")
    if h != i:
        report += f"\n⚠️ *TUG-OF-WAR:* HDFC({h}) vs ICICI({i})"
    
    report += f"\n⚖️ *SENTIMENT SCORE: {score:.2f}*"
    if abs(score) > 30 and h == i: report += "\n🌟🌟🌟 *3-STAR SIGNAL ACTIVE* 🌟🌟🌟"
    report += f"\n🚀 *STATUS: {'STRONG BULLISH' if score > 30 else 'STRONG BEARISH' if score < -30 else 'SIDEWAYS'}*"
    
    return score, report, bn_alerts, stock_alerts, high_conviction_alerts
