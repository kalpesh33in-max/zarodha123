import os
import re
from io import StringIO
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from flask import Flask, Response, jsonify, request
from kiteconnect import KiteConnect

from env_config import API_KEY
from heatmap_engine import (
    get_historical_data_cached,
    load_stock_futures_data,
)
from run_kite import load_saved_token

IST = ZoneInfo("Asia/Kolkata")
app = Flask(__name__)
NIFTY500_LIST_FILE = os.getenv("NIFTY500_LIST_FILE", "nifty500.csv")


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
                "lot_size": int(float(row["lot_size"])) if pd.notna(row.get("lot_size")) else None,
            }
        )
    return contracts


def _load_nifty500_names():
    if not os.path.exists(NIFTY500_LIST_FILE):
        return None

    try:
        df = pd.read_csv(NIFTY500_LIST_FILE)
    except Exception:
        with open(NIFTY500_LIST_FILE, "r", encoding="utf-8-sig") as f:
            text = f.read()
        rows = [line.strip() for line in text.splitlines() if line.strip()]
        return {re.sub(r"[^A-Z0-9&.-]", "", row.upper()) for row in rows}

    candidates = []
    for col in df.columns:
        if any(key in col.lower() for key in ("symbol", "name", "company", "security")):
            candidates.extend(df[col].dropna().astype(str).tolist())

    if not candidates and len(df.columns) == 1:
        candidates = df.iloc[:, 0].dropna().astype(str).tolist()

    return {re.sub(r"[^A-Z0-9&.-]", "", item.upper()) for item in candidates}


def _filter_nifty500_contracts(contracts):
    names = _load_nifty500_names()
    if not names:
        return contracts
    return [c for c in contracts if re.sub(r"[^A-Z0-9&.-]", "", c["name"].upper()) in names]


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


def _build_kite():
    token = load_saved_token()
    if not token:
        return None
    kite = KiteConnect(api_key=API_KEY)
    kite.set_access_token(token)
    return kite


@app.get("/health")
def health():
    return jsonify({"ok": True})


@app.get("/s4")
def s4_single():
    name = request.args.get("name", "RELIANCE").strip().upper()
    kite = _build_kite()
    if not kite:
        return jsonify({"error": "access token missing"}), 400

    contract = next((c for c in _active_stock_future_contracts() if c["name"] == name), None)
    if not contract:
        return jsonify({"error": f"no active future found for {name}"}), 404

    snapshot = _compute_s4_for_contract(kite, contract)
    if not snapshot:
        return jsonify({"error": f"could not compute S4 for {name}"}), 500
    return jsonify(snapshot)


@app.get("/nifty500-s4")
def nifty500_s4():
    kite = _build_kite()
    if not kite:
        return jsonify({"error": "access token missing"}), 400

    contracts = _filter_nifty500_contracts(_active_stock_future_contracts())
    rows = []
    for contract in contracts:
        try:
            snapshot = _compute_s4_for_contract(kite, contract)
            if snapshot:
                rows.append(snapshot)
        except Exception as e:
            rows.append({"name": contract["name"], "error": str(e)})

    rows.sort(key=lambda item: item.get("name", ""))
    return jsonify({"count": len(rows), "data": rows})


@app.get("/nifty500-s4.csv")
def nifty500_s4_csv():
    kite = _build_kite()
    if not kite:
        return jsonify({"error": "access token missing"}), 400

    contracts = _filter_nifty500_contracts(_active_stock_future_contracts())
    rows = []
    for contract in contracts:
        try:
            snapshot = _compute_s4_for_contract(kite, contract)
            if snapshot:
                rows.append(snapshot)
        except Exception as e:
            rows.append({"name": contract["name"], "error": str(e)})

    df = pd.DataFrame(rows)
    csv_buffer = StringIO()
    df.to_csv(csv_buffer, index=False)
    return Response(
        csv_buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=nifty500_s4.csv"},
    )


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8090"))
    app.run(host="0.0.0.0", port=port)
