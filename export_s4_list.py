import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from kiteconnect import KiteConnect

from env_config import API_KEY
from heatmap_engine import get_historical_data_cached, load_stock_futures_data

IST = ZoneInfo("Asia/Kolkata")
TOKEN_FILE = "access_token.txt"


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
            }
        )
    return contracts


def _compute_s4_for_contract(kite, contract):
    now_ist = datetime.now(IST)
    current_week_start = now_ist.date() - timedelta(days=now_ist.weekday())
    prev_week_start = current_week_start - timedelta(days=7)
    prev_week_end = prev_week_start + timedelta(days=4)

    from_time = datetime.combine(prev_week_start, datetime.strptime("09:15", "%H:%M").time(), tzinfo=IST)
    to_time = datetime.combine(prev_week_end, datetime.strptime("15:30", "%H:%M").time(), tzinfo=IST)

    quote = kite.quote([contract["symbol"]]).get(contract["symbol"], {})
    ltp = float(quote.get("last_price", 0) or 0)
    if ltp <= 0:
        return None

    candles = get_historical_data_cached(kite, contract["token"], from_time, to_time, "60minute")
    if not candles:
        return None

    high = max(float(c.get("high", 0) or 0) for c in candles)
    low = min(float(c.get("low", 0) or 0) for c in candles)
    prev_close = float(candles[-1].get("close", 0) or 0)
    if high <= 0 or low <= 0 or prev_close <= 0:
        return None

    pivot = (high + low + prev_close) / 3
    s4 = pivot - (3 * (high - low))
    diff_pct = ((ltp - s4) / s4) * 100 if s4 else None

    return {
        "name": contract["name"],
        "symbol": contract["symbol"],
        "expiry": contract["expiry"].strftime("%Y-%m-%d"),
        "ltp": round(ltp, 2),
        "high": round(high, 2),
        "low": round(low, 2),
        "prev_close": round(prev_close, 2),
        "pivot": round(pivot, 2),
        "s4": round(s4, 2),
        "diff_pct": round(diff_pct, 2) if diff_pct is not None else None,
        "time": now_ist.strftime("%Y-%m-%d %H:%M:%S"),
    }


def main():
    kite = _build_kite()
    if not kite:
        print("access token missing")
        return 1

    rows = []
    for contract in _active_stock_future_contracts():
        try:
            snapshot = _compute_s4_for_contract(kite, contract)
            if snapshot:
                rows.append(snapshot)
        except Exception as e:
            rows.append({"name": contract["name"], "error": str(e)})

    if not rows:
        print("No S4 rows generated")
        return 2

    df = pd.DataFrame(rows).sort_values("name")
    csv_path = os.path.abspath("s4_list.csv")
    xlsx_path = os.path.abspath("s4_list.xlsx")
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
