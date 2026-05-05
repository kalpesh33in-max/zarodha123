import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from websocket_flow import get_symbol_quotes, get_token_quotes

LOT_SIZES = {
    "NIFTY": 65,
    "BANKNIFTY": 30,
    "FINNIFTY": 60,
    "MIDCPNIFTY": 120,
    "SENSEX": 20,
    "HDFCBANK": 550,
    "ICICIBANK": 700,
    "RELIANCE": 500,
}

INDEX_BURST_NAMES = {"BANKNIFTY", "NIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"}
STOCK_BURST_NAMES = {"HDFCBANK", "ICICIBANK", "RELIANCE"}
BURST_TRACK_NAMES = [
    "BANKNIFTY",
    "NIFTY",
    "FINNIFTY",
    "MIDCPNIFTY",
    "SENSEX",
    "HDFCBANK",
    "ICICIBANK",
    "RELIANCE",
]
INDEX_SYMBOL = "NSE:NIFTY BANK"
INDEX_FUTURE_NAMES = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX", "SENSEX50"}

day_open_oi_store = {}
option_history = {}
active_watches = {}
gap_alert_store = {}
r3_alert_store = {}
r3_last_check_time = None
r3_watch_last_sent_time = None

_options_df = None
_futures_df = None
_last_logged_expiry = {}

IST = ZoneInfo("Asia/Kolkata")
MAY_FUTURE_GAP_THRESHOLD_PCT = 2.0
MAY_FUTURE_GAP_START_TIME = datetime.strptime("09:15", "%H:%M").time()
GAP_ALERT_COOLDOWN_SECONDS = 300
R3_PIVOT_ALERT_START_TIME = datetime.strptime("09:15", "%H:%M").time()
R3_PIVOT_CLOSE_REMINDER_START_TIME = datetime.strptime("15:00", "%H:%M").time()
R3_PIVOT_RANGE_PCT = 0.5
R3_PIVOT_CHECK_INTERVAL_SECONDS = 300
R3_PIVOT_REMINDER_SECONDS = 3600
R3_PIVOT_CLOSE_REMINDER_SECONDS = 600


def is_index_underlying(name):
    return name in INDEX_BURST_NAMES


def is_burst_underlying(name):
    return name in INDEX_BURST_NAMES or name in STOCK_BURST_NAMES


def get_burst_threshold(name):
    # Burst threshold in lots:
    # - Index underlyings: 100 lots
    # - Stock underlyings: 50 lots
    return 100 if is_index_underlying(name) else 50


def get_monthly_expiry(expiries, rollover_days=1):
    valid_expiries = sorted(exp for exp in expiries if pd.notna(exp))
    if not valid_expiries:
        return None

    now_ist = datetime.now(IST)
    month_last_expiries = {}
    for expiry in valid_expiries:
        month_last_expiries[(int(expiry.year), int(expiry.month))] = expiry

    ordered_monthlies = [month_last_expiries[key] for key in sorted(month_last_expiries)]
    current_monthly = None
    for expiry in ordered_monthlies:
        if (int(expiry.year), int(expiry.month)) == (now_ist.year, now_ist.month):
            current_monthly = expiry
            break

    if current_monthly is not None:
        rollover_date = current_monthly.date() - timedelta(days=rollover_days)
        if now_ist.date() >= rollover_date:
            for expiry in ordered_monthlies:
                if expiry > current_monthly:
                    return expiry
        elif current_monthly.date() >= now_ist.date():
            return current_monthly

    future_monthlies = [exp for exp in ordered_monthlies if exp.date() >= now_ist.date()]
    if future_monthlies:
        return future_monthlies[0]
    return ordered_monthlies[-1]


def load_options_data():
    global _options_df
    if _options_df is None:
        try:
            df = pd.read_csv("instruments.csv")
            _options_df = df[df["segment"].isin(["NFO-OPT", "BFO-OPT"])].copy()
            _options_df["expiry"] = pd.to_datetime(_options_df["expiry"], dayfirst=True)
        except Exception as e:
            print(f"Error loading Options: {e}")
    return _options_df


def load_futures_data():
    global _futures_df
    if _futures_df is None:
        try:
            df = pd.read_csv("instruments.csv")
            _futures_df = df[df["segment"].str.contains("-FUT", na=False)].copy()
            _futures_df["expiry"] = pd.to_datetime(_futures_df["expiry"], dayfirst=True)
        except Exception as e:
            print(f"Error loading Futures: {e}")
    return _futures_df


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
    if name == "BANKNIFTY":
        return INDEX_SYMBOL
    return f"NSE:{name}"


