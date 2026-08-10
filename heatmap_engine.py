kimport os
import time
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from kite_rate_limiter import kite_historical_data, kite_quote
from websocket_flow import get_symbol_quotes, get_token_quotes

INDEX_BURST_NAMES = {"BANKNIFTY"}
STOCK_BURST_NAMES = {
    "RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK",
    "KOTAKBANK", "BANKBARODA", "PNB", "INDUSINDBK", "AUBANK",
    "INFY", "TCS", "WIPRO", "HCLTECH", "TECHM",
    "PERSISTENT", "OFSS", "TATASTEEL", "JSWSTEEL",
    "JINDALSTEL", "HINDALCO", "VEDL", "NATIONALUM", "SAIL",
    "COALINDIA", "NMDC", "HINDZINC", "M&M",
    "MARUTI", "BAJAJ-AUTO", "HEROMOTOCO", "TVSMOTOR", "ASHOKLEY",
    "EICHERMOT", "BHARATFORG", "LT", "SIEMENS", "ABB",
    "CUMMINSIND", "CGPOWER", "BHEL", "HAL", "BEL",
    "CIPLA", "SUNPHARMA", "DRREDDY", "LUPIN",
    "AUROPHARMA", "ZYDUSLIFE", "TORNTPHARM", "DIVISLAB", "MANKIND",
    "ITC", "HINDUNILVR", "NESTLEIND", "BRITANNIA", "TATACONSUM",
    "GODREJCP", "DABUR", "COLPAL", "ASIANPAINT",
    "GRASIM", "ULTRACEMCO", "SHREECEM", "AMBUJACEM",
    "ADANIENT", "ADANIPORTS", "ADANIPOWER", "ADANIGREEN", "ADANIENSOL",
    "NTPC", "POWERGRID", "TATAPOWER", "RECLTD", "PFC",
    "IOC", "BPCL", "HINDPETRO", "GAIL", "ONGC",
    "BHARTIARTL", "INDUSTOWER", "IDEA", "ETERNAL", "SWIGGY",
    "TRENT", "DLF", "GODREJPROP", "PRESTIGE", "LODHA",
    "INDHOTEL", "DELHIVERY", "BAJFINANCE", "BAJAJFINSV"
}
NSE_BURST_TRACK_NAMES = []
MCX_BURST_TRACK_NAMES = [
    "CRUDEOIL",
    "CRUDEOILM",
]
MCX_BURST_NAMES = set(MCX_BURST_TRACK_NAMES)
BURST_TRACK_NAMES = NSE_BURST_TRACK_NAMES
ENABLE_INDEX_BURST_ALERTS = os.getenv("ENABLE_INDEX_BURST_ALERTS", "false").lower() in (
    "true",
    "1",
    "yes",
    "on",
)
ENABLE_MCX_BURST_ALERTS = False
BURST_OPTION_STRIKE_RANGE = 30
BANKNIFTY_BURST_OPTION_STRIKE_RANGE = 15
STOCK_BURST_OPTION_STRIKE_RANGE = 5
MCX_BURST_OPTION_STRIKE_RANGE = int(os.getenv("MCX_BURST_OPTION_STRIKE_RANGE", "10"))
BURST_THRESHOLD_LOTS = int(os.getenv("BURST_THRESHOLD_LOTS", "100"))
OPTION_BURST_THRESHOLD_LOTS = int(os.getenv("OPTION_BURST_THRESHOLD_LOTS", "100"))
FUTURE_BURST_THRESHOLD_LOTS = int(os.getenv("FUTURE_BURST_THRESHOLD_LOTS", "2000"))
INDEX_BURST_THRESHOLD_LOTS = int(os.getenv("INDEX_OPTION_BURST_THRESHOLD_LOTS", str(OPTION_BURST_THRESHOLD_LOTS)))
STOCK_BURST_THRESHOLD_LOTS = 300
MCX_BURST_THRESHOLD_LOTS = int(os.getenv("MCX_OPTION_BURST_THRESHOLD_LOTS", "100"))
INDEX_FUTURE_BURST_THRESHOLD_LOTS = int(os.getenv("INDEX_FUTURE_BURST_THRESHOLD_LOTS", str(FUTURE_BURST_THRESHOLD_LOTS)))
STOCK_FUTURE_BURST_THRESHOLD_LOTS = int(os.getenv("STOCK_FUTURE_BURST_THRESHOLD_LOTS", str(FUTURE_BURST_THRESHOLD_LOTS)))
MCX_FUTURE_BURST_THRESHOLD_LOTS = int(os.getenv("MCX_FUTURE_BURST_THRESHOLD_LOTS", str(MCX_BURST_THRESHOLD_LOTS)))
BURST_REST_FALLBACK_CACHE_SECONDS = int(os.getenv("BURST_REST_FALLBACK_CACHE_SECONDS", "3"))
INDEX_SYMBOL = "NSE:NIFTY BANK"
INDEX_FUTURE_NAMES = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX", "SENSEX50"}
TARGET_MONTHLY_EXPIRY_MONTH = int(os.getenv("TARGET_MONTHLY_EXPIRY_MONTH", "7"))
TARGET_MONTHLY_EXPIRY_DAY = int(os.getenv("TARGET_MONTHLY_EXPIRY_DAY", "28"))
TARGET_MONTHLY_EXPIRY_YEAR = os.getenv("TARGET_MONTHLY_EXPIRY_YEAR", "").strip()

day_open_oi_store = {}
option_history = {}
active_watches = {}
gap_alert_store = {}
r3_alert_store = {}
r3_last_check_time = None
r3_watch_last_sent_time = None
s4_alert_store = {}
s4_state_store = {}
s4_last_slot = None
first_30m_mismatch_scan_dates = set()
first_30m_mismatch_last_scan_time = None
daily_mismatch_break_alert_store = {}
weekly_mismatch_break_alert_store = {}
daily_mismatch_setup_date = None
daily_mismatch_setup_rows = []
weekly_mismatch_setup_date = None
weekly_mismatch_setup_rows = []

born_breakout_last_check_time = None
born_breakout_alert_store = {}
burst_alert_store = {}

_options_df = None
_options_mtime = None
_futures_df = None
_futures_mtime = None
_last_logged_expiry = {}
_historical_cache = {}
_missing_lot_size_logs = set()
_burst_rest_symbol_cache = {"ts": 0.0, "data": {}}
_burst_rest_option_cache = {"ts": 0.0, "data": {}}
_burst_quote_status = {
    "source": "none",
    "detail": "",
    "ts": 0.0,
}
_burst_monitor_status = {}
_last_burst_session = None

IST = ZoneInfo("Asia/Kolkata")
NSE_BURST_START_TIME = datetime.strptime("09:00", "%H:%M").time()
NSE_BURST_END_TIME = datetime.strptime("15:30", "%H:%M").time()
MCX_BURST_START_TIME = datetime.strptime("15:30", "%H:%M").time()
MCX_BURST_END_TIME = datetime.strptime("23:30:59", "%H:%M:%S").time()
MONTHLY_FUTURE_GAP_THRESHOLD_PCT = 2.0
MONTHLY_FUTURE_NEXT_GAP_MAX_PCT = 1.0
MONTHLY_FUTURE_MATCH_ABOVE_PCT = 0.5
MONTHLY_FUTURE_MATCH_BELOW_PCT = 0.95
MONTHLY_FUTURE_MATCH_NEXT_MIN_PCT = 2.0
MONTHLY_FUTURE_GAP_START_TIME = datetime.strptime("09:15", "%H:%M").time()
GAP_ALERT_COOLDOWN_SECONDS = 3600
R3_PIVOT_ALERT_START_TIME = datetime.strptime("09:15", "%H:%M").time()
R3_PIVOT_CLOSE_REMINDER_START_TIME = datetime.strptime("15:00", "%H:%M").time()
R3_PIVOT_RANGE_PCT = 0.5
R3_PIVOT_CHECK_INTERVAL_SECONDS = 300
R3_PIVOT_REMINDER_SECONDS = 3600
R3_PIVOT_CLOSE_REMINDER_SECONDS = 600
SEND_R3_WATCHLIST = os.getenv("SEND_R3_WATCHLIST", "false").lower() in ("true", "1", "yes")
S4_PIVOT_RANGE_PCT = R3_PIVOT_RANGE_PCT
S4_PIVOT_CHECK_TIMES = [
    datetime.strptime(value, "%H:%M").time()
    for value in ("09:30", "10:30", "11:30", "12:30", "13:30", "14:30", "15:25")
]
S4_PIVOT_CHECK_WINDOW_SECONDS = 120
BORN_BREAKOUT_MORNING_START_TIME = datetime.strptime("09:00", "%H:%M").time()
BORN_BREAKOUT_MORNING_END_TIME = datetime.strptime("09:20", "%H:%M").time()
BORN_BREAKOUT_AFTERNOON_START_TIME = datetime.strptime("15:15", "%H:%M").time()
BORN_BREAKOUT_AFTERNOON_END_TIME = datetime.strptime("15:30", "%H:%M").time()
BORN_BREAKOUT_CHECK_INTERVAL_SECONDS = 1800
BORN_BREAKOUT_LOOKBACK_DAYS = 180
# Pause non-burst reports only for this date. They resume automatically the next day.
FIRST_30M_MISMATCH_CANDLE_START_TIME = datetime.strptime("09:15", "%H:%M").time()
FIRST_30M_MISMATCH_SCAN_START_TIME = datetime.strptime("09:45", "%H:%M").time()
FIRST_30M_MISMATCH_GAP_THRESHOLD_PCT = float(os.getenv("FIRST_30M_MISMATCH_GAP_THRESHOLD_PCT", "1.0"))
FIRST_30M_MISMATCH_MIN_VOLUME = int(os.getenv("FIRST_30M_MISMATCH_MIN_VOLUME", "100000"))
FIRST_30M_MISMATCH_RETRY_SECONDS = 30
FIRST_30M_OPTION_ITM_COUNT = int(os.getenv("FIRST_30M_OPTION_ITM_COUNT", "4"))
DAILY_WEEKLY_MISMATCH_MIN_VOLUME = int(os.getenv("DAILY_WEEKLY_MISMATCH_MIN_VOLUME", "1000000"))
PREVIOUS_DAY_MISMATCH_LOOKBACK_DAYS = int(os.getenv("PREVIOUS_DAY_MISMATCH_LOOKBACK_DAYS", "20"))
WEEKLY_MISMATCH_LOOKBACK_DAYS = int(os.getenv("WEEKLY_MISMATCH_LOOKBACK_DAYS", "100"))
NON_BURST_ALERT_PAUSE_DATES = {"2026-05-26"}


def is_index_underlying(name):
    return name in INDEX_BURST_NAMES


def is_mcx_underlying(name):
    return name in MCX_BURST_NAMES


def is_burst_underlying(name):
    return name in INDEX_BURST_NAMES or name in STOCK_BURST_NAMES or is_mcx_underlying(name)


def get_option_burst_threshold(name):
    if is_index_underlying(name):
        return INDEX_BURST_THRESHOLD_LOTS
    if is_mcx_underlying(name):
        return MCX_BURST_THRESHOLD_LOTS
    return STOCK_BURST_THRESHOLD_LOTS


def get_future_burst_threshold(name):
    if is_index_underlying(name):
        return INDEX_FUTURE_BURST_THRESHOLD_LOTS
    if is_mcx_underlying(name):
        return MCX_FUTURE_BURST_THRESHOLD_LOTS
    return STOCK_FUTURE_BURST_THRESHOLD_LOTS


def get_burst_threshold(name):
    return get_option_burst_threshold(name)


def get_burst_option_strike_range(name):
    if is_mcx_underlying(name):
        return MCX_BURST_OPTION_STRIKE_RANGE
    if name == "BANKNIFTY":
        return BANKNIFTY_BURST_OPTION_STRIKE_RANGE
    if name in STOCK_BURST_NAMES:
        return STOCK_BURST_OPTION_STRIKE_RANGE
    return BURST_OPTION_STRIKE_RANGE


def get_burst_session(now_ist=None):
    now_ist = now_ist or datetime.now(IST)
    if now_ist.weekday() > 4:
        return None

    t = now_ist.time()
    # NSE session ends at 15:30:00
    if NSE_BURST_START_TIME <= t < NSE_BURST_END_TIME:
        return "nse"
    # MCX session starts at 15:30:00
    if (
        ENABLE_MCX_BURST_ALERTS
        and MCX_BURST_START_TIME <= t <= MCX_BURST_END_TIME
    ):
        return "mcx"
    return None


def is_burst_session_open(now_ist=None):
    return get_burst_session(now_ist) is not None


def get_active_burst_names(now_ist=None):
    session = get_burst_session(now_ist)
    if session == "mcx":
        return []
    if session == "nse":
        return sorted(set(INDEX_BURST_NAMES) | set(STOCK_BURST_NAMES))
    return []


