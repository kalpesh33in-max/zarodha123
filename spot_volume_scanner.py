import os
import time
import threading
from datetime import datetime
import pandas as pd
from zoneinfo import ZoneInfo
from kiteconnect import KiteTicker

# Set up local timezone
IST = ZoneInfo("Asia/Kolkata")

def load_access_token():
    token = os.getenv("KITE_ACCESS_TOKEN", "").strip()
    if not token and os.path.exists("access_token.txt"):
        with open("access_token.txt", "r") as f:
            token = f.read().strip()
    return token

def load_instruments():
    if not os.path.exists("instruments.csv"):
        print("instruments.csv not found!")
        return pd.DataFrame()
    return pd.read_csv("instruments.csv")

def start_spot_volume_scanner():
    print("Starting Spot Volume Scanner (3rd WebSocket)...")
    
    # Need API keys from env_config
    import env_config
    from heatmap_engine import STOCK_BURST_NAMES, INDEX_BURST_NAMES
    
    token = load_access_token()
    if not token:
        print("No access token found! Spot Volume Scanner cannot start.")
        return
        
    df = load_instruments()
    if df.empty:
        return

    # Prepare targets
    target_tokens = []
    token_metadata = {}
    
    # 1. Stocks: We want the SPOT instrument, but the FUTURE lot size
    for name in STOCK_BURST_NAMES:
        # Get Future to find lot size
        futs = df[(df["name"] == name) & (df["instrument_type"] == "FUT")]
        if futs.empty: continue
        lot_size = int(futs.iloc[0].get("lot_size", 1))
        
        # Get Spot instrument for price/volume
        spots = df[(df["name"] == name) & (df["segment"] == "NSE")]
        if not spots.empty:
            spot = spots.iloc[0]
            tkn = int(spot["instrument_token"])
            target_tokens.append(tkn)
            token_metadata[tkn] = {
                "name": name,
                "symbol": spot["tradingsymbol"],
                "lot_size": lot_size,
                "type": "SPOT"
            }
            
    # 2. Indices (BankNifty): We want the FUTURE instrument and FUTURE lot size
    for name in INDEX_BURST_NAMES:
        futs = df[(df["name"] == name) & (df["instrument_type"] == "FUT")]
        if not futs.empty:
            futs = futs.sort_values(by="expiry")
            fut = futs.iloc[0]
            lot_size = int(fut.get("lot_size", 1))
            tkn = int(fut["instrument_token"])
            target_tokens.append(tkn)
            token_metadata[tkn] = {
                "name": name,
                "symbol": fut["tradingsymbol"],
                "lot_size": lot_size,
                "type": "FUTURE"
            }
            
    if not target_tokens:
        print("No targets found for Spot Volume Scanner.")
        return

    print(f"Spot Volume Scanner tracking {len(target_tokens)} instruments.")

    # State tracking
    state_lock = threading.Lock()
    candle_state = {}

    def reset_candle_state(tkn, current_vol):
        candle_state[tkn] = {
            "start_vol": current_vol,
            "high": -1.0,
            "low": float('inf'),
            "close": -1.0
        }

    kws = KiteTicker(env_config.API_KEY, token)

    def on_ticks(ws, ticks):
        with state_lock:
            for tick in ticks:
                tkn = tick["instrument_token"]
                ltp = tick["last_price"]
                
                # Fetch volume from MODE_QUOTE or MODE_FULL safely
                vol = tick.get("volume_traded") or tick.get("volume", 0)
                
                if tkn not in candle_state:
                    reset_candle_state(tkn, vol)
                
                c_state = candle_state[tkn]
                c_state["close"] = ltp
                if c_state["high"] == -1.0 or ltp > c_state["high"]:
                    c_state["high"] = ltp
                if c_state["low"] == float('inf') or ltp < c_state["low"]:
                    c_state["low"] = ltp
                c_state["current_vol"] = vol

    def on_connect(ws, response):
        print("Spot Volume Scanner connected to WebSocket. Subscribing...")
        ws.subscribe(target_tokens)
        ws.set_mode(ws.MODE_QUOTE, target_tokens)

    def on_close(ws, code, reason):
        print(f"Spot Volume WebSocket closed: {code} - {reason}")

    kws.on_ticks = on_ticks
    kws.on_connect = on_connect
    kws.on_close = on_close

    # Start WS in a background thread
    def run_ws():
        while True:
            try:
                kws.connect()
                time.sleep(5)  # Prevent infinite loop log spam if connection drops instantly
            except Exception as e:
                print(f"Spot Volume WS error: {e}")
                time.sleep(5)
                
    threading.Thread(target=run_ws, daemon=True).start()

    # Reporting Loop
    def reporting_loop():
        from telegram_utils import TelegramDispatcher
        dispatcher = TelegramDispatcher()
        
        msg = f"🟢 Spot Volume Scanner (3rd WebSocket) Started Successfully!\nTracking {len(target_tokens)} Spot/Future instruments."
        
        for chat, token in [
            (env_config.TELE_CHAT_ID, env_config.TELE_TOKEN),
            (env_config.TELE_CHAT_ID_BN, env_config.TELE_TOKEN_BN),
            (env_config.TELE_CHAT_ID_STOCKS, env_config.TELE_TOKEN_STOCKS)
        ]:
            try:
                dispatcher.send_sync(0, msg, chat_id=chat, token=token)
            except Exception as e:
                print(f"Error sending startup message to {chat}: {e}")
        
        last_reported_minute = None
        
        while True:
            time.sleep(0.5)
            now = datetime.now(IST)
            
            # Silent mode on weekends and holidays
            if now.weekday() > 4 or now.date().isoformat() in env_config.NSE_HOLIDAYS:
                time.sleep(60)
                continue
                
            current_minute = now.strftime("%Y-%m-%d %H:%M")
            
            # Fire at the 02-second mark of each new minute
            if now.second >= 2 and current_minute != last_reported_minute:
                last_reported_minute = current_minute
                
                alerts = []
                
                with state_lock:
                    for tkn, meta in token_metadata.items():
                        if tkn not in candle_state:
                            continue
                            
                        c_state = candle_state[tkn]
                        
                        # Only process if we actually received prices
                        if c_state["high"] == -1.0 or c_state["low"] == float('inf'):
                            # Reset for next minute anyway using current total volume
                            reset_candle_state(tkn, c_state.get("current_vol", 0))
                            continue
                            
                        # Calculate 1-minute delta volume
                        current_vol = c_state.get("current_vol", 0)
                        start_vol = c_state.get("start_vol", 0)
                        minute_vol = max(0, current_vol - start_vol)
                        
                        # Calculate Lots
                        lots = int(minute_vol / meta["lot_size"])
                        
                        if lots >= 500:
                            # User requested calculations
                            c_high = c_state["high"]
                            c_low = c_state["low"]
                            c_close = c_state["close"]
                            c_mid = (c_high - c_low) / 2.0
                            buy_price = c_low + c_mid
                            
                            # Format Telegram message
                            msg = (
                                f"Symbol: {meta['symbol']}\n"
                                f"LOTS: {lots}\n"
                                f"spot PRICE : {c_close:.2f}\n"
                                f"Candle high: {c_high:.2f}\n"
                                f"Candle low: {c_low:.2f}\n"
                                f"Candle mid: {c_mid:.2f}\n"
                                f"Buying price: {buy_price:.2f}\n"
                                f"TIME: {now.strftime('%H:%M:%S')}"
                            )
                            alerts.append(msg)
                            
                        # Reset for next minute
                        reset_candle_state(tkn, current_vol)
                        
                # Dispatch alerts
                token_stocks = os.getenv("TELE_TOKEN_STOCKS", env_config.TELE_TOKEN)
                chat_stocks = os.getenv("CHAT_ID_STOCKS", env_config.TELE_CHAT_ID)
                
                for alert in alerts:
                    try:
                        dispatcher.send_sync(0, alert, chat_id=chat_stocks, token=token_stocks)
                    except Exception as e:
                        print(f"Error sending spot volume alert: {e}")

    threading.Thread(target=reporting_loop, daemon=True).start()

if __name__ == "__main__":
    start_spot_volume_scanner()
    while True:
        time.sleep(1)
