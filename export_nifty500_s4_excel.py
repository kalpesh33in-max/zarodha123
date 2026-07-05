import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from kiteconnect import KiteConnect

from env_config import API_KEY
from heatmap_engine import get_historical_data_cached, load_stock_futures_data

IST = ZoneInfo("Asia/Kolkata")
TOKEN_FILE = "access_token.txt"
NIFTY500_LIST_FILE = os.getenv("NIFTY500_LIST_FILE", "nifty500.csv")


def _load_saved_token():
    if not os.path.exists(TOKEN_FILE):
        return None
    with open(TOKEN_FILE, "r", encoding="utf-8") as f:
        token = f.read().strip()
    return token or None


def _build_kite():
    token = _load_saved_token()
    if not token:
        return None
    kite = KiteConnect(api_key=API_KEY)
    kite.set_access_token(token)
    return kite


def _load_nifty500_names():
    if not os.path.exists(NIFTY500_LIST_FILE):
        return None
    try:
        df = pd.read_csv(NIFTY500_LIST_FILE)
        candidates = []
        for col in df.columns:
            if any(key in col.lower() for key in ("symbol", "name", "company", "security")):
                candidates.extend(df[col].dropna().astype(str).tolist())
        if not candidates and len(df.columns) == 1:
            candidates = df.iloc[:, 0].dropna().astype(str).tolist()
    except Exception:
        with open(NIFTY500_LIST_FILE, "r", encoding="utf-8-sig") as f:
            candidates = [line.strip() for line in f if line.strip()]

    return {re.sub(r"[^A-Z0-9&.-]", "", item.upper()) for item in candidates}


def _filter_nifty500_contracts(contracts):
    names = _load_nifty500_names()
    if not names:
        return contracts
    return [c for c in contracts if re.sub(r"[^A-Z0-9&.-]", "", c["name"].upper()) in names]


def _active_stock_future_contracts():
    df = load_stock_futures_data()
    if df.empty:
        return []

    df = df.sort_values(["name", "expiry", "tradingsymbol"])
    contracts = []
    for name, rows in df.groupby("name"):
        selected = rows[rows["expiry"] == rows["expiry"].max()]
        if selected.empty:
            continue
        row = selected.iloc[0]
        contracts.append(
            {
                "name": name,
                "symbol": f"{row.get('exchange', 'NFO')}:{row['tradingsymbol']}",
                "token": int(row["instrument_token"]),
                "expiry": row["expiry"],
                "month_label": row["expiry"].strftime("%b").upper(),
            }
        )
    return contracts


def _session_range_for_1h(now_ist):
    current_week_start = now_ist.date() - timedelta(days=now_ist.weekday())
    prev_week_start = current_week_start - timedelta(days=7)
    prev_week_end = prev_week_start + timedelta(days=4)
    return (
        datetime.combine(prev_week_start, datetime.strptime("09:15", "%H:%M").time(), tzinfo=IST),
        datetime.combine(prev_week_end, datetime.strptime("15:30", "%H:%M").time(), tzinfo=IST),
    )


def _daily_range_for_previous_week(now_ist):
    current_week_start = now_ist.date() - timedelta(days=now_ist.weekday())
    prev_week_start = current_week_start - timedelta(days=7)
    prev_week_end = prev_week_start + timedelta(days=4)
    return (
        datetime.combine(prev_week_start, datetime.strptime("09:15", "%H:%M").time(), tzinfo=IST),
        datetime.combine(prev_week_end, datetime.strptime("15:30", "%H:%M").time(), tzinfo=IST),
    )


def _weekly_range_for_previous_4_weeks(now_ist):
    start = now_ist.date() - timedelta(days=28)
    end = now_ist.date() - timedelta(days=1)
    return (
        datetime.combine(start, datetime.strptime("09:15", "%H:%M").time(), tzinfo=IST),
        datetime.combine(end, datetime.strptime("15:30", "%H:%M").time(), tzinfo=IST),
    )


def _compute_s4_from_candles(candles):
    if not candles:
        return None
    high = max(float(c.get("high", 0) or 0) for c in candles)
    low = min(float(c.get("low", 0) or 0) for c in candles)
    prev_close = float(candles[-1].get("close", 0) or 0)
    if high <= 0 or low <= 0 or prev_close <= 0:
        return None
    pivot = (high + low + prev_close) / 3
    s4 = pivot - (3 * (high - low))
    if s4 <= 0:
        return None
    return {
        "high": round(high, 2),
        "low": round(low, 2),
        "prev_close": round(prev_close, 2),
        "pivot": round(pivot, 2),
        "s4": round(s4, 2),
    }


def _quote_ltp(kite, symbol):
    try:
        quote = kite.quote([symbol]).get(symbol, {})
        return float(quote.get("last_price", 0) or 0)
    except Exception:
        return 0.0


def _compute_row(kite, contract):
    now_ist = datetime.now(IST)
    row = {
        "name": contract["name"],
        "symbol": contract["symbol"],
        "expiry": contract["expiry"].strftime("%Y-%m-%d"),
        "month_label": contract["month_label"],
    }

    ltp = _quote_ltp(kite, contract["symbol"])
    row["ltp"] = round(ltp, 2) if ltp else None

    one_h_from, one_h_to = _session_range_for_1h(now_ist)
    daily_from, daily_to = _daily_range_for_previous_week(now_ist)
    weekly_from, weekly_to = _weekly_range_for_previous_4_weeks(now_ist)

    one_h = get_historical_data_cached(kite, contract["token"], one_h_from, one_h_to, "60minute")
    daily = get_historical_data_cached(kite, contract["token"], daily_from, daily_to, "day")
    weekly_days = get_historical_data_cached(kite, contract["token"], weekly_from, weekly_to, "day")

    one_h_s4 = _compute_s4_from_candles(one_h)
    daily_s4 = _compute_s4_from_candles(daily)
    weekly_s4 = _compute_s4_from_candles(weekly_days)

    for prefix, payload in (("1h", one_h_s4), ("daily", daily_s4), ("weekly", weekly_s4)):
        if payload:
            row[f"{prefix}_high"] = payload["high"]
            row[f"{prefix}_low"] = payload["low"]
            row[f"{prefix}_prev_close"] = payload["prev_close"]
            row[f"{prefix}_pivot"] = payload["pivot"]
            row[f"{prefix}_s4"] = payload["s4"]
            if ltp:
                row[f"{prefix}_diff_pct"] = round(((ltp - payload["s4"]) / payload["s4"]) * 100, 2)
        else:
            row[f"{prefix}_s4"] = None
            row[f"{prefix}_diff_pct"] = None

    row["time"] = now_ist.strftime("%Y-%m-%d %H:%M:%S")
    return row


def main():
    kite = _build_kite()
    if not kite:
        print("access token missing")
        return 1

    contracts = _filter_nifty500_contracts(_active_stock_future_contracts())
    if not contracts:
        print("No contracts found")
        return 2

    rows = []
    for contract in contracts:
        try:
            rows.append(_compute_row(kite, contract))
        except Exception as e:
            rows.append({"name": contract["name"], "symbol": contract["symbol"], "error": str(e)})

    df = pd.DataFrame(rows).sort_values("name")
    csv_path = os.path.abspath("nifty500_s4_report.csv")
    xlsx_path = os.path.abspath("nifty500_s4_report.xlsx")
    df.to_csv(csv_path, index=False)
    try:
        df.to_excel(xlsx_path, index=False)
    except Exception as e:
        print(f"Excel export failed: {e}")
    print(f"Saved: {csv_path}")
    if os.path.exists(xlsx_path):
        print(f"Saved: {xlsx_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