def get_active_future(name):
    df = load_futures_data()
    if df is None or df.empty:
        return None
    futures = df[df["name"] == name]
    if futures.empty:
        return None

    preferred_expiry = get_monthly_expiry(futures["expiry"].unique())
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
    for name in BURST_TRACK_NAMES:
        sym = get_active_future(name)
        if sym:
            symbols.append(sym)
    summary_key = "future_summary"
    summary_text = ", ".join(symbols) if symbols else "none"
    if _last_logged_expiry.get(summary_key) != summary_text:
        print(f"Selected tracked futures: {summary_text}")
        _last_logged_expiry[summary_key] = summary_text
    return symbols


def get_stock_may_future_symbols():
    futures = load_stock_futures_data()
    if futures.empty:
        return []

    now_ist = datetime.now(IST)
    may_futures = futures[
        (futures["expiry"].dt.year == now_ist.year)
        & (futures["expiry"].dt.month == 5)
    ].copy()
    if may_futures.empty:
        return []

    may_futures = may_futures.sort_values(["name", "expiry", "tradingsymbol"])
    selected = may_futures.groupby("name", as_index=False).first()
    return [
        (row["name"], f"NFO:{row['tradingsymbol']}")
        for _, row in selected.iterrows()
    ]


def get_relevant_options(name, ltp):
    df = load_options_data()
    if df is None or df.empty:
        return pd.DataFrame()

    options = df[df["name"] == name]
    if options.empty:
        return pd.DataFrame()

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

    strikes = sorted(options["strike"].unique())
    atm = min(strikes, key=lambda x: abs(x - ltp))
    idx = strikes.index(atm)
    rng = 15 if is_index_underlying(name) else 6
    selected = strikes[max(0, idx - rng): idx + rng + 1]
    return options[options["strike"].isin(selected)].copy()


def get_strength_label(lots, name="BANKNIFTY"):
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


def build_may_future_gap_alerts(kite):
    now_ist = datetime.now(IST)
    if now_ist.weekday() > 4 or now_ist.time() < MAY_FUTURE_GAP_START_TIME:
        return []

    future_symbols = get_stock_may_future_symbols()
    if not future_symbols:
        return []

    symbol_pairs = [(name, get_spot_symbol(name), future_symbol) for name, future_symbol in future_symbols]

    quote_symbols = []
    for _, spot_symbol, future_symbol in symbol_pairs:
        quote_symbols.append(spot_symbol)
        quote_symbols.append(future_symbol)

    data = get_symbol_quotes_with_fallback(kite, quote_symbols)
    if not data:
        return []

    now = datetime.now(IST)
    rows = []
    for name, spot_symbol, future_symbol in symbol_pairs:
        spot_price = data.get(spot_symbol, {}).get("last_price", 0)
        future_price = data.get(future_symbol, {}).get("last_price", 0)
        if spot_price <= 0 or future_price <= 0:
            continue

        gap_pct = ((future_price - spot_price) / spot_price) * 100
        if abs(gap_pct) < MAY_FUTURE_GAP_THRESHOLD_PCT:
            continue

        last_sent = gap_alert_store.get(name)
        if last_sent and (now - last_sent).total_seconds() < GAP_ALERT_COOLDOWN_SECONDS:
            continue

        gap_alert_store[name] = now
        rows.append(
            {
                "name": name,
                "spot_price": spot_price,
                "future_price": future_price,
                "gap_pct": gap_pct,
            }
        )

    if not rows:
        return []

    rows.sort(key=lambda item: abs(item["gap_pct"]), reverse=True)
    alerts = []
    chunk_size = 20
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i:i + chunk_size]
        body = "\n".join(
            [
                f"{item['name']}: Spot {item['spot_price']:.2f} | "
                f"May Fut {item['future_price']:.2f} | "
                f"Gap {item['gap_pct']:+.2f}% | {_format_gap_signal(item['gap_pct'])}"
                for item in chunk
            ]
        )
        alerts.append(f"📊 MAY FUTURE GAP REPORT\n\n{body}")
    return alerts


def _get_may_stock_future_contracts():
    futures = load_stock_futures_data()
    if futures.empty:
        return []

    now_ist = datetime.now(IST)
    may_futures = futures[
        (futures["expiry"].dt.year == now_ist.year)
        & (futures["expiry"].dt.month == 5)
    ].copy()
    if may_futures.empty:
        return []

    may_futures = may_futures.sort_values(["name", "expiry", "tradingsymbol"])
    selected = may_futures.groupby("name", as_index=False).first()
    return [
        {
            "name": row["name"],
            "symbol": f"NFO:{row['tradingsymbol']}",
            "token": int(row["instrument_token"]),
        }
        for _, row in selected.iterrows()
    ]


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
        candles = kite.historical_data(token, from_time, to_time, interval)
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


