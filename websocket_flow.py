class FlowEngine:
    def __init__(self, api_key, access_token, tokens):
        self.tokens = tokens
        self.kws = KiteTicker(api_key, access_token)

    def start(self):
        # Assign callbacks using the correct attribute names
        self.kws.on_ticks = self.on_ticks
        self.kws.on_connect = self.on_connect
        # Ensure you handle close/error as well to avoid crashes
        self.kws.on_close = self.on_close 
        
        self.kws.connect(threaded=True)

    def on_connect(self, ws, response):
        print("WebSocket Connected")
        ws.subscribe(self.tokens)
        ws.set_mode(ws.MODE_FULL, self.tokens)

    def on_ticks(self, ws, ticks):
        for tick in ticks:
            # Note: Ticks usually contain 'instrument_token', 
            # you may need to map them back to symbols if needed.
            token = tick.get("instrument_token")
            price = tick.get("last_price")
            oi = tick.get("oi")
            
            if price and oi:
                detect_velocity_futures(token, price, oi)

    def on_close(self, ws, code, reason):
        print(f"WebSocket Closed: {code} - {reason}")
