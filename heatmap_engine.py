import os
import time
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from iv_engine import direction_engine
from kite_rate_limiter import kite_historical_data, kite_quote
from websocket_flow import get_symbol_quotes, get_token_quotes

INDEX_BURST_NAMES = {"BANKNIFTY"}
BURST_OPTION_EXCLUDED_NAMES = {
    "NIFTY",
    "FINNIFTY",
    "MIDCPNIFTY",
    "SENSEX",
    "BANKEX",
    "SENSEX50",
}
STOCK_BURST_NAMES = {
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
}
NSE_BURST_TRACK_NAMES = []
MCX_BURST_TRACK_NAMES = [
    "CRUDEOIL"
]
MCX_BURST_NAMES = set(MCX_BURST_TRACK_NAMES)
BURST_TRACK_NAMES = NSE_BURST_TRACK_NAMES
ENABLE_INDEX_BURST_ALERTS = os.getenv("ENABLE_INDEX_BURST_ALERTS", "false").lower() in (
    "true",
    "1",
    "yes",
    "on",
)
ENABLE_MCX_BURST_ALERTS = os.getenv("ENABLE_MCX_BURST_ALERTS", "true").lower() in ("true", "1", "yes", "on")
BURST_OPTION_STRIKE_RANGE = 25
BANKNIFTY_BURST_OPTION_STRIKE_RANGE = 25
STOCK_BURST_OPTION_STRIKE_RANGE = 5
MCX_BURST_OPTION_STRIKE_RANGE = int(os.getenv("MCX_BURST_OPTION_STRIKE_RANGE", "10"))
STOCK_BURST_STRIKES_BELOW_ATM = 5
STOCK_BURST_STRIKES_ABOVE_ATM = 5
BANKNIFTY_BURST_STRIKES_BELOW_ATM = 20
BANKNIFTY_BURST_STRIKES_ABOVE_ATM = 20
BURST_THRESHOLD_LOTS = int(os.getenv("BURST_THRESHOLD_LOTS", "100"))
OPTION_BURST_THRESHOLD_LOTS = int(os.getenv("OPTION_BURST_THRESHOLD_LOTS", "100"))
FUTURE_BURST_THRESHOLD_LOTS = int(os.getenv("FUTURE_BURST_THRESHOLD_LOTS", "1000"))
BANKNIFTY_OPTION_BURST_THRESHOLD_LOTS = int(os.getenv("BANKNIFTY_OPTION_BURST_THRESHOLD_LOTS", "100"))
BANKNIFTY_HIGH_PREMIUM_PRICE = float(os.getenv("BANKNIFTY_HIGH_PREMIUM_PRICE", "1500"))
BANKNIFTY_HIGH_PREMIUM_THRESHOLD_LOTS = int(os.getenv("BANKNIFTY_HIGH_PREMIUM_THRESHOLD_LOTS", "100"))
INDEX_BURST_THRESHOLD_LOTS = int(os.getenv("INDEX_OPTION_BURST_THRESHOLD_LOTS", str(OPTION_BURST_THRESHOLD_LOTS)))
STOCK_BURST_THRESHOLD_LOTS = int(os.getenv("STOCK_OPTION_BURST_THRESHOLD_LOTS", "100"))
MCX_BURST_THRESHOLD_LOTS = int(os.getenv("MCX_OPTION_BURST_THRESHOLD_LOTS", "100"))
INDEX_FUTURE_BURST_THRESHOLD_LOTS = int(os.getenv("INDEX_FUTURE_BURST_THRESHOLD_LOTS", str(FUTURE_BURST_THRESHOLD_LOTS)))
STOCK_FUTURE_BURST_THRESHOLD_LOTS = int(os.getenv("STOCK_FUTURE_BURST_THRESHOLD_LOTS", str(FUTURE_BURST_THRESHOLD_LOTS)))
MCX_FUTURE_BURST_THRESHOLD_LOTS = int(os.getenv("MCX_FUTURE_BURST_THRESHOLD_LOTS", str(FUTURE_BURST_THRESHOLD_LOTS)))
BURST_REST_FALLBACK_CACHE_SECONDS = int(os.getenv("BURST_REST_FALLBACK_CACHE_SECONDS", "3"))
DEBUG_BURST_PRICE_NORMALIZATION = os.getenv("DEBUG_BURST_PRICE_NORMALIZATION", "false").lower() in ("true", "1", "yes", "on")
DEBUG_BURST_STRIKES = os.getenv("DEBUG_BURST_STRIKES", "false").lower() in ("true", "1", "yes", "on")
INDEX_SYMBOL = "NSE:NIFTY BANK"
INDEX_FUTURE_NAMES = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX", "SENSEX50"}
# Expiry rollover policy.  A contract expiring today is treated as expired so
# the scanner moves to the next monthly contract from the next session.  The
# default of one day also makes the scanner ignore the just-expired contract
# when it is started on the following day.
EXPIRY_ROLLOVER_DAYS = int(os.getenv("EXPIRY_ROLLOVER_DAYS", "1"))

day_open_oi_store = {}
option_history = {}
active_watches = {}
gap_alert_store = {}
s4_alert_store = {}
s4_state_store = {}
s4_last_slot = None
first_60m_mismatch_scan_dates = set()
first_60m_mismatch_last_scan_time = None
daily_mismatch_break_alert_store = {}
weekly_mismatch_break_alert_store = {}
daily_mismatch_setup_date = None
daily_mismatch_setup_rows = []
weekly_mismatch_setup_date = None
weekly_mismatch_setup_rows = []

born_breakout_last_check_time = None
born_breakout_alert_store = {}
burst_alert_store = {}
volume_burst_store = {}

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
MONTHLY_FUTURE_GAP_START_TIME = datetime.strptime("09:15", "%H:%M").time()
GAP_ALERT_COOLDOWN_SECONDS = 3600
S4_PIVOT_RANGE_PCT = 0.5
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
first_60m_MISMATCH_CANDLE_START_TIME = datetime.strptime("09:15", "%H:%M").time()
first_60m_MISMATCH_SCAN_START_TIME = datetime.strptime("10:15", "%H:%M").time()
first_60m_MISMATCH_GAP_THRESHOLD_PCT = float(os.getenv("FIRST_60M_MISMATCH_GAP_THRESHOLD_PCT", "3.0"))
first_60m_MISMATCH_MIN_VOLUME = int(os.getenv("FIRST_60M_MISMATCH_MIN_VOLUME", "300000"))
first_60m_MISMATCH_RETRY_SECONDS = 30
first_60m_OPTION_ITM_COUNT = int(os.getenv("first_60m_OPTION_ITM_COUNT", "4"))
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
    if name == "BANKNIFTY":
        return BANKNIFTY_OPTION_BURST_THRESHOLD_LOTS
    if is_index_underlying(name):
        return INDEX_BURST_THRESHOLD_LOTS
    if is_mcx_underlying(name):
        return MCX_BURST_THRESHOLD_LOTS
    return STOCK_BURST_THRESHOLD_LOTS


