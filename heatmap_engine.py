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

# ================= FUTURE BURST (UPDATED ONLY HERE) =================
def process_future_burst(symbol, name, ltp, oi, alerts_list):

    lot_size = LOT_SIZES.get(name, 1)

    # 🔥 ONLY CHANGE → threshold 500
    threshold = 500

    now = datetime.now()

    key = f"FUT_{symbol}"

    if key not in option_history:
        option_history[key] = []

    history = option_history[key]

    prev_oi = history[-1]['oi'] if history else 0
    prev_price = history[-1]['price'] if history else 0

    if prev_oi > 0:
        tick_lots = int(abs(oi - prev_oi) / lot_size)

        if tick_lots >= threshold and key not in active_watches:
            active_watches[key] = {
                "start_oi": prev_oi,
                "start_price": prev_price,
                "end_time": now + timedelta(minutes=1),
                "symbol": symbol,
                "name": name
            }

    if key in active_watches:
        watch = active_watches[key]

        if now >= watch["end_time"]:

            final_oi_chg = oi - watch["start_oi"]
            final_price_chg = ltp - watch["start_price"]
            final_lots = int(abs(final_oi_chg) / lot_size)

            if final_lots >= threshold:

                strength = "🚀 BLAST 🚀" if final_lots >= 400 else "⚡ GOOD"

                action = (
                    "FUTURE BUY (LONG) 📈" if final_oi_chg > 0 and final_price_chg >= 0 else
                    "FUTURE SELL (SHORT) 📉" if final_oi_chg > 0 else
                    "SHORT COVERING ↗️" if final_price_chg >= 0 else
                    "LONG UNWINDING ↘️"
                )

                price_icon = "▲" if final_price_chg >= 0 else "▼"

                alerts_list.append(
                    f"{strength}\n🚨 {action}\nSymbol: {symbol}\n━━━━━━━━━━━━━━━\nLOTS: {final_lots}\n"
                    f"PRICE: {ltp:.2f} ({price_icon})\nFUTURE PRICE: {ltp:.2f}\n"
                    f"━━━━━━━━━━━━━━━\nEXISTING OI: {watch['start_oi']:,}\nOI CHANGE  : {final_oi_chg:+,d}\nNEW OI     : {oi:,}\nTIME: {now.strftime('%H:%M:%S')}"
                )

            del active_watches[key]

    history.append({'time': now, 'oi': oi, 'price': ltp})
    if len(history) > 20:
        history.pop(0)
