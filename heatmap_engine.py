import pandas as pd
from datetime import datetime, timedelta

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

last_oi_store = {}
option_history = {}
active_watches = {}

# -------------------------------
# MAIN FUNCTION
# -------------------------------

def calculate_heatmap(kite):

    fut_symbols = []
    for name in BANK_NAMES:
        fut_symbols.append(f"NFO:{name}FUT")

    all_symbols = fut_symbols + [INDEX_SYMBOL]

    try:
        data = kite.quote(all_symbols)
    except Exception as e:
        return 0, f"Error: {e}", [], [], []

    score = 0
    report = "📊 *BANK MOVEMENT (FUTURES)*\n\n"

    bn_alerts = []
    stock_alerts = []
    velocity_alerts = []

    short_names = {
        "HDFCBANK": "HDBFU",
        "ICICIBANK": "ICIBFU",
        "SBIN": "SBINFU",
        "AXISBANK": "AXISFU",
        "KOTAKBANK": "KOTFU"
    }

    # -------------------------------
    # BANK LOOP
    # -------------------------------
    for s in fut_symbols:

        if s not in data:
            continue

        d = data[s]
        ltp = d["last_price"]
        open_p = d["ohlc"]["open"]
        oi = d.get("oi", 0)

        change = ((ltp - open_p) / open_p) * 100 if open_p > 0 else 0

        name = next((n for n in BANK_NAMES if n in s), "UNKNOWN")

        weighted = (change / 100) * BANK_WEIGHTS.get(name, 0)
        score += weighted * 100

        # OI change
        oi_increase_lots = 0
        if name in last_oi_store:
            oi_increase_lots = int((oi - last_oi_store[name]) / LOT_SIZES.get(name, 1))

        last_oi_store[name] = oi

        # FIXED ICON
        oi_icon = "⬆️" if oi_increase_lots >= 0 else "⬇️"

        oi_str = f"{oi/1000000:.1f}M" if oi >= 1000000 else f"{oi/1000:.0f}K"

        report += (
            f"{short_names.get(name, name)}={ltp} , "
            f"COP%={change:+.2f}% , "
            f"TOI: {oi_str},OI{oi_icon}={abs(oi_increase_lots)}LOT\n"
        )

    # -------------------------------
    # BANKNIFTY SECTION (FIXED)
    # -------------------------------
    if INDEX_SYMBOL in data:

        idx_d = data[INDEX_SYMBOL]

        ltp = idx_d["last_price"]
        open_p = idx_d["ohlc"]["open"]
        oi = idx_d.get("oi", 0)

        change = ((ltp - open_p) / open_p) * 100 if open_p > 0 else 0

        idx_oi_increase_lots = int(
            (oi - last_oi_store.get("BANKNIFTY", oi)) / LOT_SIZES["BANKNIFTY"]
        )

        last_oi_store["BANKNIFTY"] = oi

        # ✅ FIXED (IMPORTANT)
        oi_icon = "⬆️" if idx_oi_increase_lots >= 0 else "⬇️"

        pcr = 1.0  # simplified safe fallback

        report += (
            f"\nBANKNIFTY={ltp} , "
            f"COP%={change:+.2f}% , "
            f"OI{oi_icon}={abs(idx_oi_increase_lots)}LOT, "
            f"PCR-{pcr:.2f}\n"
        )

    # -------------------------------
    # FINAL SCORE STATUS
    # -------------------------------
    report += f"\n⚖️ *SENTIMENT SCORE*: {score:.2f}\n"

    if score > 30:
        report += "🚀 *STATUS: STRONG BULLISH*"
    elif score < -30:
        report += "📉 *STATUS: STRONG BEARISH*"
    else:
        report += "⚖️ *STATUS: SIDEWAYS*"

    return score, report, bn_alerts, stock_alerts, velocity_alerts