def get_option_burst_threshold_for_price(name, price):
    if name == "BANKNIFTY" and float(price or 0) >= BANKNIFTY_HIGH_PREMIUM_PRICE:
        return BANKNIFTY_HIGH_PREMIUM_THRESHOLD_LOTS
    return get_option_burst_threshold(name)


def get_future_burst_threshold(name):
    if is_index_underlying(name):
        return INDEX_FUTURE_BURST_THRESHOLD_LOTS
    if is_mcx_underlying(name):
        return MCX_FUTURE_BURST_THRESHOLD_LOTS
    return STOCK_FUTURE_BURST_THRESHOLD_LOTS


def get_burst_threshold(name):
    return get_option_burst_threshold(name)


def get_burst_option_strike_range(name):
    if name == "BANKNIFTY":
        return BANKNIFTY_BURST_OPTION_STRIKE_RANGE
    if name in STOCK_BURST_NAMES:
        return STOCK_BURST_OPTION_STRIKE_RANGE
    return BURST_OPTION_STRIKE_RANGE


def get_burst_option_strike_window(name):
    if name in BURST_OPTION_EXCLUDED_NAMES:
        return 0, 0
    if name == "BANKNIFTY":
        return BANKNIFTY_BURST_STRIKES_BELOW_ATM, 0
    if name in STOCK_BURST_NAMES:
        return STOCK_BURST_STRIKES_BELOW_ATM, 0
    return 1, 0


def get_burst_session(now_ist=None):
    now_ist = now_ist or datetime.now(IST)
    if now_ist.weekday() > 4:
        return None

    from env_config import NSE_HOLIDAYS
    t = now_ist.time()
    # NSE session ends at 15:30:00
    if NSE_BURST_START_TIME <= t < NSE_BURST_END_TIME:
        if now_ist.date().isoformat() in NSE_HOLIDAYS:
            return None
        return "nse"
    # MCX evening session (15:30 to 23:30 IST)
    if MCX_BURST_START_TIME <= t <= MCX_BURST_END_TIME:
        return "mcx"
    return None


def is_burst_session_open(now_ist=None):
    return get_burst_session(now_ist) is not None


def get_active_burst_names(now_ist=None):
    now_ist = now_ist or datetime.now(IST)
    t = now_ist.time()
    session = get_burst_session(now_ist)
    if session == "mcx":
        return ["CRUDEOIL"]
    if session == "nse":
        if datetime.strptime("09:15", "%H:%M").time() <= t < datetime.strptime("09:21", "%H:%M").time():
            return []
        return sorted(set(INDEX_BURST_NAMES) | set(STOCK_BURST_NAMES))
    return []


def get_burst_subscription_names(now_ist=None):
    now_ist = now_ist or datetime.now(IST)
    active_names = get_active_burst_names(now_ist)
    if active_names:
        return active_names

    if now_ist.weekday() <= 4:
        t = now_ist.time()
        if datetime.strptime("09:15", "%H:%M").time() <= t < datetime.strptime("09:21", "%H:%M").time():
            return []  # skip burst alerts between 09:15 and 09:20
        if t < NSE_BURST_START_TIME:
            return sorted(set(INDEX_BURST_NAMES) | set(STOCK_BURST_NAMES))
        if NSE_BURST_END_TIME <= t <= MCX_BURST_END_TIME:
            return ["CRUDEOIL"]

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


def _monthly_expiry_candidates(expiries):
    valid_expiries = sorted(exp for exp in expiries if pd.notna(exp))
    if not valid_expiries:
        return []

    # Keep only the last expiry available in each calendar month.  This
    # removes weekly expiries from monthly futures/options selection.
    month_last_expiries = {}
    for expiry in valid_expiries:
        month_last_expiries[(int(expiry.year), int(expiry.month))] = expiry
    return [month_last_expiries[key] for key in sorted(month_last_expiries)]


def get_monthly_expiry(expiries, rollover_days=EXPIRY_ROLLOVER_DAYS):
    """Return the first non-expired monthly expiry.

    Expiries on or before the rollover cutoff are intentionally ignored.  This
    is the central selector used by futures, options, burst, gap, S4, and
    historical-alert logic.
    """
    candidates = _monthly_expiry_candidates(expiries)
    if not candidates:
        return None

    cutoff = datetime.now(IST).date() + timedelta(days=max(0, int(rollover_days)))
    return next((expiry for expiry in candidates if expiry.date() > cutoff), None)


def get_target_monthly_expiry(expiries):
    # Kept as a compatibility alias for callers using the old function name.
    return get_monthly_expiry(expiries)


def get_next_monthly_expiry(expiries):
    current = get_monthly_expiry(expiries)
    if current is None:
        return None
    candidates = _monthly_expiry_candidates(expiries)
    return next((expiry for expiry in candidates if expiry > current), None)


def _get_instruments_mtime():
    try:
        return os.path.getmtime("instruments.csv")
    except OSError:
        return None


def _drop_expired_contracts(df):
    """Remove expired/rolling-off contracts from the in-memory instrument data."""
    if df is None or df.empty or "expiry" not in df.columns:
        return df
    cutoff = pd.Timestamp(
        datetime.now(IST).date() + timedelta(days=max(0, EXPIRY_ROLLOVER_DAYS))
    )
    return df[df["expiry"].notna() & (df["expiry"] > cutoff)].copy()


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
            _options_df = _drop_expired_contracts(_options_df)
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
            _futures_df = _drop_expired_contracts(_futures_df)
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