def get_burst_subscription_names(now_ist=None):
    now_ist = now_ist or datetime.now(IST)
    active_names = get_active_burst_names(now_ist)
    if active_names:
        return active_names

    if now_ist.weekday() <= 4:
        t = now_ist.time()
        if t < NSE_BURST_START_TIME:
            return sorted(set(INDEX_BURST_NAMES) | set(STOCK_BURST_NAMES))
        if NSE_BURST_END_TIME <= t <= MCX_BURST_END_TIME:
            return []

    return sorted(set(INDEX_BURST_NAMES) | set(STOCK_BURST_NAMES))


def non_burst_alerts_paused_today():
    return datetime.now(IST).date().isoformat() in NON_BURST_ALERT_PAUSE_DATES


def in_born_breakout_window(now_ist):
    t = now_ist.time()
    return (
        BORN_BREAKOUT_MORNING_START_TIME <= t <= BORN_BREAKOUT_MORNING_END_TIME
        or BORN_BREAKOUT_AFTERNOON_START_TIME <= t <= BORN_BREAKOUT_AFTERNOON_END_TIME
    )


def get_due_s4_slot(now_ist):
    current = datetime.combine(
        now_ist.date(),
        now_ist.time(),
        tzinfo=IST,
    )

    for slot_time in S4_PIVOT_CHECK_TIMES:
        slot = datetime.combine(
            now_ist.date(),
            slot_time,
            tzinfo=IST,
        )
        delta = (current - slot).total_seconds()
        if 0 <= delta <= S4_PIVOT_CHECK_WINDOW_SECONDS:
            return slot.strftime("%Y-%m-%d %H:%M")

    return None


def get_monthly_expiry(expiries, rollover_days=1):
    return get_target_monthly_expiry(expiries)


def get_target_monthly_expiry(expiries):
    valid_expiries = sorted(exp for exp in expiries if pd.notna(exp))
    if not valid_expiries:
        return None

    month_last_expiries = {}
    for expiry in valid_expiries:
        month_last_expiries[(int(expiry.year), int(expiry.month))] = expiry

    ordered_monthlies = [month_last_expiries[key] for key in sorted(month_last_expiries)]
    target_year = int(TARGET_MONTHLY_EXPIRY_YEAR) if TARGET_MONTHLY_EXPIRY_YEAR else None
    target_monthlies = [
        exp
        for exp in ordered_monthlies
        if exp.month == TARGET_MONTHLY_EXPIRY_MONTH
        and exp.day == TARGET_MONTHLY_EXPIRY_DAY
        and (target_year is None or exp.year == target_year)
    ]
    if target_monthlies:
        return target_monthlies[-1]

    return None


def get_next_monthly_expiry(expiries):
    valid_expiries = sorted(exp for exp in expiries if pd.notna(exp))
    if not valid_expiries:
        return None

    now_ist = datetime.now(IST)
    month_last_expiries = {}
    for expiry in valid_expiries:
        month_last_expiries[(int(expiry.year), int(expiry.month))] = expiry

    ordered_monthlies = [month_last_expiries[key] for key in sorted(month_last_expiries)]
    future_monthlies = [exp for exp in ordered_monthlies if exp.date() >= now_ist.date()]

    if len(future_monthlies) >= 2:
        return future_monthlies[1]
    return future_monthlies[0] if future_monthlies else ordered_monthlies[-1]


def _get_instruments_mtime():
    try:
        return os.path.getmtime("instruments.csv")
    except OSError:
        return None


def load_options_data():
    global _options_df, _options_mtime
    current_mtime = _get_instruments_mtime()
    if _options_df is None or _options_mtime != current_mtime:
        try:
            df = pd.read_csv("instruments.csv", low_memory=False)
            _options_df = df[df["segment"].isin(["NFO-OPT", "BFO-OPT", "MCX-OPT"])].copy()
            expiry = pd.to_datetime(_options_df["expiry"], format="%Y-%m-%d", errors="coerce")
            if expiry.isna().mean() > 0.05:
                expiry = pd.to_datetime(_options_df["expiry"], dayfirst=True, errors="coerce")
            _options_df["expiry"] = expiry
            _options_mtime = current_mtime
        except Exception as e:
            print(f"Error loading Options: {e}")
    return _options_df


def load_futures_data():
    global _futures_df, _futures_mtime
    current_mtime = _get_instruments_mtime()
    if _futures_df is None or _futures_mtime != current_mtime:
        try:
            df = pd.read_csv("instruments.csv", low_memory=False)
            _futures_df = df[df["segment"].str.contains("-FUT", na=False)].copy()
            expiry = pd.to_datetime(_futures_df["expiry"], format="%Y-%m-%d", errors="coerce")
            if expiry.isna().mean() > 0.05:
                expiry = pd.to_datetime(_futures_df["expiry"], dayfirst=True, errors="coerce")
            _futures_df["expiry"] = expiry
            _futures_mtime = current_mtime
        except Exception as e:
            print(f"Error loading Futures: {e}")
    return _futures_df


def _normalize_lot_size(value):
    try:
        if pd.isna(value):
            return None
        lot_size = int(float(value))
    except Exception:
        return None
    return lot_size if lot_size > 0 else None


def _get_row_lot_size(row):
    if row is None or "lot_size" not in row:
        return None
    return _normalize_lot_size(row.get("lot_size"))


def get_future_lot_size(symbol):
    df = load_futures_data()
    if df is None or df.empty or not symbol:
        return None

    tradingsymbol = symbol.split(":", 1)[1] if ":" in symbol else symbol
    rows = df[df["tradingsymbol"] == tradingsymbol]
    if rows.empty:
        return None
    return _get_row_lot_size(rows.iloc[0])


def _log_missing_lot_size_once(key, label):
    if key in _missing_lot_size_logs:
        return
    _missing_lot_size_logs.add(key)
    print(f"Skipping burst lot calculation: lot_size missing in instruments.csv for {label}.")


def load_stock_futures_data():
    df = load_futures_data()
    if df is None or df.empty:
        return pd.DataFrame()
    return df[
        (df["exchange"] == "NFO")
        & (df["segment"] == "NFO-FUT")
        & (df["name"].notna())
        & (~df["name"].isin(INDEX_FUTURE_NAMES))
    ].copy()


def get_spot_symbol(name):
    INDEX_SPOT_MAP = {
        "NIFTY": "NSE:NIFTY 50",
        "BANKNIFTY": "NSE:NIFTY BANK",
        "FINNIFTY": "NSE:NIFTY FIN SERVICE",
        "MIDCPNIFTY": "NSE:NIFTY MID SELECT",
        "SENSEX": "BSE:SENSEX",
        "CRUDEOIL": "MCX:MCXCRUDEX",
        "CRUDEOILM": "MCX:MCXCRUDEX",
    }
    if name in INDEX_SPOT_MAP:
        return INDEX_SPOT_MAP[name]
    if is_mcx_underlying(name):
        return f"MCX:{name}"
    return f"NSE:{name}"

def _get_active_stock_future_contracts():
    futures = load_stock_futures_data()
    if futures.empty:
        return []
    return futures.to_dict("records")

def _get_all_active_future_contracts():
    df = load_futures_data()
    if df is None or df.empty:
        return []
    # Include NFO-FUT and MCX-FUT
    mask = (df["exchange"].isin(["NFO", "MCX"])) & (df["segment"].str.contains("-FUT", na=False))
    futures = df[mask].copy()
    if futures.empty:
        return []

    futures = futures.sort_values(["name", "expiry", "tradingsymbol"])
    contracts = []
    for name, rows in futures.groupby("name"):
        preferred_expiry = get_monthly_expiry(rows["expiry"].unique())
        if preferred_expiry is None:
            continue

        selected = rows[rows["expiry"] == preferred_expiry]
        if selected.empty:
            continue

        row = selected.iloc[0]
        current_expiry = row["expiry"]
        exchange = str(row.get("exchange", "") or "").strip() or "NFO"
        next_futures = rows[rows["expiry"] > current_expiry]

        next_symbol = None
        next_month_label = "Next"
        next_token = None
        next_expiry = None
        if not next_futures.empty:
            next_row = next_futures.iloc[0]
            next_exchange = str(next_row.get("exchange", "") or "").strip() or "NFO"
            next_symbol = f"{next_exchange}:{next_row['tradingsymbol']}"
            next_month_label = _format_month_label(next_row["expiry"])
            next_token = int(next_row["instrument_token"])
            next_expiry = next_row["expiry"]

        contracts.append(
            {
                "name": name,
                "symbol": f"{exchange}:{row['tradingsymbol']}",
                "token": int(row["instrument_token"]),
                "expiry": current_expiry,
                "month_label": _format_month_label(current_expiry),
                "next_symbol": next_symbol,
                "next_month_label": next_month_label,
                "next_token": next_token,
                "next_expiry": next_expiry,
            }
        )
    return contracts


def get_active_future(name):
    df = load_futures_data()
    if df is None or df.empty:
        return None
    futures = df[df["name"] == name]
    if futures.empty:
        return None

    preferred_expiry = get_target_monthly_expiry(futures["expiry"].unique())

    if preferred_expiry is None:
        return None

    selected = futures[futures["expiry"] == preferred_expiry]
    if selected.empty:
        return None

    row = selected.iloc[0]
    tradingsymbol = row["tradingsymbol"]
    exchange = str(row.get("exchange", "") or "").strip() or "NFO"
    log_key = f"future:{name}"
    expiry_text = preferred_expiry.strftime("%d-%m-%Y")
    if _last_logged_expiry.get(log_key) != expiry_text:
        print(f"Selected future expiry for {name}: {expiry_text} ({exchange}:{tradingsymbol})")
        _last_logged_expiry[log_key] = expiry_text
    return f"{exchange}:{tradingsymbol}"


def get_future_expiry_text(symbol):
    df = load_futures_data()
    if df is None or df.empty or not symbol:
        return ""

    tradingsymbol = symbol.split(":", 1)[1] if ":" in symbol else symbol
    rows = df[df["tradingsymbol"] == tradingsymbol]
    if rows.empty:
        return ""

    expiry = rows.iloc[0].get("expiry")
    if pd.isna(expiry):
        return ""
    return expiry.strftime("%d-%m-%Y") if hasattr(expiry, "strftime") else str(expiry)


def get_symbol_quotes_with_fallback(kite, symbols, max_age_seconds=15):
    data = get_symbol_quotes(symbols, max_age_seconds=max_age_seconds)
    missing = [symbol for symbol in symbols if symbol not in data]
    for i in range(0, len(missing), 500):
        chunk = missing[i:i + 500]
        if not chunk:
            continue
        try:
            data.update(kite_quote(kite, chunk))
        except Exception as e:
            print(f"Fallback symbol quote error: {e}")
    return data


def get_symbol_quotes_ws_only(symbols, max_age_seconds=15):
    return get_symbol_quotes(symbols, max_age_seconds=max_age_seconds)


def get_option_quotes_ws_only(tokens, max_age_seconds=15):
    token_strings = [str(int(token)) for token in tokens]
    return get_token_quotes(token_strings, max_age_seconds=max_age_seconds)


def get_option_quotes_with_fallback(kite, tokens, max_age_seconds=15):
    token_strings = [str(int(token)) for token in tokens]
    data = get_token_quotes(token_strings, max_age_seconds=max_age_seconds)
    missing = [int(token) for token in token_strings if token not in data]
    for i in range(0, len(missing), 400):
        chunk = missing[i:i + 400]
        if not chunk:
            continue
        try:
            fresh = kite_quote(kite, chunk)
            data.update({str(key): value for key, value in fresh.items()})
        except Exception as e:
            print(f"Fallback option quote error: {e}")
    return data


def _set_burst_quote_status(source, detail=""):
    _burst_quote_status["source"] = source
    _burst_quote_status["detail"] = detail
    _burst_quote_status["ts"] = time.time()


def get_burst_quote_status():
    return dict(_burst_quote_status)


def _set_burst_monitor_status(status):
    _burst_monitor_status.clear()
    _burst_monitor_status.update(status)
    _burst_monitor_status["ts"] = time.time()


def get_burst_monitor_status():
    return dict(_burst_monitor_status)


def _cache_has_keys(cache, keys):
    data = cache.get("data") or {}
    return all(key in data for key in keys)


def _get_burst_symbol_quotes_with_fallback(kite, symbols):
    now = time.time()
    keys = list(dict.fromkeys(symbols))
    if (
        now - _burst_rest_symbol_cache.get("ts", 0.0) <= BURST_REST_FALLBACK_CACHE_SECONDS
        and _cache_has_keys(_burst_rest_symbol_cache, keys)
    ):
        data = _burst_rest_symbol_cache["data"]
        return {key: data[key] for key in keys}

    data = get_symbol_quotes_with_fallback(kite, keys)
    if data:
        _burst_rest_symbol_cache["ts"] = now
        _burst_rest_symbol_cache["data"] = dict(data)
    return data


