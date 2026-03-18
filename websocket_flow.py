from kiteconnect import KiteTicker

class FlowEngine:

    def __init__(self,api_key,access_token,tokens):

        self.tokens = tokens
        self.kws = KiteTicker(api_key,access_token)

    def start(self):

        self.kws.on_ticks = self.on_ticks
        self.kws.on_connect = self.on_connect

        self.kws.connect(threaded=True)

    def on_connect(self,ws,response):

        ws.subscribe(self.tokens)
        ws.set_mode(ws.MODE_FULL,self.tokens)

    def on_ticks(self,ws,ticks):

        for tick in ticks:

            price = tick["last_price"]
            oi = tick.get("oi",0)

            print("Token:",tick["instrument_token"],"Price:",price,"OI:",oi)