def _normalize_burst_price(name, price):
    try:
        value = float(price or 0)
    except Exception:
        return 0.0

    # Some BANKNIFTY index-future feeds have been observed arriving 100x too large.
    if name == "BANKNIFTY" and value >= 100000:
        if DEBUG_BURST_PRICE_NORMALIZATION:
            print(f"[BURST DEBUG] Normalizing {name} price from {value} to {value / 100.0}")
        return value / 100.0
    return value


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
        next_lot_size = None
        if not next_futures.empty:
            next_row = next_futures.iloc[0]
            next_symbol = f"NFO:{next_row['tradingsymbol']}"
            next_month_label = _format_month_label(next_row["expiry"])
            next_token = int(next_row["instrument_token"])
            next_expiry = next_row["expiry"]
            next_lot_size = _get_row_lot_size(next_row)

        contracts.append(
            {
                "name": name,
                "symbol": f"NFO:{row['tradingsymbol']}",
                "token": int(row["instrument_token"]),
                "expiry": current_expiry,
                "month_label": _format_month_label(current_expiry),
                "lot_size": _get_row_lot_size(row),
                "next_symbol": next_symbol,
                "next_month_label": next_month_label,
                "next_token": next_token,
                "next_expiry": next_expiry,
                "next_lot_size": next_lot_size,
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


def _get_first_60m_future_contracts():
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


def _get_first_60m_candle(kite, token, now_ist):
    session_start = datetime.combine(
        now_ist.date(),
        first_60m_MISMATCH_CANDLE_START_TIME,
        tzinfo=IST,
    )
    session_end = session_start + timedelta(minutes=60)
    try:
        candles = kite_historical_data(kite, token, session_start, session_end, "60minute")
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
            and candle_time.time() == first_60m_MISMATCH_CANDLE_START_TIME
        ):
            return candle

    for candle in candles:
        candle_time = candle.get("date")
        if hasattr(candle_time, "astimezone"):
            candle_time = candle_time.astimezone(IST)
        if candle_time and candle_time.date() == now_ist.date():
            return candle

    return None


def _get_first_60m_candle_context(kite, token, now_ist, label="First 30m"):
    session_start = datetime.combine(
        now_ist.date(),
        first_60m_MISMATCH_CANDLE_START_TIME,
        tzinfo=IST,
    )
    session_end = session_start + timedelta(minutes=60)
    prev_day = _get_previous_trading_day(now_ist)
    from_time = datetime.combine(
        prev_day,
        datetime.strptime("09:15", "%H:%M").time(),
        tzinfo=IST,
    )

    try:
        candles = get_historical_data_cached(kite, token, from_time, session_end, "60minute")
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
            and candle_time.time() == first_60m_MISMATCH_CANDLE_START_TIME
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


def _get_first_60m_itm_options(name, ltp, option_type, count=None):
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

    limit = count if count is not None else first_60m_OPTION_ITM_COUNT
    return options.head(max(0, int(limit))).copy()


def _build_first_60m_option_mismatch_rows(kite, name, ltp, gap_pct, now_ist):
    option_type = "PE" if gap_pct > 0 else "CE"
    rows = []

    for _, option in _get_first_60m_itm_options(name, ltp, option_type).iterrows():
        context = _get_first_60m_candle_context(
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
        if volume <= first_60m_MISMATCH_MIN_VOLUME :
            continue

        option_gap_pct = ((open_price - previous_close) / previous_close) * 100
        if abs(option_gap_pct) < first_60m_MISMATCH_GAP_THRESHOLD_PCT:
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


def build_first_60m_future_volume_mismatch_alerts(kite):
    global first_60m_mismatch_last_scan_time

    now_ist = datetime.now(IST)
    if now_ist.weekday() > 4 or now_ist.time() < first_60m_MISMATCH_SCAN_START_TIME:
        return []

    scan_date = now_ist.date().isoformat()
    if scan_date in first_60m_mismatch_scan_dates:
        return []

    if (
        first_60m_mismatch_last_scan_time
        and (now_ist - first_60m_mismatch_last_scan_time).total_seconds()
        < first_60m_MISMATCH_RETRY_SECONDS
    ):
        return []
    first_60m_mismatch_last_scan_time = now_ist

    contracts = _get_first_60m_future_contracts()
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
        if abs(rough_gap_pct) < first_60m_MISMATCH_GAP_THRESHOLD_PCT:
            continue

        item = dict(contract)
        item["previous_close"] = previous_close
        item["ltp"] = ltp
        candidates.append(item)

    if not candidates:
        first_60m_mismatch_scan_dates.add(scan_date)
        return []

    rows = []
    processed_candles = 0
    for contract in candidates:
        context = _get_first_60m_candle_context(
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
        if volume <= first_60m_MISMATCH_MIN_VOLUME :
            continue

        gap_pct = ((open_price - previous_close) / previous_close) * 100
        if abs(gap_pct) < first_60m_MISMATCH_GAP_THRESHOLD_PCT:
            continue

        price_color = _candle_color(open_price, close)
        volume_color = _volume_candle_color(historical_previous_close, close)
        if not price_color or not volume_color or price_color == volume_color:
            continue

        option_rows = _build_first_60m_option_mismatch_rows(
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

    first_60m_mismatch_scan_dates.add(scan_date)
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
            "FIRST 60M GAP VOLUME MISMATCH\n\n"
            f"{body}"
        )

    return alerts


FIRST_5M_MISMATCH_CANDLE_START_TIME = datetime.strptime("09:15", "%H:%M").time()
FIRST_5M_MISMATCH_SCAN_START_TIME = datetime.strptime("09:20", "%H:%M").time()
FIRST_5M_MISMATCH_GAP_THRESHOLD_PCT = float(os.getenv("FIRST_5M_MISMATCH_GAP_THRESHOLD_PCT", "1.0"))
FIRST_5M_MISMATCH_MIN_VOLUME = int(os.getenv("FIRST_5M_MISMATCH_MIN_VOLUME", "300000"))
FIRST_5M_MISMATCH_RETRY_SECONDS = 30

first_5m_mismatch_scan_dates = set()
first_5m_mismatch_last_scan_time = None

def _get_first_5m_candle_context(kite, token, now_ist, label="First 5m"):
    session_start = datetime.combine(now_ist.date(), FIRST_5M_MISMATCH_CANDLE_START_TIME, tzinfo=IST)
    session_end = session_start + timedelta(minutes=5)
    prev_day = _get_previous_trading_day(now_ist)
    from_time = datetime.combine(prev_day, datetime.strptime("09:15", "%H:%M").time(), tzinfo=IST)
    try:
        candles = get_historical_data_cached(kite, token, from_time, session_end, "5minute")
    except Exception as e:
        print(f"{label} historical data error for {token}: {e}")
        return None
    normalized = []
    for candle in candles:
        candle_time = candle.get("date")
        if candle_time is None: continue
        if hasattr(candle_time, "astimezone"): candle_time = candle_time.astimezone(IST)
        normalized.append((candle_time, candle))
    normalized.sort(key=lambda item: item[0])
    first_index = None
    for index, (candle_time, _) in enumerate(normalized):
        if candle_time.date() == now_ist.date() and candle_time.time() == FIRST_5M_MISMATCH_CANDLE_START_TIME:
            first_index = index
            break
    if first_index is None: return None
    previous = [item[1] for item in normalized[:first_index]][-5:]
    if len(previous) < 5: return None
    previous_close = float(previous[-1].get("close", 0) or 0)
    previous_volume_max = max((float(c.get("volume", 0) or 0) for c in previous), default=0)
    return {"candle": normalized[first_index][1], "previous_close": previous_close, "previous_volume_max": previous_volume_max}

def build_first_5m_future_volume_mismatch_alerts(kite):
    global first_5m_mismatch_last_scan_time
    now_ist = datetime.now(IST)
    if now_ist.weekday() > 4 or now_ist.time() < FIRST_5M_MISMATCH_SCAN_START_TIME: return []
    scan_date = now_ist.date().isoformat()
    if scan_date in first_5m_mismatch_scan_dates: return []
    if first_5m_mismatch_last_scan_time and (now_ist - first_5m_mismatch_last_scan_time).total_seconds() < FIRST_5M_MISMATCH_RETRY_SECONDS: return []
    first_5m_mismatch_last_scan_time = now_ist

    futures_df = load_futures_data()
    if futures_df is None or futures_df.empty: return []
    
    contracts = []
    for name in EXHAUSTION_REVERSAL_WATCHLIST:
        frow = futures_df[futures_df["name"] == name]
        if not frow.empty:
            f = frow.iloc[0]
            contracts.append({
                "name": name,
                "symbol": f["tradingsymbol"],
                "token": int(f["instrument_token"]),
                "kind": "future",
                "month_label": _format_month_label(f["expiry"]) if pd.notna(f.get("expiry")) else ""
            })
            
    if not contracts: return []
    symbols = [c["symbol"] for c in contracts]
    data = get_symbol_quotes_with_fallback(kite, symbols)
    if not data: return []

    candidates = []
    for contract in contracts:
        quote = data.get(contract["symbol"], {})
        ohlc = quote.get("ohlc") or {}
        previous_close = float(ohlc.get("close", 0) or 0)
        day_open = float(ohlc.get("open", 0) or 0)
        ltp = float(quote.get("last_price", 0) or day_open)
        if previous_close <= 0 or day_open <= 0: continue
        item = dict(contract)
        item["previous_close"] = previous_close
        item["ltp"] = ltp
        candidates.append(item)

    if not candidates:
        first_5m_mismatch_scan_dates.add(scan_date)
        return []

    rows = []
    processed_candles = 0
    for contract in candidates:
        context = _get_first_5m_candle_context(kite, contract["token"], now_ist)
        if not context: continue
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
        
        if previous_close <= 0 or historical_previous_close <= 0 or open_price <= 0 or close <= 0: continue
        
        gap_pct = ((open_price - previous_close) / previous_close) * 100
        
        price_color = _candle_color(open_price, close)
        volume_color = _volume_candle_color(historical_previous_close, close)
        if not price_color or not volume_color or price_color == volume_color: continue
        
        option_type = "PE" if price_color == "Bearish" else "CE"
        option_rows = []
        options = _get_exhaustion_options(contract["name"], float(contract.get("ltp", 0) or close))
        for option in options:
            if option["option_type"] != option_type: continue
            opt_context = _get_first_5m_candle_context(kite, option["token"], now_ist, label="First 5m option")
            if not opt_context: continue
            opt_candle = opt_context["candle"]
            opt_prev_close = float(opt_context["previous_close"] or 0)
            opt_prev_vol_max = float(opt_context["previous_volume_max"] or 0)
            opt_open = float(opt_candle.get("open", 0) or 0)
            opt_close = float(opt_candle.get("close", 0) or 0)
            opt_vol = float(opt_candle.get("volume", 0) or 0)
            
            if opt_prev_close <= 0 or opt_open <= 0 or opt_close <= 0: continue
            opt_gap_pct = ((opt_open - opt_prev_close) / opt_prev_close) * 100
            opt_price_color = _candle_color(opt_open, opt_close)
            opt_volume_color = _volume_candle_color(opt_prev_close, opt_close)
            if not opt_price_color or not opt_volume_color or opt_price_color == opt_volume_color: continue
            
            option_rows.append({
                "symbol": option["symbol"].split(":")[1] if ":" in option["symbol"] else option["symbol"],
                "type": option["option_type"],
                "gap_pct": opt_gap_pct,
                "volume": opt_vol,
                "previous_volume_max": opt_prev_vol_max,
                "price_color": opt_price_color,
                "volume_color": opt_volume_color,
            })
            
        rows.append({
            "name": contract["name"],
            "symbol": contract["symbol"],
            "month_label": contract["month_label"],
            "open": open_price,
            "high": high,
            "low": low,
            "volume": volume,
            "previous_volume_max": previous_volume_max,
            "gap_pct": gap_pct,
            "price_color": price_color,
            "volume_color": volume_color,
            "option_rows": option_rows,
        })

    if processed_candles == 0: return []
    first_5m_mismatch_scan_dates.add(scan_date)
    if not rows: return []
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
                        f"Symbol: {option['symbol']} | Gap {option['gap_pct']:+.2f}% | "
                        f"Vol {format_volume(option['volume'])} > Prev5 Max {format_volume(option['previous_volume_max'])} | "
                        f"Price {option['price_color']} vs Volume {option['volume_color']}"
                    )
        body = "\n".join(body_lines)
        alerts.append(f"FIRST 5M VOLUME MISMATCH\n\n{body}")
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


def get_burst_relevant_options(name, future_ltp):
    df = load_options_data()
    future_ltp = _normalize_burst_price(name, future_ltp)
    if df is None or df.empty or future_ltp <= 0:
        return pd.DataFrame()
    if name in BURST_OPTION_EXCLUDED_NAMES:
        return pd.DataFrame()

    options = df[df["name"] == name]
    if options.empty:
        return pd.DataFrame()

    monthly_expiry = get_monthly_expiry(options["expiry"].unique())
    if monthly_expiry is None:
        return pd.DataFrame()

    log_key = f"burst_options:{name}"
    expiry_text = monthly_expiry.strftime("%d-%m-%Y")
    if _last_logged_expiry.get(log_key) != expiry_text:
        print(f"Selected burst options expiry for {name}: {expiry_text}")
        _last_logged_expiry[log_key] = expiry_text

    options = options[options["expiry"] == monthly_expiry].copy()
    if options.empty:
        return pd.DataFrame()

    itm_count, _ = get_burst_option_strike_window(name)
    selected_frames = []

    for expiry, expiry_options in options.groupby("expiry"):
        strikes = sorted(expiry_options["strike"].unique())
        if not strikes:
            continue

        atm = min(strikes, key=lambda x: abs(x - future_ltp))
        idx = strikes.index(atm)
        selected = set()
        for _, row in expiry_options.iterrows():
            strike = row["strike"]
            option_type = str(row.get("instrument_type", "") or "").upper()
            if option_type not in {"CE", "PE"}:
                tradingsymbol = str(row.get("tradingsymbol", "") or "").upper()
                if tradingsymbol.endswith("CE"):
                    option_type = "CE"
                elif tradingsymbol.endswith("PE"):
                    option_type = "PE"
                else:
                    # If the contract cannot be classified, do not include it.
                    continue

            # ATM is always included for both CE and PE.
            # ITM selection is side-aware:
            # - CE: strikes at or below ATM
            # - PE: strikes at or above ATM
            # OTM is excluded completely.
            if strike == atm:
                selected.add(strike)
                continue

            if option_type == "CE":
                if strike < atm:
                    lower_bound = strikes[max(0, idx - itm_count)]
                    if lower_bound <= strike <= atm:
                        selected.add(strike)
            elif option_type == "PE":
                if strike > atm:
                    upper_bound = strikes[min(len(strikes) - 1, idx + itm_count)]
                    if atm <= strike <= upper_bound:
                        selected.add(strike)

        selected = sorted(selected)
        selected_rows = expiry_options[expiry_options["strike"].isin(selected)].copy()
        if not selected_rows.empty:
            selected_rows = selected_rows[
                selected_rows.apply(
                    lambda row: (
                        row["strike"] == atm
                        or (
                            str(row.get("instrument_type", "") or "").upper() == "CE"
                            and row["strike"] < atm
                        )
                        or (
                            str(row.get("instrument_type", "") or "").upper() == "PE"
                            and row["strike"] > atm
                        )
                    ),
                    axis=1,
                )
            ]
        if DEBUG_BURST_STRIKES:
            print(
                f"[BURST DEBUG] {name} future_ltp={future_ltp:.2f} "
                f"atm={atm} itm_count={itm_count} "
                f"selected={selected[:5]}{'...' if len(selected) > 5 else ''} "
                f"count={len(selected_rows)}"
            )
        selected_frames.append(selected_rows)

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
            contract.get("lot_size"),
            contract.get("next_lot_size"),
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
    for _, spot_symbol, future_symbol, _, next_symbol, _, _, _ in symbol_pairs:
        quote_symbols.append(spot_symbol)
        quote_symbols.append(future_symbol)
        if next_symbol:
            quote_symbols.append(next_symbol)

    data = get_symbol_quotes_with_fallback(kite, quote_symbols)
    if not data:
        return []

    now = datetime.now(IST)
    rows = []
    for (
        name,
        spot_symbol,
        future_symbol,
        month_label,
        next_symbol,
        next_month_label,
        lot_size,
        next_lot_size,
    ) in symbol_pairs:
        if not lot_size or not next_lot_size or lot_size != next_lot_size:
            continue

        spot_price = data.get(spot_symbol, {}).get("last_price", 0)
        future_price = data.get(future_symbol, {}).get("last_price", 0)
        if spot_price <= 0 or future_price <= 0:
            continue

        next_future_price = data.get(next_symbol, {}).get("last_price", 0) if next_symbol else 0
        if next_future_price <= 0:
            continue

        gap_points = next_future_price - spot_price
        next_gap_points = next_future_price - future_price
        near_spot_gap_points = future_price - spot_price
        gap_pct = (gap_points / spot_price) * 100
        next_gap_pct = (next_gap_points / future_price) * 100
        near_spot_gap_pct = (near_spot_gap_points / spot_price) * 100

        # Updated Gap Hedge Logic:
        # 1. Absolute gap between Spot and Next Future must be GREATER THAN OR EQUAL to 2.0%
        # 2. Either the gap between Near and Next must be <= 1.0%,
        #    or the gap between Spot and Near must be <= 0.5%.
        if abs(gap_pct) < MONTHLY_FUTURE_GAP_THRESHOLD_PCT:
            continue

        if (
            abs(next_gap_pct) > MONTHLY_FUTURE_NEXT_GAP_MAX_PCT
            and abs(near_spot_gap_pct) > 0.5
        ):
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
            "gap_points": gap_points,
            "next_future_price": next_future_price,
            "next_gap_pct": next_gap_pct,
            "next_gap_points": next_gap_points,
            "next_month_label": next_month_label,
            "lot_size": lot_size,
            "next_lot_size": next_lot_size,
            "loss_value": abs(next_gap_points) * lot_size,
            "profit_value": abs(gap_points) * lot_size,
        }
        rows.append(item)

    if not rows:
        return []

    rows.sort(key=lambda item: abs(item["gap_pct"]), reverse=True)
    alerts = []
    chunk_size = 20
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i:i + chunk_size]
        body_lines = []
        for item in chunk:
            body_lines.extend([
                f"{item['name']}: Spot {item['spot_price']:.2f} | "
                f"{item['month_label']} Fut {item['future_price']:.2f} ({item['lot_size']} Lot) | "
                f"{item['next_month_label']}-vs-Spot Gap {item['gap_pct']:+.2f}% ({item['gap_points']:.2f}) |",
                f"{item['next_month_label']} Fut {item['next_future_price']:.2f} ({item['next_lot_size']} Lot) | "
                f"{item['next_month_label']}-vs-{item['month_label']} "
                f"{item['next_gap_pct']:+.2f}% ({item['next_gap_points']:.2f}) | "
                f"{_format_gap_signal(item['gap_pct'])}",
                f"Loss ({item['loss_value']:.0f}) , Profit ({item['profit_value']:.0f})",
                "",
            ])
        body = "\n".join(body_lines)
        report_month = chunk[0]["month_label"] if chunk else "MONTHLY"
        alerts.append(f"📊 {report_month} FUTURE GAP REPORT\n\n{body}")

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