def _get_previous_day_r3_for_interval(kite, token, interval, interval_minutes, now_ist):
    prev_day = _get_previous_trading_day(now_ist)
    from_time = datetime.combine(prev_day, datetime.strptime("09:15", "%H:%M").time(), tzinfo=IST)
    to_time = datetime.combine(prev_day, datetime.strptime("15:30", "%H:%M").time(), tzinfo=IST)
    try:
        candles = kite.historical_data(token, from_time, to_time, interval)
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


def build_may_future_r3_pivot_alerts(kite):
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

    contracts = _get_may_stock_future_contracts()
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
        ("15MIN", "15minute", 15),
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
                candles = kite.historical_data(contract["token"], from_time, to_time, kite_interval)
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

            # Zerodha Kite "standard" pivots use PP=(H+L+C)/3 and:
            # R3 = PP + 2*(H-L), S3 = PP - 2*(H-L)
            # (R3/S3 differ from the classic floor-trader formula.)
            pivot = (high + low + prev_close) / 3
            r3 = pivot + (2 * (high - low))
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
                alert_key = f"R3_NEAR:{contract['name']}:{label}:{now_ist.date().isoformat()}"
                if alert_key not in r3_alert_store:
                    r3_alert_store[alert_key] = now_ist
                    near_rows.append(
                        {
                            "name": contract["name"],
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
                alert_key = f"R3_ABOVE:{contract['name']}:{label}:{now_ist.date().isoformat()}"
                if alert_key not in r3_alert_store:
                    r3_alert_store[alert_key] = now_ist
                    breakout_rows.append(
                        {
                            "name": contract["name"],
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
            f"{item['name']}: Fut {item['ltp']:.2f} | {item['label']} R3 {item['r3']:.2f} "
            f"| Near {item['diff_pct']:+.2f}% | Prev Close {item['prev_close']:.2f} "
            f"({item['close_diff_pct']:+.2f}%)"
            for item in near_rows
        ]
        for i in range(0, len(body_lines), 20):
            chunk = "\n".join(body_lines[i:i + 20])
            alerts.append(f"MAY FUTURE R3 NEAR ALERT\n\n{chunk}\n\nTIME: {now_ist.strftime('%H:%M:%S')} IST")

    if breakout_rows:
        body_lines = [
            f"{item['name']}: Fut {item['ltp']:.2f} | {item['label']} R3 {item['r3']:.2f} "
            f"| Above {item['diff_pct']:+.2f}% | Prev Close {item['prev_close']:.2f} "
            f"({item['close_diff_pct']:+.2f}%)"
            for item in breakout_rows
        ]
        for i in range(0, len(body_lines), 20):
            chunk = "\n".join(body_lines[i:i + 20])
            alerts.append(f"MAY FUTURE R3 BREAKOUT ALERT\n\n{chunk}\n\nTIME: {now_ist.strftime('%H:%M:%S')} IST")

    reminder_seconds = (
        R3_PIVOT_CLOSE_REMINDER_SECONDS
        if now_ist.time() >= R3_PIVOT_CLOSE_REMINDER_START_TIME
        else R3_PIVOT_REMINDER_SECONDS
    )
    reminder_due = (
        watch_rows
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
            body_lines.append(f"{item['name']}: Fut {item['ltp']:.2f} | {pivot_text}")

        for i in range(0, len(body_lines), 20):
            chunk = "\n".join(body_lines[i:i + 20])
            alerts.append(f"MAY FUTURE R3 WATCHLIST\n\n{chunk}\n\nTIME: {now_ist.strftime('%H:%M:%S')} IST")

    return alerts


def process_future_burst(symbol, name, ltp, oi, alerts_list):
    if not is_burst_underlying(name):
        return

    threshold = get_burst_threshold(name)
    lot_size = LOT_SIZES.get(name, 1)
    now = datetime.now(IST)
    key = f"FUT_{symbol}"
    if key not in option_history:
        option_history[key] = []
    history = option_history[key]
    prev_oi = history[-1]["oi"] if history else 0
    prev_price = history[-1]["price"] if history else 0

    if prev_oi > 0:
        tick_lots = int(abs(oi - prev_oi) / lot_size)
        if tick_lots >= threshold and key not in active_watches:
            active_watches[key] = {
                "start_oi": prev_oi,
                "start_price": prev_price,
                "end_time": now + timedelta(seconds=15),
                "symbol": symbol,
                "name": name,
            }

    if key in active_watches:
        watch = active_watches[key]
        if now >= watch["end_time"]:
            oi_chg = oi - watch["start_oi"]
            p_chg = ltp - watch["start_price"]
            final_lots = int(abs(oi_chg) / lot_size)
            if final_lots >= threshold:
                strength = get_strength_label(final_lots, watch["name"])
                action = classify_action(watch["symbol"], oi_chg, p_chg)
                p_icon = "▲" if p_chg >= 0 else "▼"
                alerts_list.append(
                    f"{strength}\n🚨 {action}\nSymbol: {watch['symbol']}\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"LOTS: {final_lots}\nPRICE: {ltp:.2f} ({p_icon})\nFUTURE PRICE: {ltp:.2f}\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"EXISTING OI: {watch['start_oi']:,}\nOI CHANGE  : {oi_chg:+,d}\nNEW OI     : {oi:,}\n"
                    f"TIME: {now.strftime('%H:%M:%S')}"
                )
            del active_watches[key]

    history.append({"time": now, "oi": oi, "price": ltp})
    if len(history) > 20:
        history.pop(0)


def process_option_logic(name, underlying_data, option_quotes, alerts_list):
    if not is_burst_underlying(name):
        return

    opt_df, u_ltp = underlying_data
    if opt_df.empty:
        return

    threshold = get_burst_threshold(name)
    lot_size = LOT_SIZES.get(name, 1)
    now = datetime.now(IST)

    for _, row in opt_df.iterrows():
        t_str = str(int(row["instrument_token"]))
        if t_str not in option_quotes:
            continue

        q = option_quotes[t_str]
        curr_oi = q.get("oi", 0)
        ltp = q.get("last_price", 0)
        t_int = int(row["instrument_token"])

        if t_int not in day_open_oi_store:
            day_open_oi_store[t_int] = curr_oi

        if t_int not in option_history:
            option_history[t_int] = []
        history = option_history[t_int]
        prev_oi = history[-1]["oi"] if history else 0
        prev_price = history[-1]["price"] if history else 0

        if prev_oi > 0:
            tick_lots = int(abs(curr_oi - prev_oi) / lot_size)
            if tick_lots >= threshold and t_int not in active_watches:
                active_watches[t_int] = {
                    "start_oi": prev_oi,
                    "start_price": prev_price,
                    "end_time": now + timedelta(seconds=15),
                    "symbol": row["tradingsymbol"],
                    "underlying": name,
                }

        if t_int in active_watches:
            watch = active_watches[t_int]
            if now >= watch["end_time"]:
                oi_chg = curr_oi - watch["start_oi"]
                p_chg = ltp - watch["start_price"]
                final_lots = int(abs(oi_chg) / lot_size)
                if final_lots >= threshold:
                    strength = get_strength_label(final_lots, watch["underlying"])
                    action = classify_action(watch["symbol"], oi_chg, p_chg)
                    p_icon = "▲" if p_chg >= 0 else "▼"
                    alerts_list.append(
                        f"{strength}\n🚨 {action}\nSymbol: {watch['symbol']}\n"
                        f"━━━━━━━━━━━━━━━\n"
                        f"LOTS: {final_lots}\nPRICE: {ltp:.2f} ({p_icon})\nFUTURE PRICE: {u_ltp:.2f}\n"
                        f"━━━━━━━━━━━━━━━\n"
                        f"EXISTING OI: {watch['start_oi']:,}\nOI CHANGE  : {oi_chg:+,d}\nNEW OI     : {curr_oi:,}\n"
                        f"TIME: {now.strftime('%H:%M:%S')}"
                    )
                del active_watches[t_int]

        history.append({"time": now, "oi": curr_oi, "price": ltp})
        if len(history) > 20:
            history.pop(0)


def calculate_heatmap(kite):
    fut_symbols = get_bank_futures(kite)
    symbols = fut_symbols + [INDEX_SYMBOL]
    data = get_symbol_quotes_with_fallback(kite, symbols)
    if not data:
        return 0, "", [], [], []

    bn_alerts = []
    stock_alerts = []
    gap_alerts = []

    # Map tracked future symbols by underlying name using an exact prefix match on tradingsymbol.
    # This avoids substring collisions (e.g. "NIFTY" matching "BANKNIFTY").
    fut_by_name = {}
    for sym in fut_symbols:
        try:
            tsym = sym.split(":", 1)[1]
        except Exception:
            continue
        for name in BURST_TRACK_NAMES:
            if tsym.startswith(name):
                fut_by_name[name] = sym

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

    gap_alerts = build_may_future_gap_alerts(kite)
    gap_alerts.extend(build_may_future_r3_pivot_alerts(kite))
    return 0, "", bn_alerts, stock_alerts, gap_alerts