def _get_burst_option_quotes_with_fallback(kite, tokens):
    now = time.time()
    keys = [str(int(token)) for token in tokens]
    if (
        now - _burst_rest_option_cache.get("ts", 0.0) <= BURST_REST_FALLBACK_CACHE_SECONDS
        and _cache_has_keys(_burst_rest_option_cache, keys)
    ):
        data = _burst_rest_option_cache["data"]
        return {key: data[key] for key in keys}

    data = get_option_quotes_with_fallback(kite, tokens)
    if data:
        _burst_rest_option_cache["ts"] = now
        _burst_rest_option_cache["data"] = dict(data)
    return data


def get_historical_data_cached(kite, token, from_time, to_time, interval):
    key = (
        int(token),
        interval,
        int(from_time.timestamp()) if hasattr(from_time, "timestamp") else str(from_time),
        int(to_time.timestamp()) if hasattr(to_time, "timestamp") else str(to_time),
    )
    cached = _historical_cache.get(key)
    if cached:
        return cached["candles"]

    candles = kite_historical_data(kite, token, from_time, to_time, interval)
    if len(_historical_cache) > 5000:
        oldest_keys = sorted(
            _historical_cache,
            key=lambda item: _historical_cache[item]["ts"],
        )[:500]
        for old_key in oldest_keys:
            _historical_cache.pop(old_key, None)

    _historical_cache[key] = {"ts": time.time(), "candles": candles}
    return candles


def get_burst_futures(kite, names=None):
    names = list(names or get_burst_subscription_names())
    symbols = []
    for name in names:
        sym = get_active_future(name)
        if sym:
            symbols.append(sym)
    summary_key = f"future_summary:{','.join(names)}"
    summary_text = ", ".join(symbols) if symbols else "none"
    if _last_logged_expiry.get(summary_key) != summary_text:
        print(f"Selected tracked futures: {summary_text}")
        _last_logged_expiry[summary_key] = summary_text
    return symbols


def get_bank_futures(kite):
    return get_burst_futures(kite, NSE_BURST_TRACK_NAMES)


def _format_month_label(expiry):
    if pd.isna(expiry):
        return "MONTHLY"
    return expiry.strftime("%b").upper()


def _get_active_stock_future_contracts():
    futures = load_stock_futures_data()
    if futures.empty:
        return []

    futures = futures.sort_values(["name", "expiry", "tradingsymbol"])
    contracts = []
    for name, rows in futures.groupby("name"):
        preferred_expiry = get_monthly_expiry(rows["expiry"].unique())
        if preferred_expiry is None:
            continue

        selected = rows[rows["expiry"] == preferred_expiry]
        if selected.empty:
            continue

        row = selected.iloc[0]
        current_expiry = row["expiry"]
        next_futures = rows[rows["expiry"] > current_expiry]

        next_symbol = None
        next_month_label = "Next"
        next_token = None
        next_expiry = None
        if not next_futures.empty:
            next_row = next_futures.iloc[0]
            next_symbol = f"NFO:{next_row['tradingsymbol']}"
            next_month_label = _format_month_label(next_row["expiry"])
            next_token = int(next_row["instrument_token"])
            next_expiry = next_row["expiry"]

        contracts.append(
            {
                "name": name,
                "symbol": f"NFO:{row['tradingsymbol']}",
                "token": int(row["instrument_token"]),
                "expiry": current_expiry,
                "month_label": _format_month_label(current_expiry),
                "next_symbol": next_symbol,
                "next_month_label": next_month_label,
                "next_token": next_token,
                "next_expiry": next_expiry,
            }
        )
    return contracts


def _get_active_index_future_contracts():
    futures = load_futures_data()
    if futures is None or futures.empty:
        return []

    index_futures = futures[
        (futures["name"].isin(INDEX_FUTURE_NAMES))
        & (futures["segment"].str.contains("-FUT", na=False))
    ].copy()
    if index_futures.empty:
        return []

    index_futures = index_futures.sort_values(["name", "expiry", "tradingsymbol"])
    contracts = []
    for name, rows in index_futures.groupby("name"):
        preferred_expiry = get_monthly_expiry(rows["expiry"].unique())
        if preferred_expiry is None:
            continue

        selected = rows[rows["expiry"] == preferred_expiry]
        if selected.empty:
            continue

        row = selected.iloc[0]
        exchange = str(row.get("exchange", "") or "").strip() or "NFO"
        contracts.append(
            {
                "name": name,
                "symbol": f"{exchange}:{row['tradingsymbol']}",
                "token": int(row["instrument_token"]),
                "expiry": row["expiry"],
                "month_label": _format_month_label(row["expiry"]),
                "kind": "INDEX FUTURE",
            }
        )
    return contracts


def _get_first_30m_future_contracts():
    contracts = []
    seen_symbols = set()

    for contract in _get_active_index_future_contracts():
        symbol = contract["symbol"]
        if symbol in seen_symbols:
            continue
        seen_symbols.add(symbol)
        contracts.append(contract)

    for contract in _get_active_stock_future_contracts():
        symbol = contract["symbol"]
        if symbol in seen_symbols:
            continue
        seen_symbols.add(symbol)
        item = dict(contract)
        item["kind"] = "STOCK FUTURE"
        contracts.append(item)

    return contracts


def _candle_color(open_price, close_price):
    if close_price > open_price:
        return "GREEN"
    if close_price < open_price:
        return "RED"
    return None


def _volume_candle_color(previous_close, close_price):
    if close_price > previous_close:
        return "GREEN"
    if close_price < previous_close:
        return "RED"
    return None


def _open_extreme_label(open_price, high, low):
    try:
        open_price = float(open_price or 0)
        high = float(high or 0)
        low = float(low or 0)
    except Exception:
        return ""

    if open_price <= 0 or high <= 0 or low <= 0:
        return ""
    if abs(open_price - high) <= 1e-9:
        return "open=high"
    if abs(open_price - low) <= 1e-9:
        return "open=low"
    return ""


def _get_first_30m_candle(kite, token, now_ist):
    session_start = datetime.combine(
        now_ist.date(),
        FIRST_30M_MISMATCH_CANDLE_START_TIME,
        tzinfo=IST,
    )
    session_end = session_start + timedelta(minutes=30)
    try:
        candles = kite_historical_data(kite, token, session_start, session_end, "30minute")
    except Exception as e:
        print(f"First 30m historical data error for {token}: {e}")
        return None

    for candle in candles:
        candle_time = candle.get("date")
        if hasattr(candle_time, "astimezone"):
            candle_time = candle_time.astimezone(IST)
        if (
            candle_time
            and candle_time.date() == now_ist.date()
            and candle_time.time() == FIRST_30M_MISMATCH_CANDLE_START_TIME
        ):
            return candle

    for candle in candles:
        candle_time = candle.get("date")
        if hasattr(candle_time, "astimezone"):
            candle_time = candle_time.astimezone(IST)
        if candle_time and candle_time.date() == now_ist.date():
            return candle

    return None


def _get_first_30m_candle_context(kite, token, now_ist, label="First 30m"):
    session_start = datetime.combine(
        now_ist.date(),
        FIRST_30M_MISMATCH_CANDLE_START_TIME,
        tzinfo=IST,
    )
    session_end = session_start + timedelta(minutes=30)
    prev_day = _get_previous_trading_day(now_ist)
    from_time = datetime.combine(
        prev_day,
        datetime.strptime("09:15", "%H:%M").time(),
        tzinfo=IST,
    )

    try:
        candles = get_historical_data_cached(kite, token, from_time, session_end, "30minute")
    except Exception as e:
        print(f"{label} historical data error for {token}: {e}")
        return None

    normalized = []
    for candle in candles:
        candle_time = candle.get("date")
        if candle_time is None:
            continue
        if hasattr(candle_time, "astimezone"):
            candle_time = candle_time.astimezone(IST)
        normalized.append((candle_time, candle))
    normalized.sort(key=lambda item: item[0])

    first_index = None
    for index, (candle_time, _) in enumerate(normalized):
        if (
            candle_time.date() == now_ist.date()
            and candle_time.time() == FIRST_30M_MISMATCH_CANDLE_START_TIME
        ):
            first_index = index
            break

    if first_index is None:
        return None

    previous = [item[1] for item in normalized[:first_index]][-5:]
    if len(previous) < 5:
        return None

    previous_close = float(previous[-1].get("close", 0) or 0)
    previous_volume_max = max(float(c.get("volume", 0) or 0) for c in previous)
    return {
        "candle": normalized[first_index][1],
        "previous_close": previous_close,
        "previous_volume_max": previous_volume_max,
    }


def _get_first_30m_itm_options(name, ltp, option_type, count=None):
    df = load_options_data()
    if df is None or df.empty or ltp <= 0:
        return pd.DataFrame()

    options = df[
        (df["name"] == name)
        & (df["instrument_type"] == option_type)
    ].copy()
    if options.empty:
        return pd.DataFrame()

    monthly_expiry = get_monthly_expiry(options["expiry"].unique())
    if monthly_expiry is None:
        return pd.DataFrame()

    options = options[options["expiry"] == monthly_expiry].copy()
    if options.empty:
        return pd.DataFrame()

    if option_type == "CE":
        options = options[options["strike"] < ltp].sort_values("strike", ascending=False)
    else:
        options = options[options["strike"] > ltp].sort_values("strike", ascending=True)

    limit = count if count is not None else FIRST_30M_OPTION_ITM_COUNT
    return options.head(max(0, int(limit))).copy()


def _build_first_30m_option_mismatch_rows(kite, name, ltp, gap_pct, now_ist):
    option_type = "PE" if gap_pct > 0 else "CE"
    rows = []

    for _, option in _get_first_30m_itm_options(name, ltp, option_type).iterrows():
        context = _get_first_30m_candle_context(
            kite,
            int(option["instrument_token"]),
            now_ist,
            label="First 30m option",
        )
        if not context:
            continue

        candle = context["candle"]
        previous_close = float(context["previous_close"] or 0)
        previous_volume_max = float(context["previous_volume_max"] or 0)
        open_price = float(candle.get("open", 0) or 0)
        close = float(candle.get("close", 0) or 0)
        volume = float(candle.get("volume", 0) or 0)
        if previous_close <= 0 or open_price <= 0 or close <= 0:
            continue
        if volume <= FIRST_30M_MISMATCH_MIN_VOLUME or volume <= previous_volume_max:
            continue

        option_gap_pct = ((open_price - previous_close) / previous_close) * 100
        if abs(option_gap_pct) < FIRST_30M_MISMATCH_GAP_THRESHOLD_PCT:
            continue

        price_color = _candle_color(open_price, close)
        volume_color = _volume_candle_color(previous_close, close)
        if not price_color or not volume_color or price_color == volume_color:
            continue

        rows.append(
            {
                "symbol": option["tradingsymbol"],
                "strike": float(option["strike"]),
                "type": option_type,
                "gap_pct": option_gap_pct,
                "volume": volume,
                "previous_volume_max": previous_volume_max,
                "price_color": price_color,
                "volume_color": volume_color,
            }
        )

    return rows