def build_stock_future_1hr_s4_alerts(kite):
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


def process_future_burst(kite, token, symbol, name, ltp, oi, alerts_list, stats=None):
    if not is_burst_underlying(name):
        return

    ltp = _normalize_burst_price(name, ltp)

    threshold = get_future_burst_threshold(name)
    lot_size = get_future_lot_size(symbol)
    if not lot_size:
        _log_missing_lot_size_once(f"future:{symbol}", symbol)
        return

    clean_symbol = symbol.split(":", 1)[1] if ":" in symbol else symbol
    if direction_engine:
        try:
            direction_engine.process_tick(
                symbol=clean_symbol,
                ltp=ltp,
                volume=oi,
                instrument_data={"instrument_type": "FUT"}
            )
        except Exception as e:
            print(f"Error in IV Engine (Future): {e}")

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
                "end_time": now + timedelta(seconds=60),
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


def process_option_logic(kite, name, underlying_data, option_quotes, alerts_list, stats=None):
    if not is_burst_underlying(name):
        return

    opt_df, u_ltp = underlying_data
    if opt_df.empty:
        return

    now = datetime.now(IST)
    u_ltp = _normalize_burst_price(name, u_ltp)
    if DEBUG_BURST_STRIKES:
        try:
            strikes = sorted({float(row["strike"]) for _, row in opt_df.iterrows()})
            print(
                f"[BURST RUNTIME DEBUG] {name} future_ltp={u_ltp:.2f} "
                f"option_rows={len(opt_df)} selected_strikes={strikes[:5]}{'...' if len(strikes) > 5 else ''} "
                f"count={len(strikes)}"
            )
        except Exception as e:
            print(f"[BURST RUNTIME DEBUG] {name} strike debug failed: {e}")

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
        volume = q.get("volume", 0)
        ltp = q.get("last_price", 0)
        ltp = float(ltp or 0)
        threshold = get_option_burst_threshold_for_price(name, ltp)
        t_int = int(row["instrument_token"])
        option_type = str(row.get("instrument_type", "") or "").upper()
        if option_type not in {"CE", "PE"}:
            tradingsymbol = str(row.get("tradingsymbol", "") or "").upper()
            if tradingsymbol.endswith("CE"):
                option_type = "CE"
            elif tradingsymbol.endswith("PE"):
                option_type = "PE"

        if direction_engine:
            try:
                direction_engine.process_tick(
                    symbol=row["tradingsymbol"],
                    ltp=ltp,
                    volume=volume,
                    instrument_data={
                        "instrument_type": option_type,
                        "strike": float(row["strike"]),
                        "expiry": row["expiry"],
                        "u_ltp": u_ltp
                    }
                )
            except Exception as e:
                print(f"Error in IV Engine (Option): {e}")

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
                    "end_time": now + timedelta(seconds=60),
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
                action = classify_action(watch["symbol"], oi_chg, p_chg)
                if "WRITER" in action:
                    final_threshold = 100
                else:
                    final_threshold = 2000
                    
                if final_lots >= final_threshold:
                    strength = get_strength_label(final_lots, watch["underlying"])
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
    volume_burst_store.clear()
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
    spot_symbols_by_name = {
        name: get_spot_symbol(name)
        for name in track_names
        if session == "nse"
    }
    symbols = list(dict.fromkeys([*fut_symbols, *spot_symbols_by_name.values()]))
    future_threshold = max(get_future_burst_threshold(name) for name in track_names)
    option_threshold = max(get_option_burst_threshold(name) for name in track_names)
    fut_by_name = _map_tracked_futures_by_name(fut_symbols, track_names)

    quote_source = "websocket"
    data = get_symbol_quotes_ws_only(symbols, max_age_seconds=15)
    missing_symbols = [symbol for symbol in symbols if symbol not in data]
    if not data or missing_symbols:
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
        df = get_burst_relevant_options(name, u_ltp)
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
        ltp = _normalize_burst_price(name, d["last_price"])
        volume = d.get("volume", 0)
        target_alerts = bn_alerts if is_index_underlying(name) else stock_alerts

        process_future_burst(kite, d['instrument_token'], sym, name, ltp, volume, target_alerts, stats=stats)
        process_option_logic(
            kite,
            name,
            underlying_map.get(name, (pd.DataFrame(), 0)),
            opt_quotes,
            target_alerts,
            stats=stats,
        )

    if stats["future_quotes"] == 0:
        stats["reason"] = "no current future quote"
    elif stats["future_oi_quotes"] == 0 and stats["option_oi_quotes"] == 0:
        stats["reason"] = "Volume missing/zero in quotes"
    elif (
        stats["max_future_tick_lots"] < stats["future_threshold"]
        and stats["max_option_tick_lots"] < stats["option_threshold"]
    ):
        stats["reason"] = "Volume move below threshold"
    else:
        stats["reason"] = "watching 1-minute confirmation"
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
    alerts.extend(calculate_first_60m_alerts(kite))
    alerts.extend(calculate_other_historical_alerts(kite))
    return alerts


