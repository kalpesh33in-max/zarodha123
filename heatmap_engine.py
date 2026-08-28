import os
import time
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
direction_engine = None
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
STOCK_BURST_NAMES = set()
NSE_BURST_TRACK_NAMES = []
MCX_BURST_TRACK_NAMES = [
    "CRUDEOILM"
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
STOCK_BURST_STRIKES_BELOW_ATM = 10
STOCK_BURST_STRIKES_ABOVE_ATM = 10
BANKNIFTY_BURST_STRIKES_BELOW_ATM = 20
BANKNIFTY_BURST_STRIKES_ABOVE_ATM = 20
BURST_THRESHOLD_LOTS = int(os.getenv("BURST_THRESHOLD_LOTS", "100"))
OPTION_BURST_THRESHOLD_LOTS = int(os.getenv("OPTION_BURST_THRESHOLD_LOTS", "100"))
FUTURE_BURST_THRESHOLD_LOTS = int(os.getenv("FUTURE_BURST_THRESHOLD_LOTS", "2000"))
BANKNIFTY_OPTION_BURST_THRESHOLD_LOTS = int(os.getenv("BANKNIFTY_OPTION_BURST_THRESHOLD_LOTS", "100"))
BANKNIFTY_HIGH_PREMIUM_PRICE = float(os.getenv("BANKNIFTY_HIGH_PREMIUM_PRICE", "1500"))
BANKNIFTY_HIGH_PREMIUM_THRESHOLD_LOTS = int(os.getenv("BANKNIFTY_HIGH_PREMIUM_THRESHOLD_LOTS", "100"))
INDEX_BURST_THRESHOLD_LOTS = int(os.getenv("INDEX_OPTION_BURST_THRESHOLD_LOTS", str(OPTION_BURST_THRESHOLD_LOTS)))
STOCK_BURST_THRESHOLD_LOTS = int(os.getenv("STOCK_OPTION_BURST_THRESHOLD_LOTS", "100"))
MCX_BURST_THRESHOLD_LOTS = int(os.getenv("MCX_OPTION_BURST_THRESHOLD_LOTS", "100"))
INDEX_FUTURE_BURST_THRESHOLD_LOTS = int(os.getenv("INDEX_FUTURE_BURST_THRESHOLD_LOTS", str(FUTURE_BURST_THRESHOLD_LOTS)))
STOCK_FUTURE_BURST_THRESHOLD_LOTS = int(os.getenv("STOCK_FUTURE_BURST_THRESHOLD_LOTS", str(FUTURE_BURST_THRESHOLD_LOTS)))
MCX_FUTURE_BURST_THRESHOLD_LOTS = int(os.getenv("MCX_FUTURE_BURST_THRESHOLD_LOTS", "500"))
BURST_REST_FALLBACK_CACHE_SECONDS = int(os.getenv("BURST_REST_FALLBACK_CACHE_SECONDS", "3"))
DEBUG_BURST_PRICE_NORMALIZATION = os.getenv("DEBUG_BURST_PRICE_NORMALIZATION", "false").lower() in ("true", "1", "yes", "on")
DEBUG_BURST_STRIKES = os.getenv("DEBUG_BURST_STRIKES", "false").lower() in ("true", "1", "yes", "on")
INDEX_SYMBOL = "NSE:NIFTY BANK"
INDEX_FUTURE_NAMES = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX", "SENSEX50"}
# Expiry rollover policy.  A contract expiring today is treated as expired so
# the scanner moves to the next monthly contract from the next session.  The
# default of one day also makes the scanner ignore the just-expired contract
# when it is started on the following day.
EXPIRY_ROLLOVER_DAYS = int(os.getenv("EXPIRY_ROLLOVER_DAYS", "7"))

day_open_oi_store = {}
option_history = {}
active_watches = {}
gap_alert_store = {}
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

NON_BURST_ALERT_PAUSE_DATES = {"2026-05-26"}


def is_index_underlying(name):
    return name in INDEX_BURST_NAMES


def is_mcx_underlying(name):
    return name in MCX_BURST_NAMES


def is_burst_underlying(name):
    return name in INDEX_BURST_NAMES or name in STOCK_BURST_NAMES or is_mcx_underlying(name)


def get_option_burst_threshold(name):
    if name == "CRUDEOILM":
        return 25
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
    if name == "CRUDEOILM":
        return 25
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
    return STOCK_BURST_STRIKES_BELOW_ATM, 0


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
        return ["CRUDEOILM"]
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
            return ["CRUDEOILM"]

    return sorted(set(INDEX_BURST_NAMES) | set(STOCK_BURST_NAMES))


def non_burst_alerts_paused_today():
    return datetime.now(IST).date().isoformat() in NON_BURST_ALERT_PAUSE_DATES


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
    """Remove expired contracts from the in-memory instrument data."""
    if df is None or df.empty or "expiry" not in df.columns:
        return df
    now_ist = datetime.now(IST)
    cutoff = pd.Timestamp(now_ist.date())
    return df[df["expiry"].notna() & (df["expiry"] >= cutoff)].copy()



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
    name = str(row.get("name", "") or "")
    tradingsymbol = str(row.get("tradingsymbol", "") or "")
    if name == "CRUDEOILM" or tradingsymbol.startswith("CRUDEOILM"):
        return 10
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


VOLUME_MISMATCH_WATCHLIST = [
    # Indices
    "NIFTY", "SENSEX", "BANKNIFTY", "MIDCPNIFTY",
    # Commodities (MCX)
    "CRUDEOIL", "CRUDEOILM", "NATURALGAS", "NATGASMINI",
    # Stocks with Lot Size <= 550
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "BAJAJ-AUTO",
    "BAJAJFINSV", "BHARTIARTL", "BRITANNIA", "CIPLA", "EICHERMOT",
    "GRASIM", "HCLTECH", "HEROMOTOCO", "HINDUNILVR", "INFY",
    "LT", "M&M", "MARUTI", "NESTLEIND", "RELIANCE",
    "SBILIFE", "SUNPHARMA", "TATACONSUM", "TCS", "TITAN",
    "TRENT", "ULTRACEMCO"
]

_volume_mismatch_triggered_slots = set()


def _get_active_future_for_mismatch(name):
    df = load_futures_data()
    if df is None or df.empty:
        return None
    rows = df[df["name"] == name]
    if rows.empty:
        return None
    preferred_expiry = get_target_monthly_expiry(rows["expiry"].unique())
    if preferred_expiry is None:
        return None
    selected = rows[rows["expiry"] == preferred_expiry]
    if selected.empty:
        return None
    row = selected.iloc[0]
    exchange = str(row.get("exchange", "") or "").strip() or "NFO"
    return {
        "name": name,
        "symbol": f"{exchange}:{row['tradingsymbol']}",
        "token": int(row["instrument_token"]),
        "tradingsymbol": str(row["tradingsymbol"]),
    }


def _build_timeframe_volume_mismatch_table(kite, interval_label, interval_code, slot_time_str, now_ist):
    market_open = datetime.strptime("09:15", "%H:%M").time()
    from_day = _get_previous_trading_day(now_ist)
    from_time = datetime.combine(from_day, market_open, tzinfo=IST)
    to_time = now_ist

    rows = []

    for name in VOLUME_MISMATCH_WATCHLIST:
        contract = _get_active_future_for_mismatch(name)
        if not contract:
            continue

        token = contract["token"]
        try:
            candles = get_historical_data_cached(kite, token, from_time, to_time, interval_code)
        except Exception as e:
            print(f"Volume mismatch data error for {name} ({interval_code}): {e}")
            continue

        if not candles:
            continue

        today_candles = []
        prev_day_candles = []
        for c in candles:
            c_date = c.get("date")
            if c_date is None:
                continue
            if hasattr(c_date, "astimezone"):
                c_date = c_date.astimezone(IST)
            if c_date.date() == now_ist.date():
                today_candles.append(c)
            elif c_date.date() < now_ist.date():
                prev_day_candles.append(c)

        if not today_candles:
            continue

        # Opening completed candle of today
        completed_candle = today_candles[0]
        
        # Prior candle from previous trading day's close
        prev_candle = prev_day_candles[-1] if prev_day_candles else None

        o = float(completed_candle.get("open", 0) or 0)
        c = float(completed_candle.get("close", 0) or 0)
        vol = float(completed_candle.get("volume", 0) or 0)
        if o <= 0 or c <= 0:
            continue

        # 1. Price Candle Color: Green if Close > Open, Red if Close < Open
        if c > o:
            price_candle = "🟢"
        elif c < o:
            price_candle = "🔴"
        else:
            price_candle = "⚪"

        # 2. Volume Candle Color: Green if Close > Prev Candle Close, Red if Close < Prev Candle Close
        if prev_candle:
            prev_c = float(prev_candle.get("close", 0) or 0)
            prev_vol = float(prev_candle.get("volume", 0) or 0)
            if prev_c > 0:
                if c > prev_c:
                    volume_candle = "🟢"
                elif c < prev_c:
                    volume_candle = "🔴"
                else:
                    volume_candle = "⚪"
            else:
                volume_candle = "⚪"
        else:
            prev_vol = 0.0
            volume_candle = "⚪"

        # 3. Gap Status: Day's Open vs Previous Day Close
        day_open = o
        prev_day_close = float(prev_day_candles[-1].get("close", 0) or 0) if prev_day_candles else 0.0

        if prev_day_close > 0 and day_open > 0:
            if day_open > prev_day_close:
                gap_status = "🔼"
            elif day_open < prev_day_close:
                gap_status = "🔽"
            else:
                gap_status = "🟰"
        else:
            gap_status = "🟰"

        # 4. Mismatch Check (Mandatory: Price Candle != Volume Candle)
        is_mismatch = (
            price_candle != volume_candle
            and price_candle in ("🟢", "🔴")
            and volume_candle in ("🟢", "🔴")
        )

        if is_mismatch:
            rows.append({
                "name": name,
                "price_candle": price_candle,
                "volume_candle": volume_candle,
                "gap_status": gap_status,
                "is_mismatch": is_mismatch,
            })

    if not rows:
        return None

    msg = f"📊 *{interval_label} VOLUME MISMATCH*\n"
    msg += f"⏰ Time: {now_ist.strftime('%H:%M:%S')} IST (Slot: {slot_time_str})\n\n"
    msg += "```\n"
    msg += "Future        | Price | Volume |  Gap \n"
    msg += "--------------+-------+--------+------\n"
    for r in rows:
        msg += f"{r['name']:<14}|   {r['price_candle']}  |   {r['volume_candle']}   |  {r['gap_status']} \n"
    msg += "```"
    return msg


def build_volume_mismatch_alerts(kite):
    now_ist = datetime.now(IST)
    if now_ist.weekday() > 4:
        return []

    from env_config import NSE_HOLIDAYS
    if now_ist.date().isoformat() in NSE_HOLIDAYS:
        return []

    t = now_ist.time()
    market_open = datetime.strptime("09:15", "%H:%M").time()
    market_close = datetime.strptime("15:30", "%H:%M").time()
    if not (market_open <= t <= market_close):
        return []

    alerts = []
    minute = now_ist.minute
    hour = now_ist.hour
    date_str = now_ist.date().isoformat()

    timeframes = []

    # 1. FIRST 5-MIN: Triggered at 09:20 IST
    if hour == 9 and minute == 20:
        timeframes.append(("FIRST 5MIN", "5minute", "09:20", f"FIRST_5M_{date_str}"))

    # 2. FIRST 15-MIN: Triggered at 09:30 IST
    if hour == 9 and minute == 30:
        timeframes.append(("FIRST 15MIN", "15minute", "09:30", f"FIRST_15M_{date_str}"))

    # 3. FIRST 30-MIN: Triggered at 09:45 IST
    if hour == 9 and minute == 45:
        timeframes.append(("FIRST 30MIN", "30minute", "09:45", f"FIRST_30M_{date_str}"))

    # 4. FIRST 60-MIN: Triggered at 10:15 IST
    if hour == 10 and minute == 15:
        timeframes.append(("FIRST 60MIN", "60minute", "10:15", f"FIRST_60M_{date_str}"))

    for label, code, slot_str, trigger_key in timeframes:
        if trigger_key in _volume_mismatch_triggered_slots:
            continue
        table_msg = _build_timeframe_volume_mismatch_table(kite, label, code, slot_str, now_ist)
        if table_msg:
            _volume_mismatch_triggered_slots.add(trigger_key)
            alerts.append(table_msg)

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





def _get_previous_trading_day(now_ist):
    day = now_ist.date() - timedelta(days=1)
    while day.weekday() > 4:
        day -= timedelta(days=1)
    return day


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

    if prev_oi > 0 and oi > 0:
        tick_lots = int(abs(oi - prev_oi) / lot_size)
        if stats is not None:
            stats["max_future_tick_lots"] = max(
                stats.get("max_future_tick_lots", 0),
                tick_lots,
            )
        trigger_threshold = 25 if name == "CRUDEOILM" else 100
        if tick_lots >= trigger_threshold and key not in active_watches:
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
            if oi <= 0:
                # Discard zero/expired ticks
                del active_watches[key]
            else:
                oi_chg = oi - watch["start_oi"]
                p_chg = ltp - watch["start_price"]
                final_lot_size = _normalize_lot_size(watch.get("lot_size")) or lot_size
                final_lots = int(abs(oi_chg) / final_lot_size)
                
                action = classify_action(watch["symbol"], oi_chg, p_chg)
                is_covering_unwinding = any(x in action for x in ["COVERING", "UNWINDING"])
                
                if watch["name"] == "CRUDEOILM":
                    req_threshold = 100 if is_covering_unwinding else 25
                else:
                    req_threshold = 500 if is_covering_unwinding else 100
                    
                if final_lots >= req_threshold:
                    strength = get_strength_label(final_lots, watch["name"])
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

    if oi > 0:
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

        if curr_oi > 0 and t_int not in day_open_oi_store:
            day_open_oi_store[t_int] = curr_oi

        if t_int not in option_history:
            option_history[t_int] = []
        history = option_history[t_int]
        prev_oi = history[-1]["oi"] if history else 0
        prev_price = history[-1]["price"] if history else 0

        if prev_oi > 0 and curr_oi > 0:
            tick_lots = int(abs(curr_oi - prev_oi) / lot_size)
            if stats is not None:
                stats["max_option_tick_lots"] = max(
                    stats.get("max_option_tick_lots", 0),
                    tick_lots,
                )
            trigger_threshold = 25 if name == "CRUDEOILM" else 100
            if tick_lots >= trigger_threshold and t_int not in active_watches:
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
                if curr_oi <= 0:
                    # Discard zero/expired ticks
                    del active_watches[t_int]
                else:
                    oi_chg = curr_oi - watch["start_oi"]
                    p_chg = ltp - watch["start_price"]
                    final_lot_size = _normalize_lot_size(watch.get("lot_size")) or lot_size
                    final_lots = int(abs(oi_chg) / final_lot_size)
                    
                    action = classify_action(watch["symbol"], oi_chg, p_chg)
                    is_covering_unwinding = any(x in action for x in ["COVERING", "UNWINDING"])
                    
                    if watch["underlying"] == "CRUDEOILM":
                        final_threshold = 100 if is_covering_unwinding else 25
                    else:
                        final_threshold = 500 if is_covering_unwinding else 100
                        
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

        if curr_oi > 0:
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
    # Gap Scanner paused / held until further instruction
    return []


def calculate_historical_alerts(kite):
    alerts = []
    alerts.extend(calculate_first_60m_alerts(kite))
    alerts.extend(calculate_other_historical_alerts(kite))
    return alerts


def calculate_first_60m_alerts(kite):
    if non_burst_alerts_paused_today():
        return []

    return build_volume_mismatch_alerts(kite)


def calculate_other_historical_alerts(kite):
    return []


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
    return 0, "", bn_alerts, stock_alerts, gap_alerts