def build_first_30m_future_volume_mismatch_alerts(kite):
    global first_30m_mismatch_last_scan_time

    now_ist = datetime.now(IST)
    if now_ist.weekday() > 4 or now_ist.time() < FIRST_30M_MISMATCH_SCAN_START_TIME:
        return []

    scan_date = now_ist.date().isoformat()
    if scan_date in first_30m_mismatch_scan_dates:
        return []

    if (
        first_30m_mismatch_last_scan_time
        and (now_ist - first_30m_mismatch_last_scan_time).total_seconds()
        < FIRST_30M_MISMATCH_RETRY_SECONDS
    ):
        return []
    first_30m_mismatch_last_scan_time = now_ist

    contracts = _get_first_30m_future_contracts()
    if not contracts:
        return []

    symbols = [contract["symbol"] for contract in contracts]
    data = get_symbol_quotes_with_fallback(kite, symbols)
    if not data:
        return []

    candidates = []
    for contract in contracts:
        quote = data.get(contract["symbol"], {})
        ohlc = quote.get("ohlc") or {}
        previous_close = float(ohlc.get("close", 0) or 0)
        day_open = float(ohlc.get("open", 0) or 0)
        ltp = float(quote.get("last_price", 0) or day_open)
        if previous_close <= 0 or day_open <= 0:
            continue

        rough_gap_pct = ((day_open - previous_close) / previous_close) * 100
        if abs(rough_gap_pct) < FIRST_30M_MISMATCH_GAP_THRESHOLD_PCT:
            continue

        item = dict(contract)
        item["previous_close"] = previous_close
        item["ltp"] = ltp
        candidates.append(item)

    if not candidates:
        first_30m_mismatch_scan_dates.add(scan_date)
        return []

    rows = []
    processed_candles = 0
    for contract in candidates:
        context = _get_first_30m_candle_context(
            kite,
            contract["token"],
            now_ist,
            label="First 30m future",
        )
        if not context:
            continue
        processed_candles += 1

        candle = context["candle"]
        previous_close = float(contract["previous_close"])
        historical_previous_close = float(context["previous_close"] or 0)
        previous_volume_max = float(context["previous_volume_max"] or 0)
        open_price = float(candle.get("open", 0) or 0)
        high = float(candle.get("high", 0) or 0)
        low = float(candle.get("low", 0) or 0)
        close = float(candle.get("close", 0) or 0)
        volume = float(candle.get("volume", 0) or 0)
        if previous_close <= 0 or historical_previous_close <= 0 or open_price <= 0 or close <= 0:
            continue
        if volume <= FIRST_30M_MISMATCH_MIN_VOLUME or volume <= previous_volume_max:
            continue

        gap_pct = ((open_price - previous_close) / previous_close) * 100
        if abs(gap_pct) < FIRST_30M_MISMATCH_GAP_THRESHOLD_PCT:
            continue

        price_color = _candle_color(open_price, close)
        volume_color = _volume_candle_color(historical_previous_close, close)
        if not price_color or not volume_color or price_color == volume_color:
            continue

        option_rows = _build_first_30m_option_mismatch_rows(
            kite,
            contract["name"],
            float(contract.get("ltp", 0) or close),
            gap_pct,
            now_ist,
        )

        rows.append(
            {
                "name": contract["name"],
                "symbol": contract["symbol"],
                "kind": contract["kind"],
                "month_label": contract["month_label"],
                "previous_close": previous_close,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "previous_volume_max": previous_volume_max,
                "gap_pct": gap_pct,
                "price_color": price_color,
                "volume_color": volume_color,
                "option_rows": option_rows,
            }
        )

    if processed_candles == 0:
        return []

    first_30m_mismatch_scan_dates.add(scan_date)
    if not rows:
        return []

    rows.sort(key=lambda item: abs(item["gap_pct"]), reverse=True)
    alerts = []
    chunk_size = 20
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i:i + chunk_size]
        body_lines = []
        for item in chunk:
            gap_label = "GAP UP" if item["gap_pct"] > 0 else "GAP DOWN"
            open_extreme = _open_extreme_label(item["open"], item["high"], item["low"])
            open_extreme_text = f" | {open_extreme}" if open_extreme else ""
            body_lines.append(
                f"{item['name']} {item['month_label']} FUT: "
                f"{gap_label} {item['gap_pct']:+.2f}% | "
                f"Vol {format_volume(item['volume'])} > Prev5 Max {format_volume(item['previous_volume_max'])} | "
                f"Price {item['price_color']} vs Volume {item['volume_color']}"
                f"{open_extreme_text}"
            )
            if item.get("option_rows"):
                body_lines.append("ITM OPTIONS:")
                for option in item["option_rows"]:
                    body_lines.append(
                        f"Strike {option['strike']:.0f} {option['type']} | Symbol: {option['symbol']} | "
                        f"Gap {option['gap_pct']:+.2f}% | "
                        f"Vol {format_volume(option['volume'])} > Prev5 Max {format_volume(option['previous_volume_max'])} | "
                        f"Price {option['price_color']} vs Volume {option['volume_color']}"
                    )

        body = "\n".join(body_lines)
        alerts.append(
            "FIRST 30M GAP VOLUME MISMATCH\n\n"
            f"{body}"
        )

    return alerts


def get_relevant_options(name, ltp, strike_range=None):
    df = load_options_data()
    if df is None or df.empty:
        return pd.DataFrame()

    options = df[df["name"] == name]
    if options.empty:
        return pd.DataFrame()

    # Changed from get_next_monthly_expiry to get_monthly_expiry for all
    monthly_expiry = get_monthly_expiry(options["expiry"].unique())

    selected_expiries = [monthly_expiry] if monthly_expiry is not None else []

    if not selected_expiries:
        return pd.DataFrame()

    log_key = f"options:{name}"
    expiry_text = ", ".join(exp.strftime("%d-%m-%Y") for exp in selected_expiries)
    if _last_logged_expiry.get(log_key) != expiry_text:
        print(f"Selected options expiry for {name}: {expiry_text}")
        _last_logged_expiry[log_key] = expiry_text

    options = options[options["expiry"].isin(selected_expiries)]
    if options.empty:
        return pd.DataFrame()

    rng = strike_range if strike_range is not None else (15 if is_index_underlying(name) else 6)
    selected_frames = []

    for expiry, expiry_options in options.groupby("expiry"):
        strikes = sorted(expiry_options["strike"].unique())
        if not strikes:
            continue

        atm = min(strikes, key=lambda x: abs(x - ltp))
        idx = strikes.index(atm)
        selected = strikes[max(0, idx - rng): idx + rng + 1]
        selected_frames.append(
            expiry_options[expiry_options["strike"].isin(selected)].copy()
        )

    if not selected_frames:
        return pd.DataFrame()

    return pd.concat(selected_frames, ignore_index=True)


def get_strength_label(lots, name="BANKNIFTY"):
    if is_mcx_underlying(name):
        if lots >= 400:
            return "🚀 MCX BLAST 🚀"
        if lots >= 300:
            return "🌟 MCX AWESOME"
        if lots >= 200:
            return "✅ MCX VERY GOOD"
        return "⚡ MCX GOOD"

    if lots >= 400:
        return "🚀 BLAST 🚀"
    if lots >= 300:
        return "🌟 AWESOME"
    if lots >= 200:
        return "✅ VERY GOOD"
    return "⚡ GOOD"


def format_oi_delta(oi_delta):
    value = abs(oi_delta or 0)
    if value >= 10000000:
        return f"{value/10000000:.1f}Cr"
    if value >= 100000:
        return f"{value/100000:.1f}L"
    if value >= 1000:
        return f"{value/1000:.1f}K"
    return f"{value:.0f}"


def format_volume(value):
    value = float(value or 0)
    if value >= 1000000:
        return f"{value / 1000000:.2f}M"
    if value >= 1000:
        return f"{value / 1000:.1f}K"
    return f"{value:.0f}"


def classify_action(symbol, oi_change, price_change):
    if any(x in symbol for x in ["-FUT", "FUT", "-I"]):
        if oi_change > 0:
            return "FUTURE BUY (LONG) 📈" if price_change >= 0 else "FUTURE SELL (SHORT) 📉"
        return "SHORT COVERING ↗️" if price_change >= 0 else "LONG UNWINDING ↘️"

    is_call = symbol.endswith("CE")
    if oi_change > 0:
        if price_change >= 0:
            return "CALL BUY 🔵" if is_call else "PUT BUY 🔴"
        return "CALL WRITER ✍️" if is_call else "PUT WRITER ✍️"

    if price_change >= 0:
        return "SHORT COVERING (CE) ⤴️" if is_call else "SHORT COVERING (PE) ⤴️"
    return "LONG UNWINDING (CE) ⤵️" if is_call else "LONG UNWINDING (PE) ⤵️"


def _format_gap_signal(gap_pct):
    return "FUTURE ABOVE SPOT" if gap_pct > 0 else "FUTURE BELOW SPOT"


def build_monthly_future_gap_alerts(kite, batch_index=None, max_quote_symbols=None):
    now_ist = datetime.now(IST)
    if now_ist.weekday() > 4 or now_ist.time() < MONTHLY_FUTURE_GAP_START_TIME:
        return []

    future_contracts = _get_active_stock_future_contracts()
    future_contracts = [
        contract
        for contract in future_contracts
        if contract.get("name") not in INDEX_FUTURE_NAMES
    ]
    if not future_contracts:
        return []

    if batch_index == 0 or batch_index is None:
        print(f"Gap scanner: found {len(future_contracts)} future contracts for reporting.")

    symbol_pairs = [
        (
            contract["name"],
            get_spot_symbol(contract["name"]),
            contract["symbol"],
            contract["month_label"],
            contract["next_symbol"],
            contract["next_month_label"],
        )
        for contract in future_contracts
    ]

    if max_quote_symbols and max_quote_symbols > 0:
        batches = []
        current_batch = []
        current_symbol_count = 0
        for pair in symbol_pairs:
            next_symbol = pair[4]
            pair_symbol_count = 2 + (1 if next_symbol else 0)
            if current_batch and current_symbol_count + pair_symbol_count > max_quote_symbols:
                batches.append(current_batch)
                current_batch = []
                current_symbol_count = 0
            current_batch.append(pair)
            current_symbol_count += pair_symbol_count
        if current_batch:
            batches.append(current_batch)

        if batch_index is not None and batches:
            symbol_pairs = batches[batch_index % len(batches)]

    quote_symbols = []
    for _, spot_symbol, future_symbol, _, next_symbol, _ in symbol_pairs:
        quote_symbols.append(spot_symbol)
        quote_symbols.append(future_symbol)
        if next_symbol:
            quote_symbols.append(next_symbol)

    data = get_symbol_quotes_with_fallback(kite, quote_symbols)
    if not data:
        return []

    now = datetime.now(IST)
    rows = []
    match_rows = []
    for name, spot_symbol, future_symbol, month_label, next_symbol, next_month_label in symbol_pairs:
        spot_price = data.get(spot_symbol, {}).get("last_price", 0)
        future_price = data.get(future_symbol, {}).get("last_price", 0)
        if spot_price <= 0 or future_price <= 0:
            continue

        gap_pct = ((future_price - spot_price) / spot_price) * 100
        next_future_price = data.get(next_symbol, {}).get("last_price", 0) if next_symbol else 0
        next_gap_pct = None
        if next_future_price > 0:
            next_gap_pct = ((next_future_price - future_price) / future_price) * 100

        # Updated Gap Hedge Logic:
        # 1. Absolute gap between Spot and Future must be GREATER THAN OR EQUAL to 2.0%
        # 2. Absolute gap between the two Futures (Near vs Next) must be LESS THAN OR EQUAL to 0.5%
        if abs(gap_pct) < MONTHLY_FUTURE_GAP_THRESHOLD_PCT:
            continue

        if next_gap_pct is None or abs(next_gap_pct) > MONTHLY_FUTURE_NEXT_GAP_MAX_PCT:
            continue

        last_sent = gap_alert_store.get(future_symbol)
        if last_sent and (now - last_sent).total_seconds() < GAP_ALERT_COOLDOWN_SECONDS:
            continue

        gap_alert_store[future_symbol] = now
        item = {
            "name": name,
            "month_label": month_label,
            "spot_price": spot_price,
            "future_price": future_price,
            "gap_pct": gap_pct,
            "next_future_price": next_future_price,
            "next_gap_pct": next_gap_pct,
            "next_month_label": next_month_label,
        }
        rows.append(item)

        spot_match_ok = (
            (gap_pct >= 0 and gap_pct <= MONTHLY_FUTURE_MATCH_ABOVE_PCT)
            or (gap_pct < 0 and abs(gap_pct) <= MONTHLY_FUTURE_MATCH_BELOW_PCT)
        )
        next_move_ok = abs(next_gap_pct) >= MONTHLY_FUTURE_MATCH_NEXT_MIN_PCT
        if spot_match_ok and next_move_ok:
            match_rows.append(item)

    if not rows and not match_rows:
        return []

    rows.sort(key=lambda item: abs(item["gap_pct"]), reverse=True)
    match_rows.sort(key=lambda item: abs(item["gap_pct"]), reverse=True)
    alerts = []
    chunk_size = 20
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i:i + chunk_size]
        body_lines = []
        for item in chunk:
            if item["next_gap_pct"] is None:
                next_future_text = f"Next Fut NA | Next-vs-{item['month_label']} NA"
            else:
                next_future_text = (
                    f"{item['next_month_label']} Fut {item['next_future_price']:.2f} | "
                    f"{item['next_month_label']}-vs-{item['month_label']} {item['next_gap_pct']:+.2f}%"
                )
            body_lines.append(
                f"{item['name']}: Spot {item['spot_price']:.2f} | "
                f"{item['month_label']} Fut {item['future_price']:.2f} | "
                f"Spot Gap {item['gap_pct']:+.2f}% | "
                f"{next_future_text} | {_format_gap_signal(item['gap_pct'])}"
            )
        body = "\n".join(body_lines)
        report_month = chunk[0]["month_label"] if chunk else "MONTHLY"
        alerts.append(f"📊 {report_month} FUTURE GAP REPORT\n\n{body}")

    for i in range(0, len(match_rows), chunk_size):
        chunk = match_rows[i:i + chunk_size]
        body_lines = []
        for item in chunk:
            if item["next_gap_pct"] is None:
                next_future_text = f"Next Fut NA | Next-vs-{item['month_label']} NA"
            else:
                next_future_text = (
                    f"{item['next_month_label']} Fut {item['next_future_price']:.2f} | "
                    f"{item['next_month_label']}-vs-{item['month_label']} {item['next_gap_pct']:+.2f}%"
                )
            body_lines.append(
                f"{item['name']}: Spot {item['spot_price']:.2f} | "
                f"{item['month_label']} Fut {item['future_price']:.2f} | "
                f"Spot Match {item['gap_pct']:+.2f}% | "
                f"{next_future_text} | SPOT FUTURE MATCH"
            )
        body = "\n".join(body_lines)
        report_month = chunk[0]["month_label"] if chunk else "MONTHLY"
        alerts.append(f"📌 {report_month} SPOT FUTURE GAP MATCH ALERT\n\n{body}")
    return alerts