def calculate_first_60m_alerts(kite):
    if non_burst_alerts_paused_today():
        return []

    alerts = []
    alerts.extend(build_first_5m_future_volume_mismatch_alerts(kite))
    alerts.extend(build_first_60m_future_volume_mismatch_alerts(kite))
    return alerts


def calculate_other_historical_alerts(kite):
    if non_burst_alerts_paused_today():
        return []

    alerts = []
    alerts.extend(build_previous_day_future_volume_mismatch_alerts(kite))
    alerts.extend(build_weekly_future_volume_mismatch_alerts(kite))
    alerts.extend(build_stock_future_1hr_s4_alerts(kite))
    alerts.extend(build_weekly_born_breakout_alerts(kite))
    alerts.extend(check_exhaustion_reversal_30m(kite))
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
        volume = d.get("volume", 0)
        target_alerts = bn_alerts if is_index_underlying(name) else stock_alerts

        process_future_burst(kite, d['instrument_token'], sym, name, ltp, volume, target_alerts)
        process_option_logic(kite, name, underlying_map.get(name, (pd.DataFrame(), 0)), opt_quotes, target_alerts)

    if non_burst_alerts_paused_today():
        return 0, "", bn_alerts, stock_alerts, []

    gap_alerts = build_monthly_future_gap_alerts(kite)
    gap_alerts.extend(build_stock_future_1hr_s4_alerts(kite))
    gap_alerts.extend(build_weekly_born_breakout_alerts(kite))
    return 0, "", bn_alerts, stock_alerts, gap_alerts



