import io
import os
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from kiteconnect import KiteConnect

from env_config import API_KEY, TELE_TOKEN, TELE_CHAT_ID
from heatmap_engine import get_historical_data_cached, load_stock_futures_data

IST = ZoneInfo("Asia/Kolkata")
NIFTY500_LIST_FILE = os.getenv("NIFTY500_LIST_FILE", "nifty500.csv")
TARGET_TIME = os.getenv("TARGET_TIME", "15:00")
RUN_NOW = os.getenv("RUN_NOW", "false").lower() in ("1", "true", "yes", "on")
TOKEN_FILE = "access_token.txt"


def _load_saved_token():
    token = os.getenv("KITE_ACCESS_TOKEN", "").strip()
    if token:
        return token
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            token = f.read().strip()
        if token:
            return token
    return None


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


def _s4_from_candles(candles):
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
    return high, low, prev_close, pivot, s4


def _range_1h(now_ist):
    current_week_start = now_ist.date() - timedelta(days=now_ist.weekday())
    prev_week_start = current_week_start - timedelta(days=7)
    prev_week_end = prev_week_start + timedelta(days=4)
    return (
        datetime.combine(prev_week_start, datetime.strptime("09:15", "%H:%M").time(), tzinfo=IST),
        datetime.combine(prev_week_end, datetime.strptime("15:30", "%H:%M").time(), tzinfo=IST),
    )


def _range_daily(now_ist):
    current_week_start = now_ist.date() - timedelta(days=now_ist.weekday())
    prev_week_start = current_week_start - timedelta(days=7)
    prev_week_end = prev_week_start + timedelta(days=4)
    return (
        datetime.combine(prev_week_start, datetime.strptime("09:15", "%H:%M").time(), tzinfo=IST),
        datetime.combine(prev_week_end, datetime.strptime("15:30", "%H:%M").time(), tzinfo=IST),
    )


def _range_weekly(now_ist):
    start = now_ist.date() - timedelta(days=28)
    end = now_ist.date()
    return (
        datetime.combine(start, datetime.strptime("09:15", "%H:%M").time(), tzinfo=IST),
        datetime.combine(end, datetime.strptime("15:30", "%H:%M").time(), tzinfo=IST),
    )


def _quote_ltp(kite, symbol):
    try:
        q = kite.quote([symbol]).get(symbol, {})
        return float(q.get("last_price", 0) or 0)
    except Exception:
        return 0.0


def build_report():
    kite = _build_kite()
    if not kite:
        raise RuntimeError("access token missing")

    now_ist = datetime.now(IST)
    contracts = _filter_nifty500_contracts(_active_stock_future_contracts())
    if not contracts:
        raise RuntimeError("no contracts found")

    rows = []
    one_h_from, one_h_to = _range_1h(now_ist)
    day_from, day_to = _range_daily(now_ist)
    week_from, week_to = _range_weekly(now_ist)

    for contract in contracts:
        try:
            ltp = _quote_ltp(kite, contract["symbol"])
            one_h = get_historical_data_cached(kite, contract["token"], one_h_from, one_h_to, "60minute")
            daily = get_historical_data_cached(kite, contract["token"], day_from, day_to, "day")
            weekly = get_historical_data_cached(kite, contract["token"], week_from, week_to, "day")

            row = {
                "name": contract["name"],
                "symbol": contract["symbol"],
                "expiry": contract["expiry"].strftime("%Y-%m-%d"),
                "month_label": contract["month_label"],
                "ltp": round(ltp, 2) if ltp else None,
                "report_date": now_ist.strftime("%Y-%m-%d"),
            }
            for prefix, candles in (("1h", one_h), ("daily", daily), ("weekly", weekly)):
                data = _s4_from_candles(candles)
                if data:
                    high, low, prev_close, pivot, s4 = data
                    row[f"{prefix}_high"] = high
                    row[f"{prefix}_low"] = low
                    row[f"{prefix}_prev_close"] = prev_close
                    row[f"{prefix}_pivot"] = pivot
                    row[f"{prefix}_s4"] = s4
                    row[f"{prefix}_diff_pct"] = round(((ltp - s4) / s4) * 100, 2) if ltp else None
                else:
                    row[f"{prefix}_s4"] = None
                    row[f"{prefix}_diff_pct"] = None
            rows.append(row)
        except Exception as e:
            rows.append({"name": contract["name"], "symbol": contract["symbol"], "error": str(e), "report_date": now_ist.strftime("%Y-%m-%d")})

    df = pd.DataFrame(rows).sort_values("name")
    return df, now_ist


def upload_to_telegram(df, now_ist):
    if not TELE_TOKEN or not TELE_CHAT_ID:
        raise RuntimeError("telegram token/chat_id missing")

    filename = f"s4_report_{now_ist.strftime('%Y%m%d')}.xlsx"
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="S4")
    buffer.seek(0)

    url = f"https://api.telegram.org/bot{TELE_TOKEN}/sendDocument"
    files = {"document": (filename, buffer, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    data = {"chat_id": TELE_CHAT_ID, "caption": f"S4 report for {now_ist.strftime('%Y-%m-%d')} IST"}
    response = requests.post(url, data=data, files=files, timeout=60)
    response.raise_for_status()
    return response.json()


def wait_until_target():
    if RUN_NOW:
        return
    target_hour, target_minute = map(int, TARGET_TIME.split(":", 1))
    while True:
        now_ist = datetime.now(IST)
        target = now_ist.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
        if now_ist >= target:
            return
        sleep_seconds = min(60, int((target - now_ist).total_seconds()))
        time.sleep(max(1, sleep_seconds))


def main():
    wait_until_target()
    df, now_ist = build_report()
    local_xlsx = f"s4_report_{now_ist.strftime('%Y%m%d')}.xlsx"
    df.to_excel(local_xlsx, index=False)
    upload_to_telegram(df, now_ist)
    print(f"Saved and uploaded {local_xlsx}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