def _get_latest_completed_candle(candles, interval_minutes, now_ist):
    # Prefer the last *fully completed* candle. After market close, some larger
    # intervals (e.g. 60minute) may include a final partial candle; we ignore it
    # by anchoring completion to the session close (15:30 IST).
    session_close = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
    anchor = session_close if now_ist >= session_close else now_ist
    cutoff = anchor - timedelta(minutes=interval_minutes)
    completed = []
    for candle in candles:
        candle_time = candle.get("date")
        if candle_time is None:
            continue
        if candle_time.tzinfo is None:
            candle_time = candle_time.replace(tzinfo=IST)
        else:
            candle_time = candle_time.astimezone(IST)
        if candle_time <= cutoff:
            completed.append(candle)
    return completed[-1] if completed else None


def _calculate_classic_r3(candle):
    high = float(candle.get("high", 0) or 0)
    low = float(candle.get("low", 0) or 0)
    close = float(candle.get("close", 0) or 0)
    if high <= 0 or low <= 0 or close <= 0:
        return None

    pivot = (high + low + close) / 3
    return high + (2 * (pivot - low))


def _get_r3_for_interval(kite, token, interval, interval_minutes, now_ist):
    from_time = now_ist.replace(hour=9, minute=0, second=0, microsecond=0)
    to_time = now_ist
    try:
        candles = get_historical_data_cached(kite, token, from_time, to_time, interval)
    except Exception as e:
        print(f"R3 historical data error for {token} {interval}: {e}")
        return None

    candle = _get_latest_completed_candle(candles, interval_minutes, now_ist)
    if not candle:
        return None

    r3 = _calculate_classic_r3(candle)
    if not r3:
        return None

    return {
        "r3": r3,
        "candle_time": candle.get("date"),
        "high": candle.get("high", 0),
        "low": candle.get("low", 0),
        "close": candle.get("close", 0),
    }


def _get_previous_trading_day(now_ist):
    day = now_ist.date() - timedelta(days=1)
    while day.weekday() > 4:
        day -= timedelta(days=1)
    return day


def _is_scan_window_open(now_ist, start_time, end_time):
    return start_time <= now_ist.time() <= end_time


def _get_candle_day(candle):
    candle_time = candle.get("date")
    if candle_time is None:
        return None
    if hasattr(candle_time, "astimezone"):
        return candle_time.astimezone(IST).date()
    if hasattr(candle_time, "date"):
        return candle_time.date()
    return None


def _get_recent_daily_candles_until(kite, token, through_day, lookback_days, label):
    from_day = through_day - timedelta(days=lookback_days)
    from_time = datetime.combine(
        from_day,
        datetime.strptime("09:15", "%H:%M").time(),
        tzinfo=IST,
    )
    to_time = datetime.combine(
        through_day,
        datetime.strptime("15:30", "%H:%M").time(),
        tzinfo=IST,
    )
    try:
        return get_historical_data_cached(kite, token, from_time, to_time, "day")
    except Exception as e:
        print(f"{label} daily historical data error for {token}: {e}")
        return []


def _completed_daily_candles_through(candles, through_day):
    completed = []
    for candle in candles:
        candle_day = _get_candle_day(candle)
        if candle_day and candle_day <= through_day:
            completed.append(candle)
    return sorted(completed, key=lambda item: item.get("date"))


def _build_volume_mismatch_messages(title, rows, now_ist):
    if not rows:
        return []

    rows.sort(
        key=lambda item: (
            item.get("period_sort", ""),
            float(item.get("volume", 0) or 0),
            abs(float(item.get("change_pct", 0) or 0)),
        ),
        reverse=True,
    )

    alerts = []
    chunk_size = 20
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i:i + chunk_size]
        body_lines = []
        for item in chunk:
            open_extreme = _open_extreme_label(item["open"], item["high"], item["low"])
            open_extreme_text = f" | {open_extreme}" if open_extreme else ""
            body_lines.append(
                f"{item['name']} {item['month_label']} FUT: "
                f"{item['period_text']} | "
                f"Vol {format_volume(item['volume'])} | "
                f"Price {item['price_color']} vs Volume {item['reference_color']}"
                f"{open_extreme_text}"
            )

        alerts.append(
            f"{title}\n\n"
            f"{chr(10).join(body_lines)}\n\n"
            f"TIME: {now_ist.strftime('%H:%M:%S')} IST"
        )
    return alerts


def _volume_beats_previous(candles, index, lookback=5):
    if index < lookback:
        return False, 0

    volume = float(candles[index].get("volume", 0) or 0)
    previous_volumes = [
        float(candle.get("volume", 0) or 0)
        for candle in candles[index - lookback:index]
    ]
    if len(previous_volumes) < lookback or volume <= 0:
        return False, 0

    previous_max = max(previous_volumes)
    return volume > previous_max, previous_max


def _level_was_broken_after(candles, index, direction, high, low):
    for candle in candles[index + 1:]:
        candle_high = float(candle.get("high", 0) or 0)
        candle_low = float(candle.get("low", 0) or 0)
        if direction == "BREAKOUT" and candle_high > high:
            return True
        if direction == "BREAKDOWN" and candle_low < low:
            return True
    return False


def _build_volume_mismatch_break_messages(title, rows, now_ist):
    if not rows:
        return []

    rows.sort(
        key=lambda item: (
            item.get("period_sort", ""),
            float(item.get("volume", 0) or 0),
        ),
        reverse=True,
    )

    alerts = []
    chunk_size = 20
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i:i + chunk_size]
        body_lines = []
        for item in chunk:
            open_extreme = _open_extreme_label(item["open"], item["high"], item["low"])
            open_extreme_text = f" | {open_extreme}" if open_extreme else ""
            level_text = (
                f"Fut {item['ltp']:.2f} > High {item['high']:.2f}"
                if item["direction"] == "BREAKOUT"
                else f"Fut {item['ltp']:.2f} < Low {item['low']:.2f}"
            )
            body_lines.append(
                f"{item['name']} {item['month_label']} FUT: "
                f"Setup {item['period_text']} | "
                f"{level_text} | "
                f"Vol {format_volume(item['volume'])} > Prev5 Max {format_volume(item['previous_volume_max'])} | "
                f"Price {item['price_color']} vs Volume {item['reference_color']}"
                f"{open_extreme_text}"
            )

        alerts.append(
            f"{title}\n\n"
            f"{chr(10).join(body_lines)}\n\n"
            f"TIME: {now_ist.strftime('%H:%M:%S')} IST"
        )
    return alerts


def _build_daily_volume_mismatch_setup_rows(kite, now_ist):
    target_day = _get_previous_trading_day(now_ist)
    contracts = _get_active_stock_future_contracts()
    if not contracts:
        return []

    lookback_days = max(
        PREVIOUS_DAY_MISMATCH_LOOKBACK_DAYS,
        WEEKLY_MISMATCH_LOOKBACK_DAYS,
        10,
    )

    setup_rows = []
    for contract in contracts:
        candles = _get_recent_daily_candles_until(
            kite,
            contract["token"],
            target_day,
            lookback_days,
            "Daily mismatch breakout",
        )
        completed = _completed_daily_candles_through(candles, target_day)
        if len(completed) < 6:
            continue

        for index in range(5, len(completed)):
            previous_candle = completed[index - 1]
            candle = completed[index]
            candle_day = _get_candle_day(candle)
            if not candle_day:
                continue

            previous_close = float(previous_candle.get("close", 0) or 0)
            open_price = float(candle.get("open", 0) or 0)
            high = float(candle.get("high", 0) or 0)
            low = float(candle.get("low", 0) or 0)
            close = float(candle.get("close", 0) or 0)
            volume = float(candle.get("volume", 0) or 0)
            if previous_close <= 0 or open_price <= 0 or high <= 0 or low <= 0 or close <= 0:
                continue
            if volume <= DAILY_WEEKLY_MISMATCH_MIN_VOLUME:
                continue
            volume_ok, previous_volume_max = _volume_beats_previous(completed, index)
            if not volume_ok:
                continue

            price_color = _candle_color(open_price, close)
            reference_color = _volume_candle_color(previous_close, close)
            if not price_color or not reference_color or price_color == reference_color:
                continue

            base_row = {
                "name": contract["name"],
                "month_label": contract["month_label"],
                "period_text": candle_day.strftime("%d-%m-%Y"),
                "period_sort": candle_day.isoformat(),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "previous_volume_max": previous_volume_max,
                "price_color": price_color,
                "reference_color": reference_color,
                "symbol": contract["symbol"],
            }

            for direction in ("BREAKOUT", "BREAKDOWN"):
                if _level_was_broken_after(completed, index, direction, high, low):
                    continue
                row = dict(base_row)
                row["direction"] = direction
                setup_rows.append(row)

    return setup_rows


def build_previous_day_future_volume_mismatch_alerts(kite):
    global daily_mismatch_setup_date, daily_mismatch_setup_rows

    now_ist = datetime.now(IST)
    if now_ist.weekday() > 4:
        return []

    scan_date = now_ist.date().isoformat()
    if daily_mismatch_setup_date != scan_date:
        daily_mismatch_setup_rows = _build_daily_volume_mismatch_setup_rows(kite, now_ist)
        daily_mismatch_setup_date = scan_date
        print(f"Daily volume mismatch setup cached: {len(daily_mismatch_setup_rows)} rows")

    if not daily_mismatch_setup_rows:
        return []

    symbols = sorted({row["symbol"] for row in daily_mismatch_setup_rows})
    quote_data = get_symbol_quotes_with_fallback(kite, symbols)
    if not quote_data:
        return []

    breakout_rows = []
    breakdown_rows = []
    for setup in daily_mismatch_setup_rows:
        ltp = quote_data.get(setup["symbol"], {}).get("last_price", 0)
        if ltp <= 0:
            continue

        direction = setup["direction"]
        if direction == "BREAKOUT" and ltp <= setup["high"]:
            continue
        if direction == "BREAKDOWN" and ltp >= setup["low"]:
            continue

        alert_key = (
            f"DAILY_VM_BREAK:{setup['symbol']}:"
            f"{setup['period_sort']}:{direction}:{scan_date}"
        )
        if alert_key in daily_mismatch_break_alert_store:
            continue

        daily_mismatch_break_alert_store[alert_key] = now_ist
        row = dict(setup)
        row["ltp"] = ltp
        if direction == "BREAKOUT":
            breakout_rows.append(row)
        else:
            breakdown_rows.append(row)

    alerts = []
    alerts.extend(
        _build_volume_mismatch_break_messages(
            "DAILY FUTURE VOLUME MISMATCH BREAKOUT",
            breakout_rows,
            now_ist,
        )
    )
    alerts.extend(
        _build_volume_mismatch_break_messages(
            "DAILY FUTURE VOLUME MISMATCH BREAKDOWN",
            breakdown_rows,
            now_ist,
        )
    )
    return alerts