# ==============================================================================
# EXHAUSTION REVERSAL 30-MINUTE SCANNER
# Setup: Bearish Candle 1 (Vol>=100k) -> 3+ Lower Closes (Vol>=400k each)
#        -> Hammer/Doji/Rejection (Vol>=400k) -> Green Confirmation above High
# Targets: Top-15 stocks + NIFTY + BANKNIFTY  (ATM + 5 ITM CE and PE)
# ==============================================================================

EXHAUSTION_REVERSAL_WATCHLIST = [
    "NIFTY", "BANKNIFTY",
    "HDFCBANK", "ICICIBANK", "RELIANCE", "BHARTIARTL", "LT",
    "SBIN", "INFY", "AXISBANK", "TCS", "ITC", "M&M",
    "HINDUNILVR", "TATAMOTORS", "KOTAKBANK",
]

EXH_SETUP_VOL        = 100_000   # Candle 1 min volume
EXH_LOWER_VOL        = 400_000   # Lower-close candles min volume each
EXH_REVERSAL_VOL     = 400_000   # Reversal candle min volume
EXH_MIN_LOWER_CLOSES = 3         # Minimum consecutive lower closes

_exhaustion_triggered       = set()    # (name, rev_time_str) -> no duplicate alerts
_exhaustion_active_watch    = {}       # name -> watch info (waiting for live LTP cross)
_exhaustion_last_check_slot = None     # last 30-min slot index


