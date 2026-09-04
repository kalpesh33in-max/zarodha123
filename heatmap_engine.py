import os
import time
import math
import threading
import requests
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
direction_engine = None
from kite_rate_limiter import kite_historical_data, kite_quote
from websocket_flow import get_symbol_quotes, get_token_quotes, register_ws_callbacks, add_shared_tokens
from telegram_utils import send_telegram_message

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
    "HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "KOTAKBANK",
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "BAJAJ-AUTO",
    "BAJAJFINSV", "BHARTIARTL", "BRITANNIA", "CIPLA", "EICHERMOT",
    "GRASIM", "HCLTECH", "HEROMOTOCO", "HINDUNILVR", "INFY",
    "LT", "M&M", "MARUTI", "NESTLEIND", "RELIANCE",
    "SBILIFE", "SUNPHARMA", "TATACONSUM", "TCS", "TITAN",
    "TRENT", "ULTRACEMCO"
}
NSE_BURST_TRACK_NAMES = sorted(INDEX_BURST_NAMES | STOCK_BURST_NAMES)
MCX_BURST_TRACK_NAMES = ["CRUDEOILM"]
MCX_BURST_NAMES = {"CRUDEOILM"}
BURST_TRACK_NAMES = sorted(INDEX_BURST_NAMES | STOCK_BURST_NAMES | MCX_BURST_NAMES)
ENABLE_INDEX_BURST_ALERTS = os.getenv("ENABLE_INDEX_BURST_ALERTS", "true").lower() in (
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
MCX_BURST_STRIKES_BELOW_ATM = 10
MCX_BURST_STRIKES_ABOVE_ATM = 10
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
BURST_REST_FALLBACK_CACHE_SECONDS = int(os.getenv("BURST_REST_FALLBACK_CACHE_SECONDS", "10"))
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
    if is_mcx_underlying(name):
        return MCX_BURST_STRIKES_BELOW_ATM, 0
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
    # 5 Major Banking Stocks
    "HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "KOTAKBANK",
    # Stocks with Lot Size <= 550
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "BAJAJ-AUTO",
    "BAJAJFINSV", "BHARTIARTL", "BRITANNIA", "CIPLA", "EICHERMOT",
    "GRASIM", "HCLTECH", "HEROMOTOCO", "HINDUNILVR", "INFY",
    "LT", "M&M", "MARUTI", "NESTLEIND", "RELIANCE",
    "SBILIFE", "SUNPHARMA", "TATACONSUM", "TCS", "TITAN",
    "TRENT", "ULTRACEMCO"
]

_volume_mismatch_triggered_slots = set()


def _get_mismatch_instruments(name):
    """Returns future contract and (if stock) spot equity contract."""
    df_fut = load_futures_data()
    df_all = _load_instruments_df()
    
    instruments = []
    # 1. Monthly Future Contract
    if df_fut is not None and not df_fut.empty:
        rows = df_fut[df_fut["name"] == name]
        if not rows.empty:
            preferred_expiry = get_target_monthly_expiry(rows["expiry"].unique())
            if preferred_expiry is not None:
                selected = rows[rows["expiry"] == preferred_expiry]
                if not selected.empty:
                    row = selected.iloc[0]
                    exchange = str(row.get("exchange", "") or "").strip() or "NFO"
                    label = f"{name}(F)"
                    instruments.append({
                        "label": label,
                        "token": int(row["instrument_token"]),
                        "is_spot": False
                    })

    # 2. Spot Contract for individual Stocks
    is_index = name in {"NIFTY", "SENSEX", "BANKNIFTY", "MIDCPNIFTY", "CRUDEOIL", "CRUDEOILM", "NATURALGAS", "NATGASMINI"}
    if not is_index and df_all is not None and not df_all.empty:
        spots = df_all[(df_all["tradingsymbol"] == name) & (df_all["segment"] == "NSE")]
        if not spots.empty:
            spot_row = spots.iloc[0]
            label = f"{name}(S)"
            instruments.append({
                "label": label,
                "token": int(spot_row["instrument_token"]),
                "is_spot": True
            })

    return instruments


def _build_timeframe_volume_mismatch_table(kite, interval_label, interval_code, slot_time_str, now_ist):
    market_open = datetime.strptime("09:15", "%H:%M").time()
    from_day = _get_previous_trading_day(now_ist)
    from_time = datetime.combine(from_day, market_open, tzinfo=IST)
    to_time = now_ist

    rows = []

    for name in VOLUME_MISMATCH_WATCHLIST:
        items = _get_mismatch_instruments(name)
        for item in items:
            label = item["label"]
            token = item["token"]

            try:
                candles = get_historical_data_cached(kite, token, from_time, to_time, interval_code)
            except Exception as e:
                print(f"Volume mismatch data error for {label} ({interval_code}): {e}")
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

            completed_candle = today_candles[0]
            prev_candle = prev_day_candles[-1] if prev_day_candles else None

            o = float(completed_candle.get("open", 0) or 0)
            h = float(completed_candle.get("high", 0) or 0)
            l = float(completed_candle.get("low", 0) or 0)
            c = float(completed_candle.get("close", 0) or 0)

            if o <= 0 or c <= 0 or h <= 0 or l <= 0:
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
                volume_candle = "⚪"

            # 3. Gap Status: Day's Open vs Previous Day Close
            prev_day_close = float(prev_day_candles[-1].get("close", 0) or 0) if prev_day_candles else 0.0
            if prev_day_close > 0 and o > 0:
                if o > prev_day_close:
                    gap_status = "🔼"
                elif o < prev_day_close:
                    gap_status = "🔽"
                else:
                    gap_status = "🟰"
            else:
                gap_status = "🟰"

            # 4. CS (Candle Status: OH, OL, NO)
            # Tolerance for float comparison
            if abs(o - h) <= 1e-4:
                cs_status = "OH"
            elif abs(o - l) <= 1e-4:
                cs_status = "OL"
            else:
                cs_status = "NO"

            # 5. Mismatch Check (Mandatory: Price Candle != Volume Candle)
            is_mismatch = (
                price_candle != volume_candle
                and price_candle in ("🟢", "🔴")
                and volume_candle in ("🟢", "🔴")
            )

            if is_mismatch:
                rows.append({
                    "script": label,
                    "price_candle": price_candle,
                    "volume_candle": volume_candle,
                    "gap_status": gap_status,
                    "cs": cs_status,
                    "is_mismatch": is_mismatch,
                })

    if not rows:
        return None

    msg = f"📊 *{interval_label} VOLUME MISMATCH*\n"
    msg += f"⏰ Time: {now_ist.strftime('%H:%M:%S')} IST (Slot: {slot_time_str})\n\n"
    msg += "```\n"
    msg += "SCRIPT        | PRICE | VOLUME | GAP | CS \n"
    msg += "--------------+-------+--------+-----+----\n"
    for r in rows:
        msg += f"{r['script']:<14}|   {r['price_candle']}  |   {r['volume_candle']}   |  {r['gap_status']} | {r['cs']:<3}\n"
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