def _build_weekly_volume_mismatch_setup_rows(kite, now_ist):
    current_week_start = now_ist.date() - timedelta(days=now_ist.weekday())
    previous_week_end = current_week_start - timedelta(days=3)
    contracts = _get_active_stock_future_contracts()
    if not contracts:
        return []

    setup_rows = []
    for contract in contracts:
        candles = _get_recent_daily_candles_until(
            kite,
            contract["token"],
            previous_week_end,
            WEEKLY_MISMATCH_LOOKBACK_DAYS,
            "Weekly mismatch breakout",
        )
        completed = _completed_daily_candles_through(candles, previous_week_end)
        if len(completed) < 10:
            continue

        weekly = _build_weekly_candles_from_daily(completed)
        if len(weekly) < 6:
            continue

        for index in range(5, len(weekly)):
            previous_week = weekly[index - 1]
            week = weekly[index]
            reference_close = float(previous_week.get("close", 0) or 0)
            open_price = float(week.get("open", 0) or 0)
            high = float(week.get("high", 0) or 0)
            low = float(week.get("low", 0) or 0)
            close = float(week.get("close", 0) or 0)
            volume = float(week.get("volume", 0) or 0)
            if reference_close <= 0 or open_price <= 0 or high <= 0 or low <= 0 or close <= 0:
                continue
            if volume <= DAILY_WEEKLY_MISMATCH_MIN_VOLUME:
                continue
            volume_ok, previous_volume_max = _volume_beats_previous(weekly, index)
            if not volume_ok:
                continue

            price_color = _candle_color(open_price, close)
            reference_color = _volume_candle_color(reference_close, close)
            if not price_color or not reference_color or price_color == reference_color:
                continue

            week_start = week.get("week_start")
            week_end = week.get("last_date")
            period_text = f"{week_start.strftime('%d-%m-%Y')} to {week_end.strftime('%d-%m-%Y')}"
            base_row = {
                "name": contract["name"],
                "month_label": contract["month_label"],
                "period_text": period_text,
                "period_sort": week_start.isoformat(),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "previous_volume_max": previous_volume_max,
                "price_color": price_color,
                "reference_color": reference_color,
                "symbol": contract["symbol"],
            }

            for direction in ("BREAKOUT", "BREAKDOWN"):
                if _level_was_broken_after(weekly, index, direction, high, low):
                    continue
                row = dict(base_row)
                row["direction"] = direction
                setup_rows.append(row)

    return setup_rows


def build_weekly_future_volume_mismatch_alerts(kite):
    global weekly_mismatch_setup_date, weekly_mismatch_setup_rows

    now_ist = datetime.now(IST)
    if now_ist.weekday() > 4:
        return []

    scan_date = now_ist.date().isoformat()
    current_week_start = now_ist.date() - timedelta(days=now_ist.weekday())
    if weekly_mismatch_setup_date != scan_date:
        weekly_mismatch_setup_rows = _build_weekly_volume_mismatch_setup_rows(kite, now_ist)
        weekly_mismatch_setup_date = scan_date
        print(f"Weekly volume mismatch setup cached: {len(weekly_mismatch_setup_rows)} rows")

    if not weekly_mismatch_setup_rows:
        return []

    symbols = sorted({row["symbol"] for row in weekly_mismatch_setup_rows})
    quote_data = get_symbol_quotes_with_fallback(kite, symbols)
    if not quote_data:
        return []

    breakout_rows = []
    breakdown_rows = []
    for setup in weekly_mismatch_setup_rows:
        ltp = quote_data.get(setup["symbol"], {}).get("last_price", 0)
        if ltp <= 0:
            continue

        direction = setup["direction"]
        if direction == "BREAKOUT" and ltp <= setup["high"]:
            continue
        if direction == "BREAKDOWN" and ltp >= setup["low"]:
            continue

        alert_key = (
            f"WEEKLY_VM_BREAK:{setup['symbol']}:"
            f"{setup['period_sort']}:{direction}:{current_week_start.isoformat()}"
        )
        if alert_key in weekly_mismatch_break_alert_store:
            continue

        weekly_mismatch_break_alert_store[alert_key] = now_ist
        row = dict(setup)
        row["ltp"] = ltp
        if direction == "BREAKOUT":
            breakout_rows.append(row)
        else:
            breakdown_rows.append(row)

    alerts = []
    alerts.extend(
        _build_volume_mismatch_break_messages(
            "WEEKLY FUTURE VOLUME MISMATCH BREAKOUT",
            breakout_rows,
            now_ist,
        )
    )
    alerts.extend(
        _build_volume_mismatch_break_messages(
            "WEEKLY FUTURE VOLUME MISMATCH BREAKDOWN",
            breakdown_rows,
            now_ist,
        )
    )
    return alerts


def _get_previous_day_r3_for_interval(kite, token, interval, interval_minutes, now_ist):
    prev_day = _get_previous_trading_day(now_ist)
    from_time = datetime.combine(prev_day, datetime.strptime("09:15", "%H:%M").time(), tzinfo=IST)
    to_time = datetime.combine(prev_day, datetime.strptime("15:30", "%H:%M").time(), tzinfo=IST)
    try:
        candles = get_historical_data_cached(kite, token, from_time, to_time, interval)
    except Exception as e:
        print(f"Previous day R3 historical data error for {token} {interval}: {e}")
        return None

    if not candles:
        return None

    # Use the last fully completed candle for the previous trading day.
    # Kite can return a final partial candle for larger intervals (e.g. 60minute),
    # which makes 15MIN/1HR pivots incorrectly identical.
    prev_close_time = datetime.combine(prev_day, datetime.strptime("15:30", "%H:%M").time(), tzinfo=IST)
    candle = _get_latest_completed_candle(candles, interval_minutes, prev_close_time)
    if not candle:
        candle = candles[-1]
    r3 = _calculate_classic_r3(candle)
    if not r3:
        return None

    prev_close = float(candle.get("close", 0) or 0)
    close_diff_pct = ((prev_close - r3) / r3) * 100
    if abs(close_diff_pct) > R3_PIVOT_RANGE_PCT:
        return None

    return {
        "r3": r3,
        "prev_close": prev_close,
        "close_diff_pct": close_diff_pct,
        "candle_time": candle.get("date"),
    }


def build_monthly_future_r3_pivot_alerts(kite):
    return []
    global r3_last_check_time, r3_watch_last_sent_time

    now_ist = datetime.now(IST)
    if now_ist.weekday() > 4 or now_ist.time() < R3_PIVOT_ALERT_START_TIME:
        return []

    if (
        r3_last_check_time
        and (now_ist - r3_last_check_time).total_seconds() < R3_PIVOT_CHECK_INTERVAL_SECONDS
    ):
        return []
    r3_last_check_time = now_ist

    contracts = _get_active_stock_future_contracts()
    if not contracts:
        return []

    symbols = [contract["symbol"] for contract in contracts]
    data = get_symbol_quotes_with_fallback(kite, symbols)
    if not data:
        return []

    watch_rows = []
    near_rows = []
    breakout_rows = []
    # Zerodha chart Pivot Points (standard) on intraday charts are anchored to the
    # previous trading session, but the H/L/C can differ slightly per timeframe
    # because they are derived from that timeframe's candles.
    intervals = [
        ("1HR", "60minute", 60),
    ]

    def _prev_week_window(now_ist):
        # Previous calendar week (Mon-Fri) in IST.
        # For 30m/1h charts, Kite pivots are anchored to previous week.
        current_week_start = now_ist.date() - timedelta(days=now_ist.weekday())
        prev_week_start = current_week_start - timedelta(days=7)
        prev_week_end = prev_week_start + timedelta(days=4)
        start = datetime.combine(prev_week_start, datetime.strptime("09:15", "%H:%M").time(), tzinfo=IST)
        end = datetime.combine(prev_week_end, datetime.strptime("15:30", "%H:%M").time(), tzinfo=IST)
        return start, end

    for contract in contracts:
        symbol = contract["symbol"]
        ltp = data.get(symbol, {}).get("last_price", 0)
        if ltp <= 0:
            continue

        matched = []
        for label, kite_interval, interval_minutes in intervals:
            # Kite pivot anchor:
            # - <=15m charts: previous trading day
            # - >=30m charts (including 1H): previous week
            if interval_minutes >= 30:
                from_time, to_time = _prev_week_window(now_ist)
            else:
                prev_day = _get_previous_trading_day(now_ist)
                from_time = datetime.combine(prev_day, datetime.strptime("09:15", "%H:%M").time(), tzinfo=IST)
                to_time = datetime.combine(prev_day, datetime.strptime("15:30", "%H:%M").time(), tzinfo=IST)
            try:
                candles = get_historical_data_cached(
                    kite,
                    contract["token"],
                    from_time,
                    to_time,
                    kite_interval,
                )
            except Exception as e:
                print(f"Previous session candle fetch error for {contract['token']} {kite_interval}: {e}")
                continue
            if not candles:
                continue

            # H/L/C derived from the anchor window candles for this interval.
            high = max(float(c.get("high", 0) or 0) for c in candles)
            low = min(float(c.get("low", 0) or 0) for c in candles)
            prev_close = float(candles[-1].get("close", 0) or 0)
            if high <= 0 or low <= 0 or prev_close <= 0:
                continue

            # Calculate Classic Floor Pivot R3: R3 = High + 2 * (Pivot - Low)
            # This matches most standard charting platforms and is more reachable than PP + 2*(H-L).
            pivot = (high + low + prev_close) / 3
            r3 = high + (2 * (pivot - low))
            
            close_diff_pct = ((prev_close - r3) / r3) * 100 if r3 else 0
            diff_pct = ((ltp - r3) / r3) * 100
            matched.append(
                {
                    "label": label,
                    "r3": r3,
                    "diff_pct": diff_pct,
                    "prev_close": prev_close,
                    "close_diff_pct": close_diff_pct,
                }
            )

            if abs(diff_pct) <= R3_PIVOT_RANGE_PCT and ltp <= r3:
                alert_key = f"R3_NEAR:{symbol}:{label}:{now_ist.date().isoformat()}"
                if alert_key not in r3_alert_store:
                    r3_alert_store[alert_key] = now_ist
                    near_rows.append(
                        {
                            "name": contract["name"],
                            "month_label": contract["month_label"],
                            "symbol": symbol,
                            "ltp": ltp,
                            "label": label,
                            "r3": r3,
                            "diff_pct": diff_pct,
                            "prev_close": prev_close,
                            "close_diff_pct": close_diff_pct,
                        }
                    )

            if ltp > r3:
                alert_key = f"R3_ABOVE:{symbol}:{label}:{now_ist.date().isoformat()}"
                if alert_key not in r3_alert_store:
                    r3_alert_store[alert_key] = now_ist
                    breakout_rows.append(
                        {
                            "name": contract["name"],
                            "month_label": contract["month_label"],
                            "symbol": symbol,
                            "ltp": ltp,
                            "label": label,
                            "r3": r3,
                            "diff_pct": diff_pct,
                            "prev_close": prev_close,
                            "close_diff_pct": close_diff_pct,
                        }
                    )

        if matched:
            watch_rows.append(
                {
                    "name": contract["name"],
                    "month_label": contract["month_label"],
                    "symbol": symbol,
                    "ltp": ltp,
                    "matches": matched,
                }
            )

    if not watch_rows and not near_rows and not breakout_rows:
        return []

    alerts = []

    if near_rows:
        body_lines = [
            f"{item['name']} {item['month_label']} FUT: Fut {item['ltp']:.2f} | {item['label']} R3 {item['r3']:.2f} "
            f"| Near {item['diff_pct']:+.2f}% | Prev Close {item['prev_close']:.2f} "
            f"({item['close_diff_pct']:+.2f}%)"
            for item in near_rows
        ]
        for i in range(0, len(body_lines), 20):
            chunk = "\n".join(body_lines[i:i + 20])
            report_month = near_rows[i]["month_label"]
            alerts.append(f"{report_month} FUTURE R3 NEAR ALERT\n\n{chunk}\n\nTIME: {now_ist.strftime('%H:%M:%S')} IST")

    if breakout_rows:
        body_lines = [
            f"{item['name']} {item['month_label']} FUT: Fut {item['ltp']:.2f} | {item['label']} R3 {item['r3']:.2f} "
            f"| Above {item['diff_pct']:+.2f}% | Prev Close {item['prev_close']:.2f} "
            f"({item['close_diff_pct']:+.2f}%)"
            for item in breakout_rows
        ]
        for i in range(0, len(body_lines), 20):
            chunk = "\n".join(body_lines[i:i + 20])
            report_month = breakout_rows[i]["month_label"]
            alerts.append(f"{report_month} FUTURE R3 BREAKOUT ALERT\n\n{chunk}\n\nTIME: {now_ist.strftime('%H:%M:%S')} IST")

    reminder_seconds = (
        R3_PIVOT_CLOSE_REMINDER_SECONDS
        if now_ist.time() >= R3_PIVOT_CLOSE_REMINDER_START_TIME
        else R3_PIVOT_REMINDER_SECONDS
    )
    reminder_due = (
        SEND_R3_WATCHLIST
        and watch_rows
        and (
            r3_watch_last_sent_time is None
            or (now_ist - r3_watch_last_sent_time).total_seconds() >= reminder_seconds
        )
    )

    if reminder_due:
        r3_watch_last_sent_time = now_ist
        body_lines = []
        for item in watch_rows:
            pivot_text = ", ".join(
                f"{match['label']} R3 {match['r3']:.2f} | Fut {match['diff_pct']:+.2f}% vs R3 "
                f"| Prev Close {match['prev_close']:.2f} ({match['close_diff_pct']:+.2f}%)"
                for match in item["matches"]
            )
            body_lines.append(f"{item['name']} {item['month_label']} FUT: Fut {item['ltp']:.2f} | {pivot_text}")

        for i in range(0, len(body_lines), 20):
            chunk = "\n".join(body_lines[i:i + 20])
            report_month = watch_rows[i]["month_label"]
            alerts.append(f"{report_month} FUTURE R3 WATCHLIST\n\n{chunk}\n\nTIME: {now_ist.strftime('%H:%M:%S')} IST")

    return alerts