def _classify_reversal_candle_30m(o, c, h, l):
    """Returns HAMMER / DOJI / REJECTION or None."""
    body    = abs(c - o)
    c_range = h - l
    if c_range <= 0:
        return None
    lower_wick = min(o, c) - l
    upper_wick = h - max(o, c)
    if body <= c_range * 0.1:
        return "DOJI"
    if (body <= c_range * 0.4) and (lower_wick >= body * 2) and (lower_wick >= c_range * 0.5):
        return "HAMMER"
    if (lower_wick >= c_range * 0.6) and (upper_wick <= c_range * 0.2):
        return "REJECTION"
    return None


def _get_exhaustion_options(name, ltp):
    """Returns option dicts: ATM + 5 ITM CE + 5 ITM PE for the given underlying."""
    options_df = load_options_data()
    if options_df is None or options_df.empty:
        return []
    underlying_opts = options_df[options_df["name"] == name]
    if underlying_opts.empty:
        return []
    expiry = get_monthly_expiry(underlying_opts["expiry"].unique())
    if expiry is None:
        return []
    exp_opts    = underlying_opts[underlying_opts["expiry"] == expiry].copy()
    strikes     = sorted(exp_opts["strike"].unique())
    if not strikes:
        return []
    atm         = min(strikes, key=lambda x: abs(x - ltp))
    idx         = strikes.index(atm)
    expiry_text = expiry.strftime("%d-%b-%Y").upper()
    ce_strikes  = strikes[max(0, idx - 5): idx + 1]        # ITM CE + ATM
    pe_strikes  = strikes[idx: min(len(strikes), idx + 6)] # ATM + ITM PE
    result = []
    for _, row in exp_opts.iterrows():
        itype  = str(row.get("instrument_type", "")).upper()
        strike = row["strike"]
        sym    = f"NFO:{row['tradingsymbol']}"
        tok    = int(row["instrument_token"])
        if itype in ("CE", "CALL") and strike in ce_strikes:
            result.append({"symbol": sym, "token": tok,
                           "itm_type": "ATM CE" if strike == atm else "ITM CE",
                           "expiry_text": expiry_text, "option_type": "CE"})
        elif itype in ("PE", "PUT") and strike in pe_strikes:
            result.append({"symbol": sym, "token": tok,
                           "itm_type": "ATM PE" if strike == atm else "ITM PE",
                           "expiry_text": expiry_text, "option_type": "PE"})
    return result


def _get_underlying_ltp_exh(kite, name):
    """LTP for index or stock (exhaustion scanner)."""
    from websocket_flow import get_symbol_quotes
    if name == "NIFTY":
        sym = "NSE:NIFTY 50"
    elif name == "BANKNIFTY":
        sym = "NSE:NIFTY BANK"
    else:
        sym = get_active_future(name)
    if not sym:
        return 0.0
    cached = get_symbol_quotes([sym])
    ltp = cached.get(sym, {}).get("last_price", 0.0)
    if ltp <= 0:
        try:
            q   = kite_quote(kite, [sym])
            ltp = q.get(sym, {}).get("last_price", 0.0)
        except Exception:
            pass
    return ltp


