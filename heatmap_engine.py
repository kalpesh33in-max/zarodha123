import pandas as pd
from datetime import datetime, timedelta

BANK_WEIGHTS = {
    "HDFCBANK": 26,
    "SBIN": 20,
    "ICICIBANK": 19,
    "KOTAKBANK": 8,
    "AXISBANK": 7
}

BANK_SYMBOLS = [
    "NSE:HDFCBANK",
    "NSE:SBIN",
    "NSE:ICICIBANK",
    "NSE:KOTAKBANK",
    "NSE:AXISBANK"
]

INDEX_SYMBOL = "NSE:NIFTY BANK"

def calculate_cpr(kite, instrument_token):
    # Fetch previous day OHLC for CPR calculation
    # We use 1-day interval, for the last 2 days to get the previous day's candle
    to_date = datetime.now().date()
    from_date = to_date - timedelta(days=5) # Buffer for weekends
    
    try:
        hist = kite.historical_data(instrument_token, from_date, to_date, "day")
        if len(hist) >= 1:
            prev_day = hist[-1] # This is usually the last closed candle
            h, l, c = prev_day['high'], prev_day['low'], prev_day['close']
            
            pivot = (h + l + c) / 3
            bc = (h + l) / 2
            tc = (pivot - bc) + pivot
            
            # Sort tc and bc to ensure tc is always top
            real_tc = max(tc, bc)
            real_bc = min(tc, bc)
            
            return {
                "pivot": round(pivot, 2),
                "tc": round(real_tc, 2),
                "bc": round(real_bc, 2)
            }
    except Exception as e:
        print(f"Error calculating CPR: {e}")
    return None

def calculate_heatmap(kite):
    data = kite.quote(BANK_SYMBOLS)
    score = 0

    report = "📊 *MARKET HEATMAP*\n\n"

    for s in data:
        ltp = data[s]["last_price"]
        open_price = data[s]["ohlc"]["open"]
        change = ((ltp - open_price) / open_price) * 100
        
        name = s.split(":")[1]
        weighted = (change / 100) * BANK_WEIGHTS[name] # Normalized weighted change
        score += weighted * 100 # Scaling for readability

        report += f"{name}: {change:+.2f}% (W: {weighted:+.2f})\n"

    # Add Index Info
    idx_data = kite.quote([INDEX_SYMBOL])[INDEX_SYMBOL]
    idx_ltp = idx_data["last_price"]
    idx_oi = idx_data.get("oi", 0)
    
    report += f"\n🏦 *BANKNIFTY*: {idx_ltp}\n"
    if idx_oi:
        report += f"🔹 OI: {idx_oi:,}\n"

    # CPR Calculation (using index token)
    # BankNifty Spot token is 260105
    cpr = calculate_cpr(kite, 260105)
    if cpr:
        report += f"\n🎯 *CPR LEVELS*\nTC: {cpr['tc']}\nPivot: {cpr['pivot']}\nBC: {cpr['bc']}\n"
        
        if idx_ltp > cpr['tc']:
            report += "Position: 🟢 ABOVE CPR (Bullish)\n"
        elif idx_ltp < cpr['bc']:
            report += "Position: 🔴 BELOW CPR (Bearish)\n"
        else:
            report += "Position: 🟡 INSIDE CPR (Neutral)\n"

    return score, report
