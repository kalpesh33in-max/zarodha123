from kiteconnect import KiteTicker
from heatmap_engine import detect_velocity_futures

class FlowEngine:

    def __init__(self, api_key, access_token, tokens):
        self.tokens = tokens
        self.kws = KiteTicker(api_key, access_token)

    def start(self):
        self.kws.on_ticks = self.on_ticks
        self.kws.on_connect = self.on_connect
        self.kws.connect(threaded=True)

    def on_connect(self, ws, response):
        print("WebSocket Connected")
        ws.subscribe(self.tokens)
        ws.set_mode(ws.MODE_FULL, self.tokens)

    def on_ticks(self, ws, ticks):

        for tick in ticks:
            symbol = tick.get("tradingsymbol")
            price = tick.get("last_price")
            oi = tick.get("oi")

            if not symbol or not price or oi is None:
                continue

            # ✅ ONLY VELOCITY FUTURE ALERT
            detect_velocity_futures(symbol, price, oi)