def build_stock_future_1hr_s4_alerts(kite):
    return []
    global s4_last_slot

    now_ist = datetime.now(IST)
    if now_ist.weekday() > 4:
        return []

    due_slot = get_due_s4_slot(now_ist)
    if not due_slot:
        return []

    if s4_last_slot == due_slot:
        return []
    s4_last_slot = due_slot

    contracts = _get_active_stock_future_contracts()
    if not contracts:
        return []

    symbols = [contract["symbol"] for contract in contracts]
    data = get_symbol_quotes_with_fallback(kite, symbols)
    if not data:
        return []

    current_week_start = now_ist.date() - timedelta(days=now_ist.weekday())
    prev_week_start = current_week_start - timedelta(days=7)
    prev_week_end = prev_week_start + timedelta(days=4)
    from_time = datetime.combine(prev_week_start, datetime.strptime("09:15", "%H:%M").time(), tzinfo=IST)
    to_time = datetime.combine(prev_week_end, datetime.strptime("15:30", "%H:%M").time(), tzinfo=IST)

    below_rows = []
    ready_breakdown_rows = []
    ready_breakup_rows = []
    breakup_rows = []

    for contract in contracts:
        symbol = contract["symbol"]
        ltp = data.get(symbol, {}).get("last_price", 0)
        if ltp <= 0:
            continue

        try:
            candles = get_historical_data_cached(
                kite,
                contract["token"],
                from_time,
                to_time,
                "60minute",
            )
        except Exception as e:
            print(f"S4 previous week candle fetch error for {contract['token']}: {e}")
            continue
        if not candles:
            continue

        high = max(float(c.get("high", 0) or 0) for c in candles)
        low = min(float(c.get("low", 0) or 0) for c in candles)
        prev_close = float(candles[-1].get("close", 0) or 0)
        if high <= 0 or low <= 0 or prev_close <= 0:
            continue

        pivot = (high + low + prev_close) / 3
        s4 = pivot - (3 * (high - low))
        if s4 <= 0:
            continue

        diff_pct = ((ltp - s4) / s4) * 100
        current_side = "below" if ltp < s4 else "above"
        state_key = f"S4_STATE:{symbol}:{now_ist.date().isoformat()}"
        prev_side = s4_state_store.get(state_key)

        row = {
            "name": contract["name"],
            "month_label": contract["month_label"],
            "symbol": symbol,
            "ltp": ltp,
            "s4": s4,
            "diff_pct": diff_pct,
            "prev_close": prev_close,
        }

        if prev_side == "below" and ltp > s4:
            alert_key = f"S4_BREAKUP:{symbol}:{now_ist.date().isoformat()}"
            if alert_key not in s4_alert_store:
                s4_alert_store[alert_key] = now_ist
                breakup_rows.append(row)
        elif ltp < s4:
            if abs(diff_pct) <= S4_PIVOT_RANGE_PCT:
                alert_key = f"S4_READY_BREAKUP:{symbol}:{now_ist.date().isoformat()}"
                if alert_key not in s4_alert_store:
                    s4_alert_store[alert_key] = now_ist
                    ready_breakup_rows.append(row)
            else:
                alert_key = f"S4_BELOW:{symbol}:{now_ist.date().isoformat()}"
                if alert_key not in s4_alert_store:
                    s4_alert_store[alert_key] = now_ist
                    below_rows.append(row)
        elif 0 <= diff_pct <= S4_PIVOT_RANGE_PCT:
            alert_key = f"S4_READY_BREAKDOWN:{symbol}:{now_ist.date().isoformat()}"
            if alert_key not in s4_alert_store:
                s4_alert_store[alert_key] = now_ist
                ready_breakdown_rows.append(row)

        s4_state_store[state_key] = current_side

    def _format_rows(rows, side_text):
        return [
            f"{item['name']} {item['month_label']} FUT: Fut {item['ltp']:.2f} | "
            f"1HR S4 {item['s4']:.2f} | {side_text} {item['diff_pct']:+.2f}% | "
            f"Prev Close {item['prev_close']:.2f}"
            for item in rows
        ]

    alerts = []
    alert_groups = [
        ("STOCK FUTURE 1HR S4 BELOW ALERT", below_rows, "Below"),
        ("STOCK FUTURE 1HR S4 READY BREAKDOWN", ready_breakdown_rows, "Above"),
        ("STOCK FUTURE 1HR S4 READY BREAKUP", ready_breakup_rows, "Below"),
        ("STOCK FUTURE 1HR S4 BREAKUP ABOVE", breakup_rows, "Above"),
    ]
    for title, rows, side_text in alert_groups:
        if not rows:
            continue
        body_lines = _format_rows(rows, side_text)
        for i in range(0, len(body_lines), 20):
            chunk = "\n".join(body_lines[i:i + 20])
            alerts.append(f"{title}\n\n{chunk}\n\nTIME: {now_ist.strftime('%H:%M:%S')} IST")

    return alerts


def _build_weekly_candles_from_daily(candles):
    weekly = []
    current_key = None
    current = None

    for candle in sorted(candles, key=lambda item: item.get("date")):
        candle_time = candle.get("date")
        if candle_time is None:
            continue
        if candle_time.tzinfo is None:
            candle_time = candle_time.replace(tzinfo=IST)
        else:
            candle_time = candle_time.astimezone(IST)

        week_start = candle_time.date() - timedelta(days=candle_time.weekday())
        open_price = float(candle.get("open", 0) or 0)
        high = float(candle.get("high", 0) or 0)
        low = float(candle.get("low", 0) or 0)
        close = float(candle.get("close", 0) or 0)
        volume = float(candle.get("volume", 0) or 0)
        if open_price <= 0 or high <= 0 or low <= 0 or close <= 0:
            continue

        if current_key != week_start:
            if current:
                weekly.append(current)
            current_key = week_start
            current = {
                "week_start": week_start,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "first_date": candle_time.date(),
                "last_date": candle_time.date(),
            }
            continue

        current["high"] = max(current["high"], high)
        current["low"] = min(current["low"], low)
        current["close"] = close
        current["volume"] += volume
        current["last_date"] = candle_time.date()

    if current:
        weekly.append(current)

    return weekly


def _get_born_breakout_contracts():
    contracts = []

    for contract in _get_active_stock_future_contracts():
        contracts.append(
            {
                "name": contract["name"],
                "symbol": contract["symbol"],
                "token": contract["token"],
                "expiry": contract["expiry"],
                "month_label": contract["month_label"],
                "series_label": "CURRENT",
            }
        )

        if (
            contract.get("next_symbol")
            and contract.get("next_token")
            and pd.notna(contract.get("next_expiry"))
        ):
            contracts.append(
                {
                    "name": contract["name"],
                    "symbol": contract["next_symbol"],
                    "token": contract["next_token"],
                    "expiry": contract["next_expiry"],
                    "month_label": contract["next_month_label"],
                    "series_label": "NEXT",
                }
            )

    return contracts


def build_weekly_born_breakout_alerts(kite):
    global born_breakout_last_check_time

    now_ist = datetime.now(IST)
    if now_ist.weekday() > 4 or not in_born_breakout_window(now_ist):
        return []

    if (
        born_breakout_last_check_time
        and (now_ist - born_breakout_last_check_time).total_seconds()
        < BORN_BREAKOUT_CHECK_INTERVAL_SECONDS
    ):
        return []
    born_breakout_last_check_time = now_ist

    contracts = _get_born_breakout_contracts()
    if not contracts:
        return []

    symbols = [contract["symbol"] for contract in contracts]
    quote_data = get_symbol_quotes_with_fallback(kite, symbols)
    alerts = []

    for contract in contracts:
        symbol = contract["symbol"]
        ltp = quote_data.get(symbol, {}).get("last_price", 0)
        if ltp <= 0:
            continue

        expiry = contract["expiry"]
        from_date = expiry.date() - timedelta(days=BORN_BREAKOUT_LOOKBACK_DAYS)
        from_time = datetime.combine(from_date, datetime.strptime("09:15", "%H:%M").time(), tzinfo=IST)

        try:
            candles = get_historical_data_cached(
                kite,
                contract["token"],
                from_time,
                now_ist,
                "day",
            )
        except Exception as e:
            print(f"Born breakout historical data error for {contract['token']}: {e}")
            continue

        weekly = _build_weekly_candles_from_daily(candles)
        if len(weekly) < 2:
            continue

        born = weekly[0]
        current = weekly[-1]
        born_high = float(born["high"])
        if born_high <= 0:
            continue

        already_crossed = any(
            float(item["high"]) > born_high
            for item in weekly[1:-1]
        )
        if already_crossed:
            continue

        break_price = max(float(current["high"]), float(ltp))
        if break_price <= born_high:
            continue

        alert_key = (
            f"BORN_WEEKLY:{contract['symbol']}:"
            f"{born['week_start'].isoformat()}"
        )
        if alert_key in born_breakout_alert_store:
            continue

        born_breakout_alert_store[alert_key] = now_ist
        break_pct = ((break_price - born_high) / born_high) * 100
        alerts.append(
            f"🚨 WEEKLY BORN BREAKOUT\n\n"
            f"Symbol: {symbol}\n"
            f"Contract: {contract['series_label']} {contract['month_label']} FUT\n"
            f"Born Week: {born['week_start'].strftime('%d-%m-%Y')}\n"
            f"Born High: {born_high:.2f}\n"
            f"{contract['month_label']} Fut: {ltp:.2f}\n"
            f"Break Above: {break_price:.2f} ({break_pct:+.2f}%)\n"
            f"Expiry: {expiry.strftime('%d-%m-%Y')}\n"
            f"TIME: {now_ist.strftime('%H:%M:%S')} IST"
        )

    return alerts


def process_future_burst(symbol, name, ltp, oi, alerts_list, stats=None):
    if not is_burst_underlying(name):
        return

    threshold = get_future_burst_threshold(name)
    lot_size = get_future_lot_size(symbol)
    if not lot_size:
        _log_missing_lot_size_once(f"future:{symbol}", symbol)
        return

    now = datetime.now(IST)
    key = f"FUT_{symbol}"
    if key not in option_history:
        option_history[key] = []
    history = option_history[key]
    prev_oi = history[-1]["oi"] if history else 0
    prev_price = history[-1]["price"] if history else 0

    if stats is not None:
        stats["future_quotes"] = stats.get("future_quotes", 0) + 1
        if oi > 0:
            stats["future_oi_quotes"] = stats.get("future_oi_quotes", 0) + 1

    if prev_oi > 0:
        tick_lots = int(abs(oi - prev_oi) / lot_size)
        if stats is not None:
            stats["max_future_tick_lots"] = max(
                stats.get("max_future_tick_lots", 0),
                tick_lots,
            )
        if tick_lots >= threshold and key not in active_watches:
            active_watches[key] = {
                "start_oi": prev_oi,
                "start_price": prev_price,
                "end_time": now + timedelta(seconds=15),
                "symbol": symbol,
                "name": name,
                "lot_size": lot_size,
                "expiry_text": get_future_expiry_text(symbol) if is_mcx_underlying(name) else "",
            }

    if key in active_watches:
        watch = active_watches[key]
        if now >= watch["end_time"]:
            oi_chg = oi - watch["start_oi"]
            p_chg = ltp - watch["start_price"]
            final_lot_size = _normalize_lot_size(watch.get("lot_size")) or lot_size
            final_lots = int(abs(oi_chg) / final_lot_size)
            if final_lots >= threshold:
                strength = get_strength_label(final_lots, watch["name"])
                action = classify_action(watch["symbol"], oi_chg, p_chg)
                p_icon = "▲" if p_chg >= 0 else "▼"
                expiry_line = (
                    f"EXPIRY: {watch['expiry_text']}\n"
                    if watch.get("expiry_text")
                    else ""
                )
                alert_text = (
                    f"{strength}\n🚨 {action}\nSymbol: {watch['symbol']}\n"
                    f"{expiry_line}"
                    f"━━━━━━━━━━━━━━━\n"
                    f"LOTS: {final_lots}\nPRICE: {ltp:.2f} ({p_icon})\nFUTURE PRICE: {ltp:.2f}\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"EXISTING OI: {watch['start_oi']:,}\nOI CHANGE  : {oi_chg:+,d}\nNEW OI     : {oi:,}\n"
                    f"TIME: {now.strftime('%H:%M:%S')}"
                )
                alert_key = f"FUT:{name}:{watch['symbol']}:{watch['start_oi']}:{watch['start_price']}"
                if not _burst_alert_recent(alert_key):
                    alerts_list.append(alert_text)
            del active_watches[key]

    history.append({"time": now, "oi": oi, "price": ltp})
    if len(history) > 20:
        history.pop(0)


