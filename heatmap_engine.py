import pandas as pd

BANK_WEIGHTS = {
"HDFCBANK":26,
"SBIN":20,
"ICICIBANK":19,
"KOTAKBANK":8,
"AXISBANK":7
}

BANK_SYMBOLS = [
"NSE:HDFCBANK",
"NSE:SBIN",
"NSE:ICICIBANK",
"NSE:KOTAKBANK",
"NSE:AXISBANK"
]

def calculate_heatmap(kite):

    data = kite.quote(BANK_SYMBOLS)

    score = 0

    for s in data:

        ltp = data[s]["last_price"]
        open_price = data[s]["ohlc"]["open"]

        change = ((ltp-open_price)/open_price)*100

        name = s.split(":")[1]

        weighted = change * BANK_WEIGHTS[name]

        score += weighted

    return score