def check_exhaustion_reversal_30m(kite):
    """Scans 30-min candles for the Exhaustion Reversal pattern.
    When a valid Reversal candle (Hammer/Doji/Rejection) is found after the
    setup sequence, it is stored in _exhaustion_active_watch.
    The alert fires LIVE (via check_exhaustion_live_alerts) as soon as
    the underlying LTP crosses the reversal candle high - no closed candle wait.

    Pattern:
      Candle 1  : Vol >= 100k  (preferably bearish)
      Candles N : >= 3 consecutive lower closes, each Vol >= 400k
      Reversal  : Hammer / Doji / Rejection, Vol >= 400k  -> stored in watch
      LIVE LTP  : crosses Reversal High                   -> ALERT fires
    """
    global _exhaustion_last_check_slot

    now_ist = datetime.now(IST)
    if now_ist.weekday() > 4:
        return []

    market_open  = datetime.strptime("09:15", "%H:%M").time()
    market_close = datetime.strptime("15:30", "%H:%M").time()
    if not (market_open <= now_ist.time() <= market_close):
        return []

    # Run once per 30-min slot
    current_slot = (now_ist.hour * 60 + now_ist.minute) // 30
    if current_slot == _exhaustion_last_check_slot:
        return []
    _exhaustion_last_check_slot = current_slot

    from_time = datetime.combine(now_ist.date(), market_open, tzinfo=IST)

    for name in EXHAUSTION_REVERSAL_WATCHLIST:
        try:
            ltp = _get_underlying_ltp_exh(kite, name)
            if ltp <= 0:
                continue

            futures_df = load_futures_data()
            token = None
            if futures_df is not None and not futures_df.empty:
                frow = futures_df[futures_df["name"] == name]
                if not frow.empty:
                    token = int(frow.iloc[0]["instrument_token"])
            if token is None:
                continue

            candles = get_historical_data_cached(
                kite, token, from_time, now_ist, "30minute"
            )
            if not candles or len(candles) < 5:
                continue

            # Work on completed candles only (exclude live last candle)
            completed = candles[:-1]
            if len(completed) < 4:
                continue

            # ---- Scan for pattern ----
            for setup_idx in range(len(completed) - 3):
                c1       = completed[setup_idx]
                c1_open  = float(c1.get("open",  0) or 0)
                c1_close = float(c1.get("close", 0) or 0)
                c1_vol   = int(c1.get("volume", 0) or 0)

                if c1_vol < EXH_SETUP_VOL:
                    continue
                is_c1_bearish = c1_close < c1_open

                # Count consecutive lower closes after Candle 1
                lower_close_end = setup_idx + 1
                prev_close = c1_close
                while lower_close_end < len(completed):
                    cn       = completed[lower_close_end]
                    cn_close = float(cn.get("close", 0) or 0)
                    cn_vol   = int(cn.get("volume", 0) or 0)
                    if cn_close < prev_close and cn_vol >= EXH_LOWER_VOL:
                        prev_close = cn_close
                        lower_close_end += 1
                    else:
                        break

                n_lower = lower_close_end - setup_idx - 1
                if n_lower < EXH_MIN_LOWER_CLOSES:
                    continue

                # Reversal candle right after lower closes
                rev_idx = lower_close_end
                if rev_idx >= len(completed):
                    continue

                rev      = completed[rev_idx]
                rev_o    = float(rev.get("open",  0) or 0)
                rev_c    = float(rev.get("close", 0) or 0)
                rev_h    = float(rev.get("high",  0) or 0)
                rev_l    = float(rev.get("low",   0) or 0)
                rev_vol  = int(rev.get("volume", 0) or 0)
                rev_time = rev.get("date")

                if rev_vol < EXH_REVERSAL_VOL:
                    continue

                rev_type = _classify_reversal_candle_30m(rev_o, rev_c, rev_h, rev_l)
                if not rev_type:
                    continue

                # Duplicate guard
                trig_key = (name, str(rev_time))
                if trig_key in _exhaustion_triggered:
                    continue

                # ---- Store in active watch for LIVE LTP monitoring ----
                options = _get_exhaustion_options(name, ltp)
                if not options:
                    continue

                rev_time_str = rev_time.strftime("%H:%M") if hasattr(rev_time, "strftime") else str(rev_time)
                _exhaustion_active_watch[name] = {
                    "rev_high":     rev_h,
                    "rev_low":      rev_l,
                    "rev_vol":      rev_vol,
                    "rev_type":     rev_type,
                    "rev_time_str": rev_time_str,
                    "trig_key":     trig_key,
                    "c1_open":      c1_open,
                    "c1_close":     c1_close,
                    "c1_vol":       c1_vol,
                    "is_c1_bearish": is_c1_bearish,
                    "n_lower":      n_lower,
                    "underlying_ltp": ltp,
                    "options":      options,
                }
                print(f"[ExhaustionReversal30M] Watch set for {name}: {rev_type} H={rev_h:.2f} at {rev_time_str}")
                break  # one pattern per underlying per slot

        except Exception as e:
            print(f"[ExhaustionReversal30M] Error scanning {name}: {e}")

    return []  # alerts fire via check_exhaustion_live_alerts (live LTP)


def check_exhaustion_live_alerts(kite):
    """Live minute-by-minute check. Fires alert as soon as underlying LTP
    crosses the Reversal candle High - no need to wait for candle close.
    Call this every minute from the historical scanner loop.
    """
    global _exhaustion_active_watch

    if not _exhaustion_active_watch:
        return []

    alerts = []
    now_ist = datetime.now(IST)
    triggered_names = []

    for name, watch in _exhaustion_active_watch.items():
        ltp = _get_underlying_ltp_exh(kite, name)
        if ltp <= 0:
            continue

        # Alert fires when live LTP crosses the Reversal candle High
        if ltp <= watch["rev_high"]:
            continue

        # Mark as triggered so the scanner doesn't re-add it
        _exhaustion_triggered.add(watch["trig_key"])
        triggered_names.append(name)

        rev_type  = watch["rev_type"]
        rev_emoji = {"HAMMER": "\U0001f528", "DOJI": "\u26a1", "REJECTION": "\U0001f53b"}.get(rev_type, "\u26a1")

        for opt in watch["options"]:
            clean_sym = opt["symbol"].split(":", 1)[1] if ":" in opt["symbol"] else opt["symbol"]
            c1_dir = "\U0001f4c9 Bearish" if watch["is_c1_bearish"] else "\U0001f4c8"
            alert_msg = (
                f"\U0001f525 EXHAUSTION REVERSAL ({rev_type}) {rev_emoji}\n"
                f"\U0001f6a8 OPTION BUY ({opt['option_type']}) \U0001f4c8\n"
                f"Symbol: {clean_sym} ({opt['itm_type']})\n"
                f"Underlying: {name} @ {ltp:.2f} \U0001f680 (Crossed {rev_type} H: {watch['rev_high']:.2f})\n"
                f"Expiry: {opt['expiry_text']}\n"
                f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
                f"Setup Candle  : {watch['c1_open']:.2f}\u2192{watch['c1_close']:.2f} | Vol: {watch['c1_vol']//1000}k {c1_dir}\n"
                f"Lower Closes  : {watch['n_lower']} candles (Vol \u2265 {EXH_LOWER_VOL//1000}k each)\n"
                f"{rev_type} Candle : H={watch['rev_high']:.2f} L={watch['rev_low']:.2f} | Vol: {watch['rev_vol']//1000}k | {watch['rev_time_str']} IST\n"
                f"LIVE CROSS    : LTP {ltp:.2f} > {rev_type} High {watch['rev_high']:.2f} \u2705\n"
                f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
                f"TIME: {now_ist.strftime('%H:%M:%S')} IST"
            )
            alerts.append(alert_msg)

    # Remove triggered entries
    for name in triggered_names:
        _exhaustion_active_watch.pop(name, None)

    return alerts