def process_option_logic(name, underlying_data, option_quotes, alerts_list, stats=None):
    if not is_burst_underlying(name):
        return

    opt_df, u_ltp = underlying_data
    if opt_df.empty:
        return

    threshold = get_option_burst_threshold(name)
    now = datetime.now(IST)

    for _, row in opt_df.iterrows():
        t_str = str(int(row["instrument_token"]))
        if t_str not in option_quotes:
            continue

        lot_size = _get_row_lot_size(row)
        if not lot_size:
            _log_missing_lot_size_once(
                f"option:{t_str}",
                row.get("tradingsymbol", t_str),
            )
            continue
        
        q = option_quotes[t_str]
        curr_oi = q.get("oi", 0)
        ltp = q.get("last_price", 0)
        t_int = int(row["instrument_token"])

        if stats is not None:
            stats["option_quotes"] = stats.get("option_quotes", 0) + 1
            if curr_oi > 0:
                stats["option_oi_quotes"] = stats.get("option_oi_quotes", 0) + 1

        if t_int not in day_open_oi_store:
            day_open_oi_store[t_int] = curr_oi

        if t_int not in option_history:
            option_history[t_int] = []
        history = option_history[t_int]
        prev_oi = history[-1]["oi"] if history else 0
        prev_price = history[-1]["price"] if history else 0

        if prev_oi > 0:
            tick_lots = int(abs(curr_oi - prev_oi) / lot_size)
            if stats is not None:
                stats["max_option_tick_lots"] = max(
                    stats.get("max_option_tick_lots", 0),
                    tick_lots,
                )
            if tick_lots >= threshold and t_int not in active_watches:
                expiry_text = (
                    row["expiry"].strftime("%d-%m-%Y")
                    if pd.notna(row.get("expiry"))
                    else "NA"
                )
                active_watches[t_int] = {
                    "start_oi": prev_oi,
                    "start_price": prev_price,
                    "end_time": now + timedelta(seconds=15),
                    "symbol": row["tradingsymbol"],
                    "underlying": name,
                    "lot_size": lot_size,
                    "expiry_text": expiry_text,
                }

        if t_int in active_watches:
            watch = active_watches[t_int]
            if now >= watch["end_time"]:
                oi_chg = curr_oi - watch["start_oi"]
                p_chg = ltp - watch["start_price"]
                final_lot_size = _normalize_lot_size(watch.get("lot_size")) or lot_size
                final_lots = int(abs(oi_chg) / final_lot_size)
                if final_lots >= threshold:
                    strength = get_strength_label(final_lots, watch["underlying"])
                    action = classify_action(watch["symbol"], oi_chg, p_chg)
                    p_icon = "▲" if p_chg >= 0 else "▼"
                    alert_text = (
                        f"{strength}\n🚨 {action}\nSymbol: {watch['symbol']}\n"
                        f"EXPIRY: {watch.get('expiry_text', 'NA')}\n"
                        f"━━━━━━━━━━━━━━━\n"
                        f"LOTS: {final_lots}\nPRICE: {ltp:.2f} ({p_icon})\nFUTURE PRICE: {u_ltp:.2f}\n"
                        f"━━━━━━━━━━━━━━━\n"
                        f"EXISTING OI: {watch['start_oi']:,}\nOI CHANGE  : {oi_chg:+,d}\nNEW OI     : {curr_oi:,}\n"
                        f"TIME: {now.strftime('%H:%M:%S')}"
                    )
                    alert_key = f"OPT:{name}:{t_int}:{watch['start_oi']}:{watch['start_price']}"
                    if not _burst_alert_recent(alert_key):
                        alerts_list.append(alert_text)
                del active_watches[t_int]

        history.append({"time": now, "oi": curr_oi, "price": ltp})
        if len(history) > 20:
            history.pop(0)


def _map_tracked_futures_by_name(fut_symbols, names=None):
    names = set(names or BURST_TRACK_NAMES)
    fut_by_name = {}
    futures = load_futures_data()
    for sym in fut_symbols:
        try:
            tsym = sym.split(":", 1)[1]
        except Exception:
            continue
        if futures is None or futures.empty:
            continue
        rows = futures[futures["tradingsymbol"] == tsym]
        if rows.empty:
            continue
        name = str(rows.iloc[0].get("name", "") or "")
        if name in names:
            fut_by_name[name] = sym
    return fut_by_name


def _reset_burst_state_if_session_changed(session):
    global _last_burst_session
    if _last_burst_session == session:
        return

    option_history.clear()
    active_watches.clear()
    day_open_oi_store.clear()
    burst_alert_store.clear()
    _last_burst_session = session
    print(f"Burst state reset for {session.upper()} session.")


def _burst_alert_recent(alert_key, cooldown_seconds=120):
    now = time.time()
    last_sent = burst_alert_store.get(alert_key)
    if last_sent and now - last_sent < cooldown_seconds:
        return True

    burst_alert_store[alert_key] = now
    if len(burst_alert_store) > 2000:
        stale_cutoff = now - max(cooldown_seconds, 300)
        for key, ts in list(burst_alert_store.items()):
            if ts < stale_cutoff:
                burst_alert_store.pop(key, None)

    return False


def calculate_burst_alerts(kite):
    session = get_burst_session()
    track_names = get_active_burst_names()
    if not track_names:
        _set_burst_quote_status("inactive", "burst session closed")
        return [], []

    _reset_burst_state_if_session_changed(session)

    fut_symbols = get_burst_futures(kite, track_names)
    symbols = list(fut_symbols)
    future_threshold = max(get_future_burst_threshold(name) for name in track_names)
    option_threshold = max(get_option_burst_threshold(name) for name in track_names)
    if session == "nse":
        for name in track_names:
            if is_index_underlying(name):
                symbols.append(get_spot_symbol(name))
    fut_by_name = _map_tracked_futures_by_name(fut_symbols, track_names)

    quote_source = "websocket"
    data = get_symbol_quotes_ws_only(symbols, max_age_seconds=15)
    missing_futures = [symbol for symbol in fut_symbols if symbol not in data]
    if not data or missing_futures:
        data = _get_burst_symbol_quotes_with_fallback(kite, symbols)
        quote_source = "rest_fallback"

    if not data:
        _set_burst_quote_status("none", "no future quotes")
        _set_burst_monitor_status({
            "session": session,
            "names": ",".join(track_names),
            "source": "none",
            "reason": "no future quotes",
            "threshold": f"future={future_threshold}, option={option_threshold}",
            "future_threshold": future_threshold,
            "option_threshold": option_threshold,
        })
        return [], []

    bn_alerts = []
    stock_alerts = []
    stats = {
        "session": session,
        "names": ",".join(track_names),
        "source": quote_source,
        "threshold": f"future={future_threshold}, option={option_threshold}",
        "future_threshold": future_threshold,
        "option_threshold": option_threshold,
        "future_symbols": len(fut_symbols),
        "future_quotes": 0,
        "future_oi_quotes": 0,
        "option_tokens": 0,
        "option_quotes": 0,
        "option_oi_quotes": 0,
        "max_future_tick_lots": 0,
        "max_option_tick_lots": 0,
        "reason": "",
    }

    all_opt_tokens = []
    underlying_map = {}
    for name in track_names:
        base_symbol = fut_by_name.get(name, "")
        u_ltp = data.get(base_symbol, {}).get("last_price", 0)
        if u_ltp <= 0:
            continue
        df = get_relevant_options(name, u_ltp, strike_range=get_burst_option_strike_range(name))
        if df.empty:
            continue
        underlying_map[name] = (df, u_ltp)
        all_opt_tokens.extend(df["instrument_token"].tolist())
    stats["option_tokens"] = len(all_opt_tokens)

    opt_quotes = get_option_quotes_ws_only(all_opt_tokens, max_age_seconds=15)
    missing_option_tokens = [
        token for token in all_opt_tokens
        if str(int(token)) not in opt_quotes
    ]
    if all_opt_tokens and (quote_source == "rest_fallback" or missing_option_tokens):
        fallback_opt_quotes = _get_burst_option_quotes_with_fallback(kite, all_opt_tokens)
        if fallback_opt_quotes:
            opt_quotes.update(fallback_opt_quotes)
            quote_source = "rest_fallback"
            stats["source"] = quote_source

    _set_burst_quote_status(
        quote_source,
        f"session={session} futures={len(data)} options={len(opt_quotes)}",
    )

    for name in track_names:
        sym = fut_by_name.get(name)
        if not sym or sym not in data:
            continue

        d = data[sym]
        ltp = d["last_price"]
        oi = d.get("oi", 0)
        target_alerts = bn_alerts if is_index_underlying(name) else stock_alerts

        process_future_burst(sym, name, ltp, oi, target_alerts, stats=stats)
        process_option_logic(
            name,
            underlying_map.get(name, (pd.DataFrame(), 0)),
            opt_quotes,
            target_alerts,
            stats=stats,
        )

    if stats["future_quotes"] == 0:
        stats["reason"] = "no current future quote"
    elif stats["future_oi_quotes"] == 0 and stats["option_oi_quotes"] == 0:
        stats["reason"] = "OI missing/zero in quotes"
    elif (
        stats["max_future_tick_lots"] < stats["future_threshold"]
        and stats["max_option_tick_lots"] < stats["option_threshold"]
    ):
        stats["reason"] = "OI move below threshold"
    else:
        stats["reason"] = "watching 15-second confirmation"
    _set_burst_monitor_status(stats)

    return bn_alerts, stock_alerts


def calculate_gap_alerts(kite, batch_index=0, max_quote_symbols=500):
    if non_burst_alerts_paused_today():
        return []
    return build_monthly_future_gap_alerts(
        kite,
        batch_index=batch_index,
        max_quote_symbols=max_quote_symbols,
    )


def calculate_historical_alerts(kite):
    alerts = []
    alerts.extend(calculate_first_30m_alerts(kite))
    alerts.extend(calculate_other_historical_alerts(kite))
    return alerts


def calculate_first_30m_alerts(kite):
    if non_burst_alerts_paused_today():
        return []

    return build_first_30m_future_volume_mismatch_alerts(kite)


def calculate_other_historical_alerts(kite):
    if non_burst_alerts_paused_today():
        return []

    alerts = []
    alerts.extend(build_previous_day_future_volume_mismatch_alerts(kite))
    alerts.extend(build_weekly_future_volume_mismatch_alerts(kite))
    alerts.extend(build_stock_future_1hr_s4_alerts(kite))
    alerts.extend(build_weekly_born_breakout_alerts(kite))
    return alerts


def calculate_heatmap(kite):
    fut_symbols = get_bank_futures(kite)
    symbols = list(fut_symbols)
    # Dynamically add spots for tracked indices
    for name in NSE_BURST_TRACK_NAMES:
        if is_index_underlying(name):
            symbols.append(get_spot_symbol(name))

    data = get_symbol_quotes_with_fallback(kite, symbols)
    if not data:
        return 0, "", [], [], []

    bn_alerts = []
    stock_alerts = []
    gap_alerts = []

    fut_by_name = _map_tracked_futures_by_name(fut_symbols)

    all_opt_tokens = []
    underlying_map = {}
    bnf_future_symbol = fut_by_name.get("BANKNIFTY", "")

    for name in BURST_TRACK_NAMES:
        base_symbol = fut_by_name.get(name, "")
        u_ltp = data.get(base_symbol, {}).get("last_price", 0)
        if u_ltp <= 0:
            continue
        df = get_relevant_options(name, u_ltp)
        if df.empty:
            continue
        underlying_map[name] = (df, u_ltp)
        all_opt_tokens.extend(df["instrument_token"].tolist())

    opt_quotes = get_option_quotes_with_fallback(kite, all_opt_tokens)

    for name in BURST_TRACK_NAMES:
        sym = fut_by_name.get(name)
        if not sym or sym not in data:
            continue

        d = data[sym]
        ltp = d["last_price"]
        oi = d.get("oi", 0)
        target_alerts = bn_alerts if is_index_underlying(name) else stock_alerts

        process_future_burst(sym, name, ltp, oi, target_alerts)
        process_option_logic(name, underlying_map.get(name, (pd.DataFrame(), 0)), opt_quotes, target_alerts)

    if non_burst_alerts_paused_today():
        return 0, "", bn_alerts, stock_alerts, []

    gap_alerts = build_monthly_future_gap_alerts(kite)
    gap_alerts.extend(build_stock_future_1hr_s4_alerts(kite))
    gap_alerts.extend(build_weekly_born_breakout_alerts(kite))
    return 0, "", bn_alerts, stock_alerts, gap_alerts
