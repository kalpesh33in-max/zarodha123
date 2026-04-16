import threading
import time

import pandas as pd
from kiteconnect import KiteTicker

from env_config import API_KEY
from heatmap_engine import INDEX_SYMBOL, get_bank_futures, get_relevant_options, load_futures_data, load_options_data
from live_cache import mark_connected, update_symbol_quote, update_token_quote


class FlowEngine:
    def __init__(self, kite, access_token=None, tokens=None):
        self.kite = kite
        self.kws = None
        self._started = False
        self._lock = threading.Lock()
        self._tokens = list(tokens or [])
        self._symbol_by_token = {}
        self._access_token_override = access_token

    def start(self):
        with self._lock:
            if self._started:
                print("WebSocket collector already running. Skipping duplicate start.")
                return True

            access_token = self._access_token_override or getattr(self.kite, "access_token", None)
            if not access_token:
                print("WebSocket collector not started: access token missing.")
                return False

            tokens, symbol_by_token = self._build_subscription_map()
            if not tokens:
                print("WebSocket collector not started: no tokens selected.")
                return False

            if symbol_by_token:
                self._symbol_by_token = symbol_by_token
            if tokens:
                self._tokens = tokens
            self.kws = KiteTicker(API_KEY, access_token)
            self.kws.on_connect = self.on_connect
            self.kws.on_ticks = self.on_ticks
            self.kws.on_close = self.on_close
            self.kws.on_error = self.on_error
            self.kws.on_reconnect = self.on_reconnect
            self.kws.on_noreconnect = self.on_noreconnect
            self.kws.connect(threaded=True)
            self._started = True
            print(f"WebSocket collector started with {len(tokens)} subscriptions.")
            return True

    def _build_subscription_map(self):
        if self._tokens:
            return self._tokens, self._symbol_by_token

        symbol_by_token = {}
        tokens = set()

        futures = load_futures_data()
        options = load_options_data()
        if futures is None or futures.empty or options is None or options.empty:
            return [], {}

        fut_symbols = get_bank_futures(self.kite)
        if not fut_symbols:
            return [], {}

        symbol_quotes = {}
        try:
            symbol_quotes = self.kite.quote(fut_symbols + [INDEX_SYMBOL])
        except Exception as e:
            print(f"WebSocket bootstrap quote failed: {e}")

        for symbol in fut_symbols:
            tradingsymbol = symbol.split(":", 1)[1]
            rows = futures[futures["tradingsymbol"] == tradingsymbol]
            if rows.empty:
                continue
            token = int(rows.iloc[0]["instrument_token"])
            tokens.add(token)
            symbol_by_token[token] = symbol

        index_rows = self._load_index_rows()
        if index_rows is not None and not index_rows.empty:
            row = index_rows.iloc[0]
            index_token = int(row["instrument_token"])
            tokens.add(index_token)
            symbol_by_token[index_token] = INDEX_SYMBOL

        report_names = ["BANKNIFTY", "HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK"]
        for name in report_names:
            base_symbol = INDEX_SYMBOL if name == "BANKNIFTY" else next((s for s in fut_symbols if name in s), "")
            u_ltp = symbol_quotes.get(base_symbol, {}).get("last_price", 0)
            if u_ltp <= 0:
                continue

            df = get_relevant_options(name, u_ltp)
            if df.empty:
                continue

            for token in df["instrument_token"].tolist():
                tokens.add(int(token))

        return sorted(tokens), symbol_by_token

    def _load_index_rows(self):
        try:
            df = pd.read_csv("instruments.csv")
        except Exception as e:
            print(f"Error loading index rows for websocket: {e}")
            return None

        rows = df[
            (df["segment"] == "INDICES")
            & (df["exchange"] == "NSE")
            & (df["tradingsymbol"] == "NIFTY BANK")
        ]
        return rows if not rows.empty else None

    def on_connect(self, ws, response):
        mark_connected(True)
        if not self._tokens:
            return

        chunk_size = 3000
        for i in range(0, len(self._tokens), chunk_size):
            chunk = self._tokens[i:i + chunk_size]
            ws.subscribe(chunk)
            ws.set_mode(ws.MODE_FULL, chunk)
            time.sleep(0.2)

    def on_ticks(self, ws, ticks):
        now = time.time()
        for tick in ticks:
            token = str(tick.get("instrument_token"))
            quote = {
                "last_price": tick.get("last_price", 0),
                "oi": tick.get("oi", 0),
                "ohlc": tick.get("ohlc", {}),
                "timestamp": tick.get("exchange_timestamp") or tick.get("last_trade_time") or now,
            }
            update_token_quote(token, quote)

            symbol = self._symbol_by_token.get(int(token)) if token.isdigit() else None
            if symbol:
                update_symbol_quote(symbol, quote)

    def on_close(self, ws, code, reason):
        mark_connected(False)
        print(f"WebSocket closed: {code} {reason}")

    def on_error(self, ws, code, reason):
        print(f"WebSocket error: {code} {reason}")

    def on_reconnect(self, ws, attempts_count):
        print(f"WebSocket reconnect attempt: {attempts_count}")

    def on_noreconnect(self, ws):
        mark_connected(False)
        print("WebSocket stopped reconnecting.")