def process_future_burst(kite, token, symbol, name, ltp, oi, alerts_list, volume=0, stats=None):
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
                volume=volume or oi,
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
    prev_vol = history[-1].get("vol", 0) if history else 0

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
        trigger_threshold = 25 if name == "CRUDEOILM" else min(50, threshold)
        if tick_lots >= trigger_threshold and key not in active_watches:
            active_watches[key] = {
                "start_oi": prev_oi,
                "start_price": prev_price,
                "start_vol": volume,
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
                vol_traded = max(0, volume - watch.get("start_vol", volume))
                final_lot_size = _normalize_lot_size(watch.get("lot_size")) or lot_size
                final_lots = int(abs(oi_chg) / final_lot_size)
                
                action = classify_action(watch["symbol"], oi_chg, p_chg)
                is_covering_unwinding = any(x in action for x in ["COVERING", "UNWINDING"])
                
                if watch["name"] == "CRUDEOILM":
                    req_threshold = 100 if is_covering_unwinding else 25
                else:
                    req_threshold = 500 if is_covering_unwinding else 100
                    
                # Ensure actual trading volume occurred (at least 5 lots) to discard connection baseline jumps
                vol_lots = int(vol_traded / final_lot_size) if final_lot_size > 0 else vol_traded
                if final_lots >= req_threshold and (vol_lots >= 5 or vol_traded == 0):
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
        history.append({"time": now, "oi": oi, "price": ltp, "vol": volume})
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
            trigger_threshold = 25 if name == "CRUDEOILM" else min(50, threshold)
            if tick_lots >= trigger_threshold and t_int not in active_watches:
                expiry_text = (
                    row["expiry"].strftime("%d-%m-%Y")
                    if pd.notna(row.get("expiry"))
                    else "NA"
                )
                active_watches[t_int] = {
                    "start_oi": prev_oi,
                    "start_price": prev_price,
                    "start_vol": volume,
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
                    vol_traded = max(0, volume - watch.get("start_vol", volume))
                    final_lot_size = _normalize_lot_size(watch.get("lot_size")) or lot_size
                    final_lots = int(abs(oi_chg) / final_lot_size)
                    
                    action = classify_action(watch["symbol"], oi_chg, p_chg)
                    is_covering_unwinding = any(x in action for x in ["COVERING", "UNWINDING"])
                    
                    if watch["underlying"] == "CRUDEOILM":
                        final_threshold = 100 if is_covering_unwinding else 25
                    else:
                        final_threshold = 500 if is_covering_unwinding else 100
                        
                    # Volume confirmation: Ensure actual trading volume occurred
                    vol_lots = int(vol_traded / final_lot_size) if final_lot_size > 0 else vol_traded
                    if final_lots >= final_threshold and (vol_lots >= 5 or vol_traded == 0):
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
            history.append({"time": now, "oi": curr_oi, "price": ltp, "vol": volume})
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
    # Only invoke expensive REST fallback if we have NO WebSocket quotes at all (e.g. during initial startup)
    if all_opt_tokens and quote_source == "rest_fallback" and not opt_quotes:
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
        oi_val = d.get("oi", 0)
        vol_val = d.get("volume", 0)
        target_alerts = bn_alerts if is_index_underlying(name) else stock_alerts

        process_future_burst(kite, d['instrument_token'], sym, name, ltp, oi_val, target_alerts, volume=vol_val, stats=stats)
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


# ==============================================================================
# ================= UNIFIED REAL-TIME SCANNERS & ALERT ENGINES =================
# ==============================================================================

# Helper: Read access token
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


# ------------------------------------------------------------------------------
# 1. SPOT & FUTURE VOLUME SCANNER (1-Minute Spikes + ATM OI Table)
# ------------------------------------------------------------------------------
_spot_vol_state_lock = threading.Lock()
_spot_vol_candle_state = {}
_spot_vol_symbol_metadata = {}

def _reset_spot_candle_state(tkn, current_vol):
    _spot_vol_candle_state[tkn] = {
        "start_vol": current_vol,
        "high": -1.0,
        "low": float('inf'),
        "close": -1.0
    }

def start_spot_volume_scanner(kite=None):
    """Tracks Spot & Future volume anomalies on the single shared WebSocket feed."""
    print("Starting Spot & Future Volume Scanner Engine...")
    import env_config
    from kiteconnect import KiteConnect

    token = _get_access_token()
    if not kite and token:
        try:
            kite = KiteConnect(api_key=env_config.API_KEY)
            kite.set_access_token(token)
        except Exception as e:
            print("Failed to initialize Kite for Spot Scanner:", e)
            kite = None

    df = _load_instruments_df()
    if df.empty:
        return

    target_tokens = []
    global _spot_vol_symbol_metadata
    _spot_vol_symbol_metadata.clear()

    # 1. Focus Stocks: BOTH Spot and Future
    for name in STOCK_BURST_NAMES:
        futs = df[(df["name"] == name) & (df["instrument_type"] == "FUT")]
        if futs.empty:
            continue
        futs = futs.sort_values(by="expiry")
        fut = futs.iloc[0]
        lot_size = int(fut.get("lot_size", 1))
        fut_tkn = int(fut["instrument_token"])
        target_tokens.append(fut_tkn)

        spot_tkn = None
        spots = df[(df["tradingsymbol"] == name) & (df["segment"] == "NSE")]
        if not spots.empty:
            spot = spots.iloc[0]
            spot_tkn = int(spot["instrument_token"])
            target_tokens.append(spot_tkn)

        opts = df[(df["name"] == name) & (df["instrument_type"].isin(["CE", "PE"]))]
        strike_step = 50
        opts_df = pd.DataFrame()
        if not opts.empty:
            sample_strikes = sorted(opts["strike"].unique())
            strike_step = sample_strikes[1] - sample_strikes[0] if len(sample_strikes) > 1 else 50
            closest_expiry = opts["expiry"].min()
            opts_df = opts[opts["expiry"] == closest_expiry]

        _spot_vol_symbol_metadata[name] = {
            "spot_tkn": spot_tkn,
            "fut_tkn": fut_tkn,
            "lot_size": lot_size,
            "is_mcx": False,
            "is_stock": True,
            "symbol": fut["tradingsymbol"],
            "strike_step": strike_step,
            "opts_df": opts_df
        }

    # 2. Indices (BANKNIFTY)
    INDEX_TARGETS = ["BANKNIFTY"]
    DEFAULT_STRIKE_STEPS = {"BANKNIFTY": 100, "CRUDEOILM": 50}
    spot_index_map = {"BANKNIFTY": "NIFTY BANK"}

    for name in INDEX_TARGETS:
        futs = df[(df["name"] == name) & (df["instrument_type"] == "FUT")]
        if not futs.empty:
            futs = futs.sort_values(by="expiry")
            fut = futs.iloc[0]
            lot_size = int(fut.get("lot_size", 1))
            tkn = int(fut["instrument_token"])
            target_tokens.append(tkn)

            spot_name = spot_index_map.get(name)
            spots = df[(df["tradingsymbol"] == spot_name) & (df["segment"] == "INDICES")]
            spot_tkn = int(spots.iloc[0]["instrument_token"]) if not spots.empty else None
            if spot_tkn:
                target_tokens.append(spot_tkn)

            opts = df[(df["name"] == name) & (df["instrument_type"].isin(["CE", "PE"]))]
            strike_step = DEFAULT_STRIKE_STEPS.get(name, 50)
            opts_df = pd.DataFrame()
            if not opts.empty:
                closest_expiry = opts["expiry"].min()
                opts_df = opts[opts["expiry"] == closest_expiry]

            _spot_vol_symbol_metadata[name] = {
                "spot_tkn": spot_tkn,
                "fut_tkn": tkn,
                "lot_size": lot_size,
                "is_mcx": False,
                "is_stock": False,
                "symbol": fut["tradingsymbol"],
                "strike_step": strike_step,
                "opts_df": opts_df
            }

    # 3. Commodities (CRUDEOILM)
    for name in MCX_BURST_NAMES:
        futs = df[(df["name"] == name) & (df["instrument_type"] == "FUT")]
        if not futs.empty:
            futs = futs.sort_values(by="expiry")
            fut = futs.iloc[0]
            lot_size = 10 if name == "CRUDEOILM" else int(fut.get("lot_size", 1))
            tkn = int(fut["instrument_token"])
            target_tokens.append(tkn)

            opts = df[(df["name"] == name) & (df["instrument_type"].isin(["CE", "PE"]))]
            strike_step = 50
            opts_df = pd.DataFrame()
            if not opts.empty:
                sample_strikes = sorted(opts["strike"].unique())
                strike_step = sample_strikes[1] - sample_strikes[0] if len(sample_strikes) > 1 else 50
                closest_expiry = opts["expiry"].min()
                opts_df = opts[opts["expiry"] == closest_expiry]

            _spot_vol_symbol_metadata[name] = {
                "spot_tkn": None,
                "fut_tkn": tkn,
                "lot_size": lot_size,
                "is_mcx": True,
                "is_stock": False,
                "symbol": fut["tradingsymbol"],
                "strike_step": strike_step,
                "opts_df": opts_df
            }

    if not target_tokens:
        print("[SPOT SCANNER] No targets configured.")
        return

    print(f"[SPOT SCANNER] Tracking {len(target_tokens)} Spot/Future instruments on shared WebSocket.")

    def on_ticks(ws, ticks):
        with _spot_vol_state_lock:
            for tick in ticks:
                tkn = tick["instrument_token"]
                ltp = tick["last_price"]
                vol = tick.get("volume_traded") or tick.get("volume", 0)
                oi = tick.get("oi", 0)

                if tkn not in _spot_vol_candle_state:
                    _reset_spot_candle_state(tkn, vol)

                c_state = _spot_vol_candle_state[tkn]
                c_state["close"] = ltp
                if c_state["high"] == -1.0 or ltp > c_state["high"]:
                    c_state["high"] = ltp
                if c_state["low"] == float('inf') or ltp < c_state["low"]:
                    c_state["low"] = ltp
                c_state["current_vol"] = vol
                c_state["oi"] = oi

    def on_connect(ws, response):
        add_shared_tokens(target_tokens)

    register_ws_callbacks(on_connect, on_ticks)
    add_shared_tokens(target_tokens)

    def reporting_loop():
        import env_config
        last_reported_minute = None

        while True:
            time.sleep(0.5)
            now = datetime.now(IST)
            if now.weekday() > 4:
                time.sleep(60)
                continue

            t = now.time()
            is_nse_holiday = now.date().isoformat() in env_config.NSE_HOLIDAYS
            is_nse_open = datetime.strptime("09:00", "%H:%M").time() <= t <= datetime.strptime("15:30", "%H:%M").time() and not is_nse_holiday
            is_mcx_open = datetime.strptime("15:30", "%H:%M").time() <= t <= datetime.strptime("23:30", "%H:%M").time()

            if not is_nse_open and not is_mcx_open:
                time.sleep(60)
                continue

            current_minute = now.strftime("%Y-%m-%d %H:%M")
            if now.second >= 2 and current_minute != last_reported_minute:
                last_reported_minute = current_minute
                alerts = []

                def format_vol(v):
                    if v >= 1_000_000:
                        val = v / 1_000_000
                        return f"{int(val)}M" if val.is_integer() else f"{val:.1f}M"
                    elif v >= 1_000:
                        val = v / 1_000
                        return f"{int(val)}K" if val.is_integer() else f"{val:.1f}K"
                    return str(int(v))

                with _spot_vol_state_lock:
                    for name, meta in _spot_vol_symbol_metadata.items():
                        is_mcx = meta["is_mcx"]
                        if is_mcx and not is_mcx_open:
                            continue
                        if not is_mcx and not is_nse_open:
                            for tkn in [meta["spot_tkn"], meta["fut_tkn"]]:
                                if tkn and tkn in _spot_vol_candle_state:
                                    _reset_spot_candle_state(tkn, _spot_vol_candle_state[tkn].get("current_vol", 0))
                            continue

                        spot_tkn = meta["spot_tkn"]
                        fut_tkn = meta["fut_tkn"]
                        lot_size = meta["lot_size"]

                        spot_state = _spot_vol_candle_state.get(spot_tkn) if spot_tkn else None
                        fut_state = _spot_vol_candle_state.get(fut_tkn) if fut_tkn else None

                        spot_vol = 0
                        spot_lots = 0
                        fut_vol = 0
                        fut_lots = 0

                        spot_valid = spot_state and spot_state["high"] != -1.0
                        fut_valid = fut_state and fut_state["high"] != -1.0

                        if spot_valid:
                            spot_vol = max(0, spot_state.get("current_vol", 0) - spot_state.get("start_vol", 0))
                            spot_lots = int(spot_vol / lot_size)

                        if fut_valid:
                            fut_vol = max(0, fut_state.get("current_vol", 0) - fut_state.get("start_vol", 0))
                            fut_lots = int(fut_vol / lot_size)

                        required_lots = 150 if name == "CRUDEOILM" else 500
                        if spot_lots >= required_lots or fut_lots >= required_lots:
                            oi_table = ""
                            ref_price = 0

                            if meta["is_stock"] and spot_tkn:
                                s_high = spot_state["high"] if spot_valid else 0
                                s_low = spot_state["low"] if spot_valid else 0
                                s_close = spot_state["close"] if spot_valid else 0
                                s_mid = (s_high - s_low) / 2.0 if spot_valid else 0
                                buy_price = s_low + s_mid if spot_valid else 0
                                ref_price = buy_price

                                f_high = fut_state["high"] if fut_valid else 0
                                f_low = fut_state["low"] if fut_valid else 0
                                f_close = fut_state["close"] if fut_valid else 0
                                f_mid = (f_high - f_low) / 2.0 if fut_valid else 0

                                msg = (
                                    f"Symbol: {meta['symbol']} ({lot_size} lots)\n"
                                    f"S-V(L): {format_vol(spot_vol)}({spot_lots} L) & F-V(L): {format_vol(fut_vol)}({fut_lots} L)\n"
                                    f"S-Price: {s_close:.2f} F-Price: {f_close:.2f}\n"
                                    f"S-Candle C: {s_mid:.2f} FC: {f_mid:.2f}\n"
                                    f"Buying Price: {buy_price:.2f}\n"
                                )
                            else:
                                price_source = spot_state if spot_valid else fut_state
                                c_high = price_source["high"]
                                c_low = price_source["low"]
                                c_close = price_source["close"]
                                c_mid = (c_high - c_low) / 2.0
                                buy_price = c_low + c_mid
                                ref_price = buy_price

                                msg = (
                                    f"Symbol: {meta['symbol']} ({lot_size} lots)\n"
                                    f"Volume(Lots): {format_vol(fut_vol)}({fut_lots} L)\n"
                                    f"Price : {c_close:.2f}\n"
                                    f"Candle C: {c_mid:.2f}\n"
                                    f"Buying price: {buy_price:.2f}\n"
                                )

                            if kite and ref_price > 0 and meta.get("opts_df") is not None and not meta["opts_df"].empty:
                                strike_step = meta["strike_step"]
                                atm_strike = round(ref_price / strike_step) * strike_step
                                target_strikes = [atm_strike + i * strike_step for i in range(-2, 3)]

                                opts_df = meta["opts_df"]
                                relevant_opts = opts_df[opts_df["strike"].astype(float).round(2).isin(target_strikes)]

                                symbols_to_quote = []
                                symbol_to_strike = {}
                                for _, row in relevant_opts.iterrows():
                                    qs = f"{row['exchange']}:{row['tradingsymbol']}"
                                    symbols_to_quote.append(qs)
                                    symbol_to_strike[qs] = {
                                        "strike": float(row["strike"]),
                                        "type": row["instrument_type"]
                                    }

                                try:
                                    quotes = kite_quote(kite, symbols_to_quote)
                                    strike_data = {s: {"CE": 0, "PE": 0} for s in target_strikes}
                                    for qs, data in quotes.items():
                                        if qs in symbol_to_strike:
                                            s = symbol_to_strike[qs]["strike"]
                                            t_type = symbol_to_strike[qs]["type"]
                                            strike_data[s][t_type] = data.get("oi", 0)

                                    def fmt_lakhs(v):
                                        if v == 0: return "0"
                                        if v <= 99000: return f"{int(round(v/1000))}K"
                                        return f"{v/100000:.1f}L"

                                    max_ce = max(d["CE"] for d in strike_data.values())
                                    max_pe = max(d["PE"] for d in strike_data.values())

                                    oi_table += "\n```\n"
                                    oi_table += f"   Call OI  |  Strike  |   Put OI   \n"
                                    oi_table += f"------------+----------+------------\n"

                                    for s in target_strikes:
                                        ce_val = strike_data[s]["CE"]
                                        pe_val = strike_data[s]["PE"]
                                        ce_str = fmt_lakhs(ce_val)
                                        pe_str = fmt_lakhs(pe_val)

                                        is_max_ce = (ce_val == max_ce and ce_val > 0)
                                        is_max_pe = (pe_val == max_pe and pe_val > 0)

                                        ce_prefix = "🔥" if is_max_ce else "  "
                                        pe_suffix = "🔥" if is_max_pe else "  "

                                        ce_oi_str = f"{ce_prefix}{ce_str:<5}"
                                        pe_oi_str = f"{pe_str:>5}{pe_suffix}"

                                        strike_str = f"{int(s)} 🎯" if s == atm_strike else f"{int(s)}   "
                                        oi_table += f"  {ce_oi_str:<7}  |  {strike_str} |  {pe_oi_str:>7}\n"
                                    oi_table += "```\n"
                                except Exception as e:
                                    print("Error fetching OI quote:", e)

                            msg += oi_table
                            msg += f"TIME: {now.strftime('%H:%M:%S')}\n"
                            alerts.append(msg)

                        if spot_tkn and spot_tkn in _spot_vol_candle_state:
                            _reset_spot_candle_state(spot_tkn, _spot_vol_candle_state[spot_tkn].get("current_vol", 0))
                        if fut_tkn and fut_tkn in _spot_vol_candle_state:
                            _reset_spot_candle_state(fut_tkn, _spot_vol_candle_state[fut_tkn].get("current_vol", 0))

                token_stocks = os.getenv("TELE_TOKEN_STOCKS", env_config.TELE_TOKEN)
                chat_stocks = os.getenv("CHAT_ID_STOCKS", env_config.TELE_CHAT_ID)
                for alert in alerts:
                    try:
                        send_telegram_message(alert, chat_id=chat_stocks, token=token_stocks)
                    except Exception as e:
                        print(f"Error sending spot volume alert: {e}")

    threading.Thread(target=reporting_loop, daemon=True).start()


# ------------------------------------------------------------------------------
# 2. 1-HOUR NARROW RANGE (NR-1H) 15-MINUTE OPTION BREAKOUT SCANNER
# ------------------------------------------------------------------------------
NR_WATCHLIST = [
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

NR_MAX_COMPRESSION_PCT = float(os.getenv("NR_MAX_COMPRESSION_PCT", "0.60"))
_nr_tracked_candidates = {}
_nr_alerted_assets = {}
_nr_state_lock = threading.Lock()
_nr_candidates_identified_date = None

def _get_target_monthly_expiry_date(expiries, target_date):
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

def _get_nr_highest_oi_option(kite, name, ref_price, direction, df_opts):
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
    try:
        quotes = kite.quote(symbols_to_quote)
    except Exception:
        quotes = {}

    best_strike = None
    max_oi = -1
    best_ltp = 0.0
    best_symbol = ""
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
        is_atm_str = " (ATM)" if best_strike == atm_strike else ""
        return action_verb, f"{best_symbol}{is_atm_str}", best_ltp, max_oi
    return action_verb, f"(ATM {target_type} Strike)", 0.0, 0


def _identify_nr_candidates(kite, df):
    """
    Runs at 10:15 AM to scan 1-Hour Future Candles (09:15 to 10:15 IST).
    Filters symbols where 1H Future Range % < 1.0%.
    """
    global _nr_tracked_candidates, _nr_alerted_assets, _nr_candidates_identified_date
    now = datetime.now(IST)
    today_date = now.date()

    print(f"[NR-1H] Scanning 1-Hour Future Candles (09:15 - 10:15) across {len(NR_WATCHLIST)} symbols (< {NR_MAX_COMPRESSION_PCT:.2f}% Compression)...")
    from_time = datetime.combine(today_date, datetime.strptime("09:15", "%H:%M").time(), tzinfo=IST)
    to_time = datetime.combine(today_date, datetime.strptime("10:15", "%H:%M").time(), tzinfo=IST)

    new_candidates = {}
    for name in NR_WATCHLIST:
        try:
            futs = df[(df["name"] == name) & (df["instrument_type"] == "FUT")]
            if futs.empty:
                continue
            futs = futs.sort_values(by="expiry")
            fut_row = futs.iloc[0]
            fut_token = int(fut_row["instrument_token"])
            fut_symbol = fut_row["tradingsymbol"]

            fut_candles = kite.historical_data(fut_token, from_time, to_time, "60minute")
            if not fut_candles:
                continue

            c1h = fut_candles[0]
            h_1h = float(c1h.get("high", 0.0))
            l_1h = float(c1h.get("low", 0.0))
            c_1h = float(c1h.get("close", 0.0))

            if l_1h <= 0 or h_1h <= l_1h:
                continue

            # 1H Future Range % Formula: ((High - Low) / Low) * 100
            range_pct = ((h_1h - l_1h) / l_1h) * 100.0

            # Compression Threshold (< NR_MAX_COMPRESSION_PCT)
            if range_pct < NR_MAX_COMPRESSION_PCT:
                opts = df[(df["name"] == name) & (df["instrument_type"].isin(["CE", "PE"]))]
                target_monthly_exp = None
                df_opts_monthly = pd.DataFrame()
                if not opts.empty:
                    all_expiries = sorted(opts["expiry_dt"].dt.date.unique())
                    target_monthly_exp = _get_target_monthly_expiry_date(all_expiries, today_date)
                    if target_monthly_exp is not None:
                        df_opts_monthly = opts[opts["expiry_dt"].dt.date == target_monthly_exp].copy()

                new_candidates[name] = {
                    "token": fut_token,
                    "symbol": fut_symbol,
                    "underlying": name,
                    "high_1h": h_1h,
                    "low_1h": l_1h,
                    "close_1h": c_1h,
                    "range_pct": range_pct,
                    "opts_df": df_opts_monthly,
                    "alerted": False
                }
                print(f"  [NR-1H FUTURE COMPRESSED] {name}: 1H High={h_1h:.2f}, Low={l_1h:.2f}, Range={range_pct:.2f}% (< {NR_MAX_COMPRESSION_PCT:.2f}%)")
        except Exception:
            continue

    with _nr_state_lock:
        _nr_tracked_candidates = new_candidates
        _nr_alerted_assets.clear()
        _nr_candidates_identified_date = today_date

    print(f"[NR-1H] Registered {len(new_candidates)} compressed Future candidates (< {NR_MAX_COMPRESSION_PCT:.2f}% Range).")


def _scan_nr_15m_breakouts(kite):
    """
    Evaluates completed 15-minute Future candles for Breakout (> 1H High) or Breakdown (< 1H Low).
    Recommends Highest OI ATM Option & locks direction for the day.
    """
    now = datetime.now(IST)
    today_date = now.date()
    with _nr_state_lock:
        active_list = [
            v for v in _nr_tracked_candidates.values()
            if not v["alerted"] and v["underlying"] not in _nr_alerted_assets
        ]

    if not active_list:
        return

    from_time = datetime.combine(today_date, datetime.strptime("10:15", "%H:%M").time(), tzinfo=IST)
    to_time = now

    for cand in active_list:
        token = cand["token"]
        name = cand["underlying"]
        high_1h = cand["high_1h"]
        low_1h = cand["low_1h"]

        try:
            candles = kite.historical_data(token, from_time, to_time, "15minute")
        except Exception:
            continue

        if not candles:
            continue

        last_candle = candles[-1]
        c_time = last_candle.get("date")
        if c_time and c_time.minute == now.minute and (now.minute % 15 != 0):
            candles_completed = candles[:-1]
        else:
            candles_completed = candles

        if not candles_completed:
            continue

        eval_candle = candles_completed[-1]
        candle_close = float(eval_candle.get("close", 0.0))
        candle_high = float(eval_candle.get("high", 0.0))
        candle_low = float(eval_candle.get("low", 0.0))

        is_bullish = candle_close > high_1h
        is_bearish = candle_close < low_1h

        if is_bullish or is_bearish:
            direction = "BULLISH" if is_bullish else "BEARISH"
            with _nr_state_lock:
                if name in _nr_alerted_assets:
                    continue
                cand["alerted"] = True
                _nr_alerted_assets[name] = {
                    "time": now,
                    "direction": direction
                }

            action_verb, opt_symbol, opt_ltp, max_oi = _get_nr_highest_oi_option(
                kite, name, candle_close, direction, cand["opts_df"]
            )

            time_str = eval_candle.get("date").strftime("%H:%M") if eval_candle.get("date") else "Completed"
            oi_text = f" (OI: {max_oi:,})" if max_oi > 0 else ""
            ltp_text = f"₹{opt_ltp:.2f}" if opt_ltp > 0 else "ATM Strike"

            if is_bullish:
                header = "🚀 *NR-1H FUTURE BREAKOUT*"
                level_line = f"15M Close: *₹{candle_close:.2f}* (Broke 1H High ₹{high_1h:.2f})"
            else:
                header = "🚨 *NR-1H FUTURE BREAKDOWN*"
                level_line = f"15M Close: *₹{candle_close:.2f}* (Broke 1H Low ₹{low_1h:.2f})"

            msg = (
                f"{header}\n"
                f"Asset: *{name}* (1H Range: {cand['range_pct']:.2f}%)\n"
                f"{level_line}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"Action: *{action_verb}*\n"
                f"Strike: *{opt_symbol}*\n"
                f"LTP: *{ltp_text}*{oi_text}\n"
                f"TIME: {now.strftime('%H:%M:%S')}"
            )
            print(f"[NR-1H FUTURE] Triggered {name} {direction} -> {action_verb} {opt_symbol}")
            import env_config
            send_telegram_message(msg, chat_id=env_config.TELE_CHAT_ID, token=env_config.TELE_TOKEN)

def start_nr_option_breakout_scanner(kite=None):
    """Initializes 1-Hour Narrow Range (NR-1H) option compression scanner."""
    print("Starting 1-Hour Narrow Range (NR-1H) Option Breakout Scanner...")
    import env_config
    from kiteconnect import KiteConnect

    token = _get_access_token()
    if not kite and token:
        try:
            kite = KiteConnect(api_key=env_config.API_KEY)
            kite.set_access_token(token)
        except Exception:
            pass

    df = _load_instruments_df()
    last_scanned_slot = None

    def worker_loop():
        nonlocal df, last_scanned_slot, kite
        while True:
            try:
                now = datetime.now(IST)
                if now.weekday() > 4:
                    time.sleep(60)
                    continue

                t = now.time()
                if not (datetime.strptime("09:15", "%H:%M").time() <= t <= datetime.strptime("15:30", "%H:%M").time()):
                    time.sleep(30)
                    continue

                global _nr_candidates_identified_date
                if t >= datetime.strptime("10:15", "%H:%M").time() and _nr_candidates_identified_date != now.date():
                    if df.empty:
                        df = _load_instruments_df()
                    if kite:
                        _identify_nr_candidates(kite, df)

                if t >= datetime.strptime("10:30", "%H:%M").time() and kite:
                    current_slot = (now.hour, (now.minute // 15) * 15)
                    if current_slot != last_scanned_slot and (now.minute % 15 == 0 and now.second >= 10 or now.minute % 15 > 0):
                        last_scanned_slot = current_slot
                        _scan_nr_15m_breakouts(kite)
            except Exception as e:
                print(f"[NR-1H] Scanner error: {e}")
            time.sleep(10)

    threading.Thread(target=worker_loop, daemon=True).start()


# ------------------------------------------------------------------------------
# 3. EXPIRY GAMMA (0-DTE & HERO-ZERO) ENGINE
# ------------------------------------------------------------------------------
_gamma_spot_price = 0.0
_gamma_option_quotes = {}
_gamma_last_alert_time = {}
_gamma_current_expiry_date = None

def _norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)

def _calc_gamma(spot, strike, iv_pct, minutes_to_close, r=0.07):
    if minutes_to_close <= 0 or iv_pct <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    sigma = iv_pct / 100.0
    T = minutes_to_close / (375.0 * 252.0)
    try:
        d1 = (math.log(spot / strike) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        return _norm_pdf(d1) / (spot * sigma * math.sqrt(T))
    except (ZeroDivisionError, ValueError):
        return 0.0

def start_expiry_gamma_scanner(kite=None):
    """Tracks 0-DTE Gamma squeeze and afternoon Hero-Zero breakout plays."""
    print("Starting Expiry Gamma & Hero-Zero Scanner Engine...")
    import env_config
    from kiteconnect import KiteConnect

    def supervisor():
        global _gamma_spot_price, _gamma_option_quotes, _gamma_current_expiry_date, _gamma_last_alert_time
        while True:
            try:
                now = datetime.now(IST)
                today_date = now.date()
                if now.weekday() > 4 or now.date().isoformat() in getattr(env_config, "NSE_HOLIDAYS", set()):
                    time.sleep(60)
                    continue

                # Tuesday = NIFTY, Thursday = SENSEX
                weekday = now.weekday()
                if weekday == 1:
                    name, exchange = "NIFTY", "NSE"
                elif weekday == 3:
                    name, exchange = "SENSEX", "BSE"
                else:
                    time.sleep(300)
                    continue

                t = now.time()
                if not (datetime.strptime("09:15", "%H:%M").time() <= t <= datetime.strptime("15:30", "%H:%M").time()):
                    time.sleep(30)
                    continue

                token = _get_access_token()
                if not token:
                    time.sleep(30)
                    continue

                kite_client = KiteConnect(api_key=env_config.API_KEY)
                kite_client.set_access_token(token)

                df = _load_instruments_df()
                if df.empty:
                    time.sleep(30)
                    continue

                opts = df[(df["name"] == name) & (df["segment"].isin(["NFO-OPT", "BFO-OPT"]))].copy()
                if opts.empty:
                    time.sleep(60)
                    continue

                opts["expiry"] = pd.to_datetime(opts["expiry"])
                active_opts = opts[opts["expiry"].dt.date >= today_date]
                if active_opts.empty:
                    time.sleep(60)
                    continue

                closest_expiry = active_opts["expiry"].min()
                if closest_expiry.date() != today_date:
                    time.sleep(300)
                    continue

                spot_tsym = "NIFTY 50" if name == "NIFTY" else "SENSEX"
                spot_symbol = "NSE:NIFTY 50" if name == "NIFTY" else "BSE:SENSEX"
                spots = df[(df["tradingsymbol"] == spot_tsym) & (df["segment"] == "INDICES")]
                if spots.empty:
                    spots = df[df["tradingsymbol"] == spot_tsym]
                if spots.empty:
                    time.sleep(30)
                    continue

                spot_token = int(spots.iloc[0]["instrument_token"])
                lot_size = int(active_opts.iloc[0].get("lot_size", 20 if name == "SENSEX" else 65))

                if _gamma_current_expiry_date != today_date:
                    _gamma_current_expiry_date = today_date
                    _gamma_last_alert_time.clear()
                    print(f"🟢 [0-DTE GAMMA] Tracking {name} on Expiry {today_date} (Spot Token: {spot_token})")

                    try:
                        spot_quote = kite_client.quote([spot_symbol]).get(spot_symbol, {})
                        initial_spot = float(spot_quote.get("last_price", 0.0))
                    except Exception:
                        initial_spot = 0.0

                    if initial_spot <= 0:
                        time.sleep(10)
                        continue

                    strikes = sorted(active_opts["strike"].unique())
                    atm_strike = min(strikes, key=lambda x: abs(x - initial_spot))
                    idx = strikes.index(atm_strike)
                    selected_strikes = strikes[max(0, idx - 8): min(len(strikes), idx + 9)]
                    expiry_opts = active_opts[(active_opts["expiry"] == closest_expiry) & (active_opts["strike"].isin(selected_strikes))]

                    active_option_tokens = []
                    token_to_strike_info = {}
                    for _, row in expiry_opts.iterrows():
                        tkn = int(row["instrument_token"])
                        active_option_tokens.append(tkn)
                        token_to_strike_info[tkn] = {
                            "strike": float(row["strike"]),
                            "type": row["instrument_type"],
                            "symbol": row["tradingsymbol"]
                        }

                    target_tokens = [spot_token] + active_option_tokens

                    def on_ticks(ws, ticks):
                        global _gamma_spot_price, _gamma_option_quotes
                        for tick in ticks:
                            tkn = tick["instrument_token"]
                            ltp = tick["last_price"]
                            if tkn == spot_token:
                                _gamma_spot_price = ltp
                            elif tkn in token_to_strike_info:
                                _gamma_option_quotes[tkn] = {
                                    "ltp": ltp,
                                    "oi": tick.get("oi", 0),
                                    "iv": tick.get("iv", 0.0) or 15.0,
                                    "volume": tick.get("volume_traded") or tick.get("volume", 0)
                                }

                    def on_connect(ws, response):
                        add_shared_tokens(target_tokens)

                    register_ws_callbacks(on_connect, on_ticks)
                    add_shared_tokens(target_tokens)

                market_end = datetime.strptime("15:30", "%H:%M").time()
                close_time = datetime.combine(now.date(), market_end, tzinfo=IST)
                minutes_left = (close_time - now).total_seconds() / 60.0

                # REST fallback if WebSocket quotes are missing or stale
                if _gamma_spot_price <= 0 or not _gamma_option_quotes:
                    try:
                        spot_q = kite_client.quote([spot_symbol]).get(spot_symbol, {})
                        if spot_q and spot_q.get("last_price"):
                            _gamma_spot_price = float(spot_q["last_price"])
                        if token_to_strike_info:
                            syms = [f"{exchange}:{info['symbol']}" for info in token_to_strike_info.values()]
                            opt_q = kite_client.quote(syms)
                            for tkn, info in token_to_strike_info.items():
                                sym_key = f"{exchange}:{info['symbol']}"
                                if sym_key in opt_q:
                                    oq = opt_q[sym_key]
                                    _gamma_option_quotes[tkn] = {
                                        "ltp": float(oq.get("last_price", 0.0)),
                                        "oi": oq.get("oi", 0),
                                        "iv": 15.0,
                                        "volume": oq.get("volume", 0)
                                    }
                    except Exception as e:
                        print(f"[GAMMA REST FALLBACK] Error: {e}")

                if minutes_left <= 0 or _gamma_spot_price <= 0 or not _gamma_option_quotes:
                    time.sleep(10)
                    continue

                # Evaluate afternoon Hero-Zero Squeeze (13:00 to 15:05 IST)
                is_hero_zero_window = datetime.strptime("13:00", "%H:%M").time() <= now.time() <= datetime.strptime("15:05", "%H:%M").time()
                if is_hero_zero_window:
                    now_ts = now.timestamp()
                    if not hasattr(supervisor, "spot_history"):
                        supervisor.spot_history = []
                        supervisor.hero_zero_locked_dir = None

                    supervisor.spot_history.append((now_ts, _gamma_spot_price))
                    supervisor.spot_history = [(t_s, p) for t_s, p in supervisor.spot_history if now_ts - t_s <= 1800]

                    if len(supervisor.spot_history) >= 10:
                        spot_prices_30m = [p for _, p in supervisor.spot_history]
                        spot_30m_high = max(spot_prices_30m)
                        spot_30m_low = min(spot_prices_30m)
                        spot_30m_vwap = sum(spot_prices_30m) / len(spot_prices_30m)

                        is_bullish_breakout = (_gamma_spot_price >= spot_30m_high - 1.5) and (_gamma_spot_price > spot_30m_vwap)
                        is_bearish_breakdown = (_gamma_spot_price <= spot_30m_low + 1.5) and (_gamma_spot_price < spot_30m_vwap)

                        otm_ce_strike_data = None
                        otm_pe_strike_data = None
                        for tkn, info in token_to_strike_info.items():
                            s_val = info["strike"]
                            q = _gamma_option_quotes.get(tkn, {})
                            if s_val > _gamma_spot_price and info["type"] == "CE" and otm_ce_strike_data is None:
                                otm_ce_strike_data = {"strike": s_val, "ltp": q.get("ltp", 0.0), "symbol": info["symbol"]}
                            if s_val < _gamma_spot_price and info["type"] == "PE":
                                otm_pe_strike_data = {"strike": s_val, "ltp": q.get("ltp", 0.0), "symbol": info["symbol"]}

                        min_price = 25.0 if name == "SENSEX" else 10.0
                        max_price = 90.0 if name == "SENSEX" else 35.0

                        if is_bullish_breakout and supervisor.hero_zero_locked_dir in (None, "CALL") and otm_ce_strike_data:
                            ce_ltp = otm_ce_strike_data["ltp"]
                            ce_strike = int(otm_ce_strike_data["strike"])
                            if min_price <= ce_ltp <= max_price:
                                alert_key = f"hz_ff_ce_{ce_strike}"
                                if time.time() - _gamma_last_alert_time.get(alert_key, 0.0) > 1800:
                                    _gamma_last_alert_time[alert_key] = time.time()
                                    supervisor.hero_zero_locked_dir = "CALL"
                                    msg = (
                                        f"🚀 *HERO-ZERO: {name} {ce_strike} CE*\n"
                                        f"Price: *₹{ce_ltp:.2f}*\n"
                                        f"SL: *₹{max(4.0, round(ce_ltp * 0.45, 1)):.2f}* | Target: *₹{round(ce_ltp * 2.2, 1):.2f}*\n"
                                        f"Spot: {_gamma_spot_price:.2f} (30M High Break)\n"
                                        f"Time: {now.strftime('%H:%M:%S')}"
                                    )
                                    send_telegram_message(msg, chat_id=env_config.TELE_CHAT_ID, token=env_config.TELE_TOKEN)

                        elif is_bearish_breakdown and supervisor.hero_zero_locked_dir in (None, "PUT") and otm_pe_strike_data:
                            pe_ltp = otm_pe_strike_data["ltp"]
                            pe_strike = int(otm_pe_strike_data["strike"])
                            if min_price <= pe_ltp <= max_price:
                                alert_key = f"hz_ff_pe_{pe_strike}"
                                if time.time() - _gamma_last_alert_time.get(alert_key, 0.0) > 1800:
                                    _gamma_last_alert_time[alert_key] = time.time()
                                    supervisor.hero_zero_locked_dir = "PUT"
                                    msg = (
                                        f"🚨 *HERO-ZERO: {name} {pe_strike} PE*\n"
                                        f"Price: *₹{pe_ltp:.2f}*\n"
                                        f"SL: *₹{max(4.0, round(pe_ltp * 0.45, 1)):.2f}* | Target: *₹{round(pe_ltp * 2.2, 1):.2f}*\n"
                                        f"Spot: {_gamma_spot_price:.2f} (30M Low Break)\n"
                                        f"Time: {now.strftime('%H:%M:%S')}"
                                    )
                                    send_telegram_message(msg, chat_id=env_config.TELE_CHAT_ID, token=env_config.TELE_TOKEN)
            except Exception as e:
                print(f"[GAMMA SCANNER] Supervisor error: {e}")
            time.sleep(30)

    threading.Thread(target=supervisor, daemon=True).start()


# ------------------------------------------------------------------------------
# 4. 2-CANDLE RVOL BREAKOUT SCANNER (4 Indices + Crude + 32 Stocks)
# ------------------------------------------------------------------------------
RVOL_INDICES = ["BANKNIFTY", "NIFTY", "SENSEX", "MIDCPNIFTY"]
RVOL_COMMODITIES = ["CRUDEOILM"]
RVOL_STOCKS = [
    "HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "KOTAKBANK",
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "BAJAJ-AUTO",
    "BAJAJFINSV", "BHARTIARTL", "BRITANNIA", "CIPLA", "EICHERMOT",
    "GRASIM", "HCLTECH", "HEROMOTOCO", "HINDUNILVR", "INFY",
    "LT", "M&M", "MARUTI", "NESTLEIND", "RELIANCE",
    "SBILIFE", "SUNPHARMA", "TATACONSUM", "TCS", "TITAN",
    "TRENT", "ULTRACEMCO"
]

_rvol_candle_history = {}
_rvol_candle_state = {}
_rvol_current_minute = {}
_rvol_token_metadata = {}
_rvol_option_contracts_cache = {}
_rvol_last_alert_times = {}
_rvol_state_lock = threading.Lock()
_rvol_kite_client = None

def _rvol_highest_oi_option(name, ref_price, direction):
    global _rvol_kite_client, _rvol_option_contracts_cache
    target_type = "CE" if direction == "BULLISH" else "PE"
    action_verb = "BUY CALL (CE)" if direction == "BULLISH" else "BUY PUT (PE)"

    if not _rvol_kite_client or name not in _rvol_option_contracts_cache or ref_price <= 0:
        return f"Action:- *{action_verb}*\n(ATM {target_type} Strike)"

    opts = _rvol_option_contracts_cache.get(name)
    if opts is None or opts.empty:
        return f"Action:- *{action_verb}*\n(ATM {target_type} Strike)"

    opts_side = opts[opts["instrument_type"] == target_type]
    if opts_side.empty:
        return f"Action:- *{action_verb}*\n(ATM {target_type} Strike)"

    unique_strikes = sorted(opts_side["strike"].unique())
    if not unique_strikes:
        return f"Action:- *{action_verb}*\n(ATM {target_type} Strike)"

    atm_strike = min(unique_strikes, key=lambda x: abs(x - ref_price))
    idx = unique_strikes.index(atm_strike)
    selected_strikes = unique_strikes[max(0, idx - 1): min(len(unique_strikes), idx + 2)]
    target_opts = opts_side[opts_side["strike"].isin(selected_strikes)]
    if target_opts.empty:
        return f"Action:- *{action_verb}*\n(ATM {target_type} Strike)"

    symbols_to_quote = [f"{r['exchange']}:{r['tradingsymbol']}" for _, r in target_opts.iterrows()]
    try:
        quotes = _rvol_kite_client.quote(symbols_to_quote)
    except Exception:
        quotes = {}

    best_strike = None
    max_oi = -1
    best_ltp = 0.0
    best_symbol = ""
    for _, row in target_opts.iterrows():
        sym_key = f"{row['exchange']}:{row['tradingsymbol']}"
        q = quotes.get(sym_key, {})
        oi = q.get("oi", 0)
        ltp = q.get("last_price", 0.0)
        strike_val = float(row["strike"])
        if oi > max_oi or (oi == max_oi and strike_val == atm_strike):
            max_oi = oi
            best_strike = strike_val
            best_ltp = ltp
            best_symbol = row["tradingsymbol"]

    if best_symbol and best_strike is not None:
        return f"Action:- *{action_verb}*\n*{best_symbol}*\nLTP: *₹{best_ltp:.2f}*"
    return f"Action:- *{action_verb}*\n(ATM {target_type} Strike)"

def _analyze_2candle_pattern(c1, c2, is_mcx=False):
    c1_c, c1_o, c1_h, c1_l, c1_lots = c1["close"], c1["open"], c1["high"], c1["low"], c1["lots"]
    c2_c, c2_o, c2_h, c2_l, c2_lots = c2["close"], c2["open"], c2["high"], c2["low"], c2["lots"]

    c1_range = max(0.01, c1_h - c1_l)
    c2_range = max(0.01, c2_h - c2_l)
    c1_body_ratio = abs(c1_c - c1_o) / c1_range
    c2_body_ratio = abs(c2_c - c2_o) / c2_range

    c1_req_lots = 75 if is_mcx else 500
    c2_req_lots = 50 if is_mcx else 300

    if c1_lots < c1_req_lots or c2_lots < c2_req_lots:
        return False, None, 0, {}

    if c1_c < c1_o and c2_c < c2_o:
        c1_lower_wick = max(0.0, c1_c - c1_l) / c1_range
        c2_lower_wick = max(0.0, c2_c - c2_l) / c2_range
        if (c1_body_ratio >= 0.60 and c1_lower_wick <= 0.20) and (c2_c < c1_l and c2_body_ratio >= 0.60 and c2_lower_wick <= 0.20):
            return True, "BEARISH", 9, {"c1_lots": c1_lots, "c2_lots": c2_lots, "broken_level": c1_l}

    elif c1_c > c1_o and c2_c > c2_o:
        c1_upper_wick = max(0.0, c1_h - c1_c) / c1_range
        c2_upper_wick = max(0.0, c2_h - c2_c) / c2_range
        if (c1_body_ratio >= 0.60 and c1_upper_wick <= 0.20) and (c2_c > c1_h and c2_body_ratio >= 0.60 and c2_upper_wick <= 0.20):
            return True, "BULLISH", 9, {"c1_lots": c1_lots, "c2_lots": c2_lots, "broken_level": c1_h}

    return False, None, 0, {}

def _process_rvol_1m_candle(token, closed_candle):
    with _rvol_state_lock:
        meta = _rvol_token_metadata.get(token)
        if not meta:
            return
        if token not in _rvol_candle_history:
            _rvol_candle_history[token] = []
        hist = _rvol_candle_history[token]
        hist.append(closed_candle)
        if len(hist) > 10:
            hist.pop(0)
        if len(hist) < 2:
            return
        c1, c2 = hist[-2], hist[-1]

    is_mcx = meta.get("is_mcx", False)
    is_signal, direction, score, details = _analyze_2candle_pattern(c1, c2, is_mcx=is_mcx)
    if is_signal:
        now = datetime.now(IST)
        name = meta["name"]
        display_label = meta["display_label"]
        alert_key = f"{display_label}_{direction}"

        with _rvol_state_lock:
            if time.time() - _rvol_last_alert_times.get(alert_key, 0.0) < 300:
                return
            _rvol_last_alert_times[alert_key] = time.time()

        signal_header = "🚀 *1-MIN 2-CANDLE BREAKOUT*" if direction == "BULLISH" else "🚨 *1-MIN 2-CANDLE BREAKDOWN*"
        action_line = _rvol_highest_oi_option(name, c2["close"], direction)
        msg = (
            f"{signal_header}\n"
            f"Asset: *{display_label}* (₹{c2['close']:.2f})\n"
            f"Vol: C1: *{details['c1_lots']}L* | C2: *{details['c2_lots']}L*\n"
            f"Broken: *₹{details['broken_level']:.2f}*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"{action_line}\n"
            f"TIME: {now.strftime('%H:%M:%S')}"
        )
        import env_config
        chat_stocks = getattr(env_config, "TELE_CHAT_ID_STOCKS", env_config.TELE_CHAT_ID)
        token_stocks = getattr(env_config, "TELE_TOKEN_STOCKS", env_config.TELE_TOKEN)
        send_telegram_message(msg, chat_id=chat_stocks, token=token_stocks)

def start_rvol_2candle_breakout_scanner(kite=None):
    """Initializes 2-Candle 1-Minute Volume Breakout/Breakdown Scanner."""
    print("Starting 2-Candle 1-Minute RVOL Breakout Scanner...")
    global _rvol_kite_client, _rvol_option_contracts_cache, _rvol_token_metadata
    import env_config
    from kiteconnect import KiteConnect

    token = _get_access_token()
    if not _rvol_kite_client and token:
        try:
            _rvol_kite_client = KiteConnect(api_key=env_config.API_KEY)
            _rvol_kite_client.set_access_token(token)
        except Exception:
            pass

    df = _load_instruments_df()
    if df.empty:
        return

    target_tokens = []
    # 1. Index Futures
    for name in RVOL_INDICES:
        futs = df[(df["name"] == name) & (df["instrument_type"] == "FUT")]
        if not futs.empty:
            futs = futs.sort_values(by="expiry")
            fut = futs.iloc[0]
            lot_size = int(fut.get("lot_size", 20 if name == "SENSEX" else 65))
            fut_tkn = int(fut["instrument_token"])
            target_tokens.append(fut_tkn)
            _rvol_token_metadata[fut_tkn] = {
                "name": name,
                "display_label": fut["tradingsymbol"],
                "lot_size": lot_size,
                "is_mcx": False,
                "is_spot": False
            }

    # 2. MCX Commodities
    for name in RVOL_COMMODITIES:
        futs = df[(df["name"] == name) & (df["instrument_type"] == "FUT")]
        if not futs.empty:
            futs = futs.sort_values(by="expiry")
            fut = futs.iloc[0]
            lot_size = 10 if name == "CRUDEOILM" else int(fut.get("lot_size", 1))
            fut_tkn = int(fut["instrument_token"])
            target_tokens.append(fut_tkn)
            _rvol_token_metadata[fut_tkn] = {
                "name": name,
                "display_label": fut["tradingsymbol"],
                "lot_size": lot_size,
                "is_mcx": True,
                "is_spot": False
            }

    # 3. 32 Focus Stocks (Spot & Future)
    for name in RVOL_STOCKS:
        futs = df[(df["name"] == name) & (df["instrument_type"] == "FUT")]
        if not futs.empty:
            futs = futs.sort_values(by="expiry")
            fut = futs.iloc[0]
            lot_size = int(fut.get("lot_size", 1))
            fut_tkn = int(fut["instrument_token"])
            target_tokens.append(fut_tkn)
            _rvol_token_metadata[fut_tkn] = {
                "name": name,
                "display_label": f"{fut['tradingsymbol']} (FUT)",
                "lot_size": lot_size,
                "is_mcx": False,
                "is_spot": False
            }

        spots = df[(df["tradingsymbol"] == name) & (df["segment"] == "NSE")]
        if not spots.empty:
            spot_tkn = int(spots.iloc[0]["instrument_token"])
            target_tokens.append(spot_tkn)
            _rvol_token_metadata[spot_tkn] = {
                "name": name,
                "display_label": f"{name} (SPOT)",
                "lot_size": lot_size,
                "is_mcx": False,
                "is_spot": True
            }

    # Cache closest options for strike lookup
    for name in (RVOL_INDICES + RVOL_COMMODITIES + RVOL_STOCKS):
        opts = df[(df["name"] == name) & (df["instrument_type"].isin(["CE", "PE"]))]
        if not opts.empty:
            closest_expiry = opts["expiry"].min()
            _rvol_option_contracts_cache[name] = opts[opts["expiry"] == closest_expiry].copy()

    def on_ticks(ws, ticks):
        now = datetime.now(IST)
        minute_str = now.strftime("%Y-%m-%d %H:%M")

        with _rvol_state_lock:
            for tick in ticks:
                tkn = tick["instrument_token"]
                if tkn not in _rvol_token_metadata:
                    continue

                ltp = tick["last_price"]
                vol = tick.get("volume_traded") or tick.get("volume", 0)
                meta = _rvol_token_metadata[tkn]
                lot_size = meta["lot_size"]

                if tkn not in _rvol_current_minute:
                    _rvol_current_minute[tkn] = minute_str
                    _rvol_candle_state[tkn] = {
                        "open": ltp, "high": ltp, "low": ltp, "close": ltp,
                        "start_vol": vol, "current_vol": vol
                    }

                if _rvol_current_minute[tkn] != minute_str:
                    c = _rvol_candle_state[tkn]
                    candle_vol = max(0, c["current_vol"] - c["start_vol"])
                    closed_candle = {
                        "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"],
                        "volume": candle_vol, "lots": int(candle_vol / lot_size),
                        "minute": _rvol_current_minute[tkn]
                    }
                    _rvol_current_minute[tkn] = minute_str
                    _rvol_candle_state[tkn] = {
                        "open": ltp, "high": ltp, "low": ltp, "close": ltp,
                        "start_vol": vol, "current_vol": vol
                    }
                    threading.Thread(target=_process_rvol_1m_candle, args=(tkn, closed_candle), daemon=True).start()
                else:
                    c = _rvol_candle_state[tkn]
                    c["close"] = ltp
                    c["high"] = max(c["high"], ltp)
                    c["low"] = min(c["low"], ltp)
                    c["current_vol"] = vol

    def on_connect(ws, response):
        add_shared_tokens(target_tokens)

    register_ws_callbacks(on_connect, on_ticks)
    add_shared_tokens(target_tokens)


# ------------------------------------------------------------------------------
# 5. ALL-F&O INSTITUTIONAL VOLUME SHOCK & BREAKOUT SCANNER
# ------------------------------------------------------------------------------
_fo_future_metadata = {}
_fo_rolling_candles = {}
_fo_current_minute = {}
_fo_minute_candles = {}
_fo_option_contracts_cache = {}
_fo_last_alert_times = {}
_fo_state_lock = threading.Lock()
_fo_kite_client = None

def _fo_highest_oi_option(name, ref_price, direction):
    global _fo_kite_client, _fo_option_contracts_cache
    target_type = "CE" if direction == "BULLISH" else "PE"
    action_verb = "BUY CALL (CE)" if direction == "BULLISH" else "BUY PUT (PE)"

    if not _fo_kite_client or name not in _fo_option_contracts_cache or ref_price <= 0:
        return action_verb, "(ATM Strike)", 0.0

    opts = _fo_option_contracts_cache.get(name)
    if opts is None or opts.empty:
        return action_verb, f"(ATM {target_type} Strike)", 0.0

    opts_side = opts[opts["instrument_type"] == target_type]
    if opts_side.empty:
        return action_verb, f"(ATM {target_type} Strike)", 0.0

    unique_strikes = sorted(opts_side["strike"].unique())
    if not unique_strikes:
        return action_verb, f"(ATM {target_type} Strike)", 0.0

    atm_strike = min(unique_strikes, key=lambda x: abs(x - ref_price))
    idx = unique_strikes.index(atm_strike)
    selected_strikes = unique_strikes[max(0, idx - 1): min(len(unique_strikes), idx + 2)]
    target_opts = opts_side[opts_side["strike"].isin(selected_strikes)]
    if target_opts.empty:
        return action_verb, f"(ATM {target_type} Strike)", 0.0

    symbols_to_quote = [f"{r['exchange']}:{r['tradingsymbol']}" for _, r in target_opts.iterrows()]
    try:
        quotes = _fo_kite_client.quote(symbols_to_quote)
    except Exception:
        quotes = {}

    best_strike = None
    max_oi = -1
    best_ltp = 0.0
    best_symbol = ""
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
        return action_verb, best_symbol, best_ltp
    return action_verb, f"(ATM {target_type} Strike)", 0.0

def _process_fo_1m_candle(token, closed_candle):
    with _fo_state_lock:
        meta = _fo_future_metadata.get(token)
        if not meta:
            return
        if token not in _fo_rolling_candles:
            _fo_rolling_candles[token] = []
        history = _fo_rolling_candles[token]
        history.append(closed_candle)
        if len(history) > 61:
            history.pop(0)
        if len(history) < 15:
            return

        past_volumes = [c["volume"] for c in history[:-1][-20:]]
        avg_vol_20m = sum(past_volumes) / len(past_volumes) if past_volumes else 1.0
        past_highs = [c["high"] for c in history[:-1]]
        past_lows = [c["low"] for c in history[:-1]]
        high_60m = max(past_highs)
        low_60m = min(past_lows)

    c_o, c_h, c_l, c_c = closed_candle["open"], closed_candle["high"], closed_candle["low"], closed_candle["close"]
    c_vol, c_lots = closed_candle["volume"], closed_candle["lots"]
    c_range = max(0.05, c_h - c_l)
    body_ratio = abs(c_c - c_o) / c_range
    rvol = c_vol / max(1.0, avg_vol_20m)

    if rvol < 8.0 or c_lots < 250 or body_ratio < 0.55:
        return

    is_bullish = (c_c > c_o) and (c_c > high_60m)
    is_bearish = (c_c < c_o) and (c_c < low_60m)
    if not (is_bullish or is_bearish):
        return

    direction = "BULLISH" if is_bullish else "BEARISH"
    name = meta["name"]
    alert_key = f"{name}_{direction}"

    with _fo_state_lock:
        if time.time() - _fo_last_alert_times.get(alert_key, 0.0) < 900:
            return
        _fo_last_alert_times[alert_key] = time.time()

    now = datetime.now(IST)
    action_verb, opt_symbol, opt_ltp = _fo_highest_oi_option(name, c_c, direction)
    opt_line = f"Option: *{opt_symbol}*"
    if opt_ltp > 0:
        opt_line += f" (LTP: ₹{opt_ltp:.2f})"

    header = "🚀 *INSTITUTIONAL VOLUME BREAKOUT (ALL F&O)*" if is_bullish else "🚨 *INSTITUTIONAL VOLUME BREAKDOWN (ALL F&O)*"
    level_line = f"Broke 60M High: *₹{high_60m:.2f}*" if is_bullish else f"Broke 60M Low: *₹{low_60m:.2f}*"

    msg = (
        f"{header}\n"
        f"Stock: *{name}* (₹{c_c:.2f})\n"
        f"Volume: *{c_lots} Lots* ({rvol:.1f}x of 20M Avg)\n"
        f"{level_line}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Action: *{action_verb}*\n"
        f"{opt_line}\n"
        f"Time: {now.strftime('%H:%M:%S')} IST"
    )
    import env_config
    target_chat = getattr(env_config, "TELE_CHAT_ID_STOCKS", env_config.TELE_CHAT_ID)
    target_token = getattr(env_config, "TELE_TOKEN_STOCKS", env_config.TELE_TOKEN)
    send_telegram_message(msg, chat_id=target_chat, token=target_token)

def start_fo_institutional_breakout_scanner(kite=None):
    """Initializes All-F&O Institutional Volume Shock & Breakout Scanner."""
    print("Starting All-F&O Institutional Volume Shock Scanner...")
    global _fo_kite_client, _fo_option_contracts_cache, _fo_future_metadata
    import env_config
    from kiteconnect import KiteConnect

    token = _get_access_token()
    if not _fo_kite_client and token:
        try:
            _fo_kite_client = KiteConnect(api_key=env_config.API_KEY)
            _fo_kite_client.set_access_token(token)
        except Exception:
            pass

    df = _load_instruments_df()
    if df.empty:
        return

    excluded_idx = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX", "SENSEX50"}
    stock_futs = df[
        (df["segment"] == "NFO-FUT") &
        (~df["name"].isin(excluded_idx)) &
        (df["name"].notna())
    ].copy()

    if stock_futs.empty:
        return

    target_tokens = []
    for name, rows in stock_futs.groupby("name"):
        rows_sorted = rows.sort_values(by="expiry")
        near_fut = rows_sorted.iloc[0]
        fut_tkn = int(near_fut["instrument_token"])
        lot_size = int(near_fut.get("lot_size", 1))

        target_tokens.append(fut_tkn)
        _fo_future_metadata[fut_tkn] = {
            "name": name,
            "symbol": near_fut["tradingsymbol"],
            "lot_size": lot_size
        }

        opts = df[(df["name"] == name) & (df["segment"] == "NFO-OPT")]
        if not opts.empty:
            closest_exp = opts["expiry"].min()
            _fo_option_contracts_cache[name] = opts[opts["expiry"] == closest_exp].copy()

    def on_ticks(ws, ticks):
        now = datetime.now(IST)
        minute_str = now.strftime("%Y-%m-%d %H:%M")

        with _fo_state_lock:
            for tick in ticks:
                tkn = tick["instrument_token"]
                if tkn not in _fo_future_metadata:
                    continue

                ltp = tick["last_price"]
                vol = tick.get("volume_traded") or tick.get("volume", 0)
                lot_size = _fo_future_metadata[tkn]["lot_size"]

                if tkn not in _fo_current_minute:
                    _fo_current_minute[tkn] = minute_str
                    _fo_minute_candles[tkn] = {
                        "open": ltp, "high": ltp, "low": ltp, "close": ltp,
                        "start_vol": vol, "current_vol": vol
                    }

                if _fo_current_minute[tkn] != minute_str:
                    c = _fo_minute_candles[tkn]
                    candle_vol = max(0, c["current_vol"] - c["start_vol"])
                    closed_candle = {
                        "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"],
                        "volume": candle_vol, "lots": int(candle_vol / lot_size),
                        "minute": _fo_current_minute[tkn]
                    }
                    _fo_current_minute[tkn] = minute_str
                    _fo_minute_candles[tkn] = {
                        "open": ltp, "high": ltp, "low": ltp, "close": ltp,
                        "start_vol": vol, "current_vol": vol
                    }
                    threading.Thread(target=_process_fo_1m_candle, args=(tkn, closed_candle), daemon=True).start()
                else:
                    c = _fo_minute_candles[tkn]
                    c["close"] = ltp
                    c["high"] = max(c["high"], ltp)
                    c["low"] = min(c["low"], ltp)
                    c["current_vol"] = vol

    def on_connect(ws, response):
        add_shared_tokens(target_tokens)

    register_ws_callbacks(on_connect, on_ticks)
    add_shared_tokens(target_tokens)


# ------------------------------------------------------------------------------
# 6. 1-HOUR 3-CANDLE PRICE & VOLUME DIVERGENCE ENGINE (FUTURES & ATM+3 ITM OPTIONS)
# ------------------------------------------------------------------------------
PRICE_VOL_3C_WATCHLIST = [
    # 4 Indices
    "NIFTY", "SENSEX", "BANKNIFTY", "MIDCPNIFTY",
    # 1 Commodity (MCX)
    "CRUDEOILM",
    # 32 Core Focus Stocks
    "HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "KOTAKBANK",
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "BAJAJ-AUTO",
    "BAJAJFINSV", "BHARTIARTL", "BRITANNIA", "CIPLA", "EICHERMOT",
    "GRASIM", "HCLTECH", "HEROMOTOCO", "HINDUNILVR", "INFY",
    "LT", "M&M", "MARUTI", "NESTLEIND", "RELIANCE",
    "SBILIFE", "SUNPHARMA", "TATACONSUM", "TCS", "TITAN",
    "TRENT", "ULTRACEMCO"
]

_3c_div_alerted_slots = set()

def _analyze_3candle_price_vol_divergence(c1, c2, c3):
    """
    Evaluates 3 consecutive 1-Hour candles (c1 -> c2 -> c3) for 4 Price & Volume patterns:
    1. Price LL + Vol HH (Selling Absorption / Bullish Climax Reversal)
    2. Price LL + Vol LL (Selling Exhaustion / Thin Volume Breakdown)
    3. Price HH + Vol HH (Bullish Expansion / Aggressive Institutional Buy)
    4. Price HH + Vol LL (Buying Exhaustion / Bearish Divergence Trap)
    """
    l1, l2, l3 = float(c1.get("low", 0)), float(c2.get("low", 0)), float(c3.get("low", 0))
    h1, h2, h3 = float(c1.get("high", 0)), float(c2.get("high", 0)), float(c3.get("high", 0))
    close1, close2, close3 = float(c1.get("close", 0)), float(c2.get("close", 0)), float(c3.get("close", 0))
    v1, v2, v3 = float(c1.get("volume", 0)), float(c2.get("volume", 0)), float(c3.get("volume", 0))

    if l1 <= 0 or l2 <= 0 or l3 <= 0 or h1 <= 0 or h2 <= 0 or h3 <= 0 or v1 <= 0 or v2 <= 0 or v3 <= 0:
        return None

    is_price_ll = (l3 < l2 < l1) and (close3 < close2 < close1)
    is_price_hh = (h3 > h2 > h1) and (close3 > close2 > close1)
    is_vol_hh = (v3 > v2 > v1)
    is_vol_ll = (v3 < v2 < v1)

    # Criteria 1: Price LL + Vol HH
    if is_price_ll and is_vol_hh:
        return {
            "type": "ABSORPTION_REVERSAL",
            "condition": "Price LL + Vol HH (Selling Absorption Trap)",
            "action": "BUY CALL (CE)",
            "sentiment": "🟢 BULLISH REVERSAL",
            "p_trend": f"{close1:.1f} ➔ {close2:.1f} ➔ {close3:.1f} (LL)",
            "v_trend": f"{v1:,.0f} ➔ {v2:,.0f} ➔ {v3:,.0f} (HH 🟢)",
            "close": close3
        }

    # Criteria 2: Price LL + Vol LL
    if is_price_ll and is_vol_ll:
        return {
            "type": "SELLING_EXHAUSTION",
            "condition": "Price LL + Vol LL (Selling Volume Exhaustion)",
            "action": "WATCH REVERSAL / CAUTION SELL",
            "sentiment": "⚠️ BEARISH EXHAUSTION",
            "p_trend": f"{close1:.1f} ➔ {close2:.1f} ➔ {close3:.1f} (LL)",
            "v_trend": f"{v1:,.0f} ➔ {v2:,.0f} ➔ {v3:,.0f} (LL 🔴)",
            "close": close3
        }

    # Criteria 3: Price HH + Vol HH
    if is_price_hh and is_vol_hh:
        return {
            "type": "BULLISH_EXPANSION",
            "condition": "Price HH + Vol HH (Institutional Trend Expansion)",
            "action": "BUY CALL (CE)",
            "sentiment": "🚀 BULLISH EXPANSION",
            "p_trend": f"{close1:.1f} ➔ {close2:.1f} ➔ {close3:.1f} (HH)",
            "v_trend": f"{v1:,.0f} ➔ {v2:,.0f} ➔ {v3:,.0f} (HH 🟢)",
            "close": close3
        }

    # Criteria 4: Price HH + Vol LL
    if is_price_hh and is_vol_ll:
        return {
            "type": "BUYING_EXHAUSTION",
            "condition": "Price HH + Vol LL (Buying Exhaustion / Divergence Trap)",
            "action": "BUY PUT (PE)",
            "sentiment": "🚨 BEARISH DIVERGENCE",
            "p_trend": f"{close1:.1f} ➔ {close2:.1f} ➔ {close3:.1f} (HH)",
            "v_trend": f"{v1:,.0f} ➔ {v2:,.0f} ➔ {v3:,.0f} (LL 🔴)",
            "close": close3
        }

    return None

def _get_3c_atm_and_itm_options(kite, name, ref_price, direction, df_opts):
    target_type = "CE" if "CALL" in direction or "BULLISH" in direction else "PE"
    if df_opts is None or df_opts.empty or ref_price <= 0:
        return f"(ATM {target_type} Strike)", 0.0

    opts_side = df_opts[df_opts["instrument_type"] == target_type]
    if opts_side.empty:
        return f"(ATM {target_type} Strike)", 0.0

    unique_strikes = sorted(opts_side["strike"].unique())
    if not unique_strikes:
        return f"(ATM {target_type} Strike)", 0.0

    atm_strike = min(unique_strikes, key=lambda x: abs(x - ref_price))
    idx = unique_strikes.index(atm_strike)

    # ATM + 3 ITM strikes:
    # CE ITM: strikes at or below ATM
    # PE ITM: strikes at or above ATM
    if target_type == "CE":
        selected_strikes = unique_strikes[max(0, idx - 3): idx + 1]
    else:
        selected_strikes = unique_strikes[idx: min(len(unique_strikes), idx + 4)]

    target_opts = opts_side[opts_side["strike"].isin(selected_strikes)]
    if target_opts.empty:
        return f"(ATM {target_type} Strike)", 0.0

    symbols_to_quote = [f"{r['exchange']}:{r['tradingsymbol']}" for _, r in target_opts.iterrows()]
    try:
        quotes = kite.quote(symbols_to_quote)
    except Exception:
        quotes = {}

    best_strike = None
    max_oi = -1
    best_ltp = 0.0
    best_symbol = ""
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
        is_atm = " (ATM)" if best_strike == atm_strike else f" ({abs(idx - unique_strikes.index(best_strike))} ITM)"
        return f"{best_symbol}{is_atm}", best_ltp
    return f"(ATM {target_type} Strike)", 0.0

def start_3candle_price_volume_divergence_scanner(kite=None):
    """
    Scans 1-Hour 3-Candle Price & Volume Divergence patterns:
    - On Future contracts (recommending ATM/ITM strike)
    - Directly on individual ATM + 3 ITM Option contracts (CE and PE)
    """
    print("Starting 1-Hour 3-Candle Price & Volume Divergence Scanner...")
    import env_config
    from kiteconnect import KiteConnect

    token = _get_access_token()
    if not kite and token:
        try:
            kite = KiteConnect(api_key=env_config.API_KEY)
            kite.set_access_token(token)
        except Exception:
            pass

    df = _load_instruments_df()

    def scanner_loop():
        nonlocal df, kite
        while True:
            try:
                now = datetime.now(IST)
                if now.weekday() > 4:
                    time.sleep(60)
                    continue

                t = now.time()
                is_nse_open = datetime.strptime("09:15", "%H:%M").time() <= t <= datetime.strptime("15:30", "%H:%M").time()
                is_mcx_open = datetime.strptime("15:30", "%H:%M").time() <= t <= datetime.strptime("23:30", "%H:%M").time()

                if not is_nse_open and not is_mcx_open:
                    time.sleep(60)
                    continue

                # Run after completed 1-Hour candles (e.g. 10:16, 11:16, 12:16, 13:16, 14:16, 15:16, etc.)
                slot_key = f"{now.date()}_{now.hour}"
                if now.minute >= 15 and slot_key not in _3c_div_alerted_slots:
                    _3c_div_alerted_slots.add(slot_key)
                    if df.empty:
                        df = _load_instruments_df()

                    from_time = datetime.combine(_get_previous_trading_day(now), datetime.strptime("09:15", "%H:%M").time(), tzinfo=IST)
                    to_time = now

                    for name in PRICE_VOL_3C_WATCHLIST:
                        is_mcx = (name == "CRUDEOILM")
                        if is_mcx and not is_mcx_open and t < datetime.strptime("15:30", "%H:%M").time():
                            continue
                        if not is_mcx and not is_nse_open:
                            continue

                        send_channel = env_config.TELE_CHAT_ID_STOCKS
                        send_token = env_config.TELE_TOKEN_STOCKS

                        futs = df[(df["name"] == name) & (df["instrument_type"] == "FUT")]
                        if futs.empty:
                            continue
                        futs = futs.sort_values(by="expiry")
                        fut_row = futs.iloc[0]
                        fut_token = int(fut_row["instrument_token"])
                        fut_symbol = fut_row["tradingsymbol"]

                        # 1. EVALUATE FUTURE CONTRACT
                        try:
                            fut_candles = get_historical_data_cached(kite, fut_token, from_time, to_time, "60minute")
                        except Exception:
                            fut_candles = []

                        fut_ltp = 0.0
                        if fut_candles and len(fut_candles) >= 4:
                            c1, c2, c3 = fut_candles[-4], fut_candles[-3], fut_candles[-2]
                            fut_ltp = float(c3.get("close", 0))
                            res_fut = _analyze_3candle_price_vol_divergence(c1, c2, c3)
                            if res_fut:
                                opts = df[(df["name"] == name) & (df["instrument_type"].isin(["CE", "PE"]))]
                                df_opts_monthly = pd.DataFrame()
                                if not opts.empty:
                                    all_expiries = sorted(opts["expiry_dt"].dt.date.unique())
                                    target_monthly_exp = _get_target_monthly_expiry_date(all_expiries, now.date())
                                    if target_monthly_exp is not None:
                                        df_opts_monthly = opts[opts["expiry_dt"].dt.date == target_monthly_exp].copy()

                                opt_symbol, opt_ltp = _get_3c_atm_and_itm_options(
                                    kite, name, res_fut["close"], res_fut["action"], df_opts_monthly
                                )
                                ltp_str = f"₹{opt_ltp:.2f}" if opt_ltp > 0 else "ATM"
                                msg = (
                                    f"📊 *1H 3C FUTURE: {res_fut['sentiment']}*\n"
                                    f"Future: *{fut_symbol}* (₹{res_fut['close']:.2f})\n"
                                    f"Pattern: {res_fut['condition']}\n"
                                    f"━━━━━━━━━━━━━━━━━━━\n"
                                    f"Action: *{res_fut['action']}*\n"
                                    f"Recommended Strike: *{opt_symbol}*\n"
                                    f"LTP: *{ltp_str}*\n"
                                    f"TIME: {now.strftime('%H:%M:%S')}"
                                )
                                send_telegram_message(msg, chat_id=send_channel, token=send_token)

                        # 2. EVALUATE INDIVIDUAL OPTION CONTRACTS (ATM + 3 ITM CE & PE)
                        if fut_ltp <= 0 and fut_candles:
                            fut_ltp = float(fut_candles[-1].get("close", 0))

                        if fut_ltp > 0:
                            opts = df[(df["name"] == name) & (df["instrument_type"].isin(["CE", "PE"]))]
                            if not opts.empty:
                                all_expiries = sorted(opts["expiry_dt"].dt.date.unique())
                                target_monthly_exp = _get_target_monthly_expiry_date(all_expiries, now.date())
                                if target_monthly_exp is not None:
                                    opts_active = opts[opts["expiry_dt"].dt.date == target_monthly_exp].copy()
                                    unique_strikes = sorted(opts_active["strike"].unique())
                                    if unique_strikes:
                                        atm_strike = min(unique_strikes, key=lambda x: abs(x - fut_ltp))
                                        atm_idx = unique_strikes.index(atm_strike)

                                        # CE strikes: ATM + 3 below ATM
                                        ce_strikes = unique_strikes[max(0, atm_idx - 3): atm_idx + 1]
                                        # PE strikes: ATM + 3 above ATM
                                        pe_strikes = unique_strikes[atm_idx: min(len(unique_strikes), atm_idx + 4)]

                                        target_opts = opts_active[
                                            (opts_active["instrument_type"] == "CE") & (opts_active["strike"].isin(ce_strikes)) |
                                            (opts_active["instrument_type"] == "PE") & (opts_active["strike"].isin(pe_strikes))
                                        ]

                                        matched_options = []
                                        for _, opt_row in target_opts.iterrows():
                                            opt_tkn = int(opt_row["instrument_token"])
                                            opt_sym = opt_row["tradingsymbol"]
                                            opt_type = opt_row["instrument_type"]
                                            opt_strike = float(opt_row["strike"])
                                            strike_label = "(ATM)" if opt_strike == atm_strike else f"({abs(atm_idx - unique_strikes.index(opt_strike))} ITM)"

                                            try:
                                                opt_candles = get_historical_data_cached(kite, opt_tkn, from_time, to_time, "60minute")
                                            except Exception:
                                                continue

                                            if not opt_candles or len(opt_candles) < 4:
                                                continue

                                            oc1, oc2, oc3 = opt_candles[-4], opt_candles[-3], opt_candles[-2]
                                            res_opt = _analyze_3candle_price_vol_divergence(oc1, oc2, oc3)
                                            if res_opt:
                                                matched_options.append({
                                                    "row": opt_row,
                                                    "token": opt_tkn,
                                                    "symbol": opt_sym,
                                                    "type": opt_type,
                                                    "strike": opt_strike,
                                                    "strike_label": strike_label,
                                                    "res_opt": res_opt,
                                                    "candle_close": res_opt["close"]
                                                })

                                        if matched_options:
                                            # Fetch quotes for all matching options to compare Open Interest (OI)
                                            symbols_to_quote = [
                                                f"{item['row'].get('exchange', 'NFO')}:{item['symbol']}"
                                                for item in matched_options
                                            ]
                                            quotes = {}
                                            if kite:
                                                try:
                                                    quotes = kite_quote(kite, symbols_to_quote)
                                                except Exception as q_err:
                                                    print(f"[3C OPTION QUOTE ERROR] {q_err}")
                                                    quotes = {}

                                            # Group by option type (CE / PE) so if multiple strikes triggered (e.g. ATM, 1 ITM, 2 ITM),
                                            # we select ONLY the single option with the highest OI out of them
                                            by_type = {}
                                            for item in matched_options:
                                                sym_key = f"{item['row'].get('exchange', 'NFO')}:{item['symbol']}"
                                                q = quotes.get(sym_key, {})
                                                oi = q.get("oi", 0)
                                                ltp = float(q.get("last_price", 0.0))
                                                if oi <= 0:
                                                    ws_q = get_token_quotes([item["token"]]).get(str(item["token"]), {})
                                                    oi = ws_q.get("oi", 0)
                                                    if ltp <= 0:
                                                        ltp = float(ws_q.get("last_price", 0.0))

                                                item["oi"] = oi
                                                item["ltp"] = ltp if ltp > 0 else item["candle_close"]
                                                by_type.setdefault(item["type"], []).append(item)

                                            for opt_type, items in by_type.items():
                                                # Pick the one with the highest OI (tie-breaker: closest to ATM)
                                                best_opt = max(
                                                    items,
                                                    key=lambda x: (
                                                        x["oi"],
                                                        -abs(x["strike"] - atm_strike)
                                                    )
                                                )
                                                res_opt = best_opt["res_opt"]
                                                action_verb = f"BUY {opt_type}" if "EXPANSION" in res_opt["type"] or "REVERSAL" in res_opt["type"] else "WATCH / EXIT"
                                                oi_text = f" (OI: {best_opt['oi']:,})" if best_opt["oi"] > 0 else ""
                                                msg = (
                                                    f"📊 *1H 3C OPTION: {res_opt['sentiment']}*\n"
                                                    f"Option: *{best_opt['symbol']} {best_opt['strike_label']}*{oi_text}\n"
                                                    f"LTP: *₹{best_opt['ltp']:.2f}*\n"
                                                    f"Pattern: {res_opt['condition']}\n"
                                                    f"━━━━━━━━━━━━━━━━━━━\n"
                                                    f"Action: *{action_verb}*\n"
                                                    f"TIME: {now.strftime('%H:%M:%S')}"
                                                )
                                                send_telegram_message(msg, chat_id=send_channel, token=send_token)
            except Exception as e:
                print(f"[3-CANDLE DIVERGENCE] Worker loop error: {e}")
            time.sleep(30)

    threading.Thread(target=scanner_loop, daemon=True).start()


# ------------------------------------------------------------------------------
# 7. UNIFIED MASTER SCANNER STARTER
# ------------------------------------------------------------------------------
def start_unified_scanners(kite=None):
    """
    Spawns all consolidated scanner engines under 1 single shared WebSocket pipeline:
    - Spot & Future Volume Scanner
    - 1-Hour Narrow Range (NR-1H) 15-Minute Option Breakout Scanner
    - 0-DTE Expiry Gamma Exposure & Afternoon Hero-Zero Engine
    - 2-Candle Relative Volume (RVOL) Breakout Scanner
    - All-F&O Institutional 1-Minute Volume Shock Scanner
    - 1-Hour 3-Candle Price & Volume Divergence Scanner (Futures & ATM+3 ITM Options)
    """
    print("🚀 Initializing Consolidated Unified Market Scanners...")
    start_spot_volume_scanner(kite)
    start_nr_option_breakout_scanner(kite)
    start_expiry_gamma_scanner(kite)
    start_rvol_2candle_breakout_scanner(kite)
    start_fo_institutional_breakout_scanner(kite)
    start_3candle_price_volume_divergence_scanner(kite)
    try:
        from pattern_volume_scanner import start_pattern_volume_scanner
        start_pattern_volume_scanner(kite)
    except Exception as e:
        print(f"Failed to start Pattern Volume Scanner: {e}")
    print("✅ All Consolidated Alert Engines Active on Single Shared WebSocket.")


