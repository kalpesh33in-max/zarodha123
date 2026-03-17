from kiteconnect import KiteTicker
from heatmap_engine import detect_velocity_futures

class FlowEngine:
    def __init__(self, api_key, access_token, tokens):
        self.tokens = tokens
        self.kws = KiteTicker(api_key, access_token)

    def start(self):
        # Correct callback assignment
        self.kws.on_ticks = self.on_ticks
        self.kws.on_connect = self.on_connect
        self.kws.connect(threaded=True)

    def on_connect(self, ws, response):
        ws.subscribe(self.tokens)
        ws.set_mode(ws.MODE_FULL, self.tokens)

    def on_ticks(self, ws, ticks):
        for tick in ticks:
            # Note: Webhooks usually send instrument_token; 
            # you may need to map token to symbol for detect_velocity_futures
            price = tick.get("last_price")
            oi = tick.get("oi")
            token = tick.get("instrument_token")
            
            if price and oi:
                detect_velocity_futures(token, price, oi)
