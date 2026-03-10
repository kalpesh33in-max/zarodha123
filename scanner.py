import pandas as pd
from heatmap_engine import calculate_heatmap

def run_scanner(kite):

    score = calculate_heatmap(kite)

    if score > 30:

        print("BANKNIFTY STRONG BULLISH")

    elif score < -30:

        print("BANKNIFTY STRONG BEARISH")

    else:

        print("SIDEWAYS")
