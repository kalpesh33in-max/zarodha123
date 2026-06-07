import threading
import time

import pandas as pd
from kiteconnect import KiteTicker

from env_config import API_KEY
from kite_rate_limiter import kite_quote

INDEX_SYMBOL = "NSE:NIFTY BANK"


_cache_lock = threading.Lock()
_token_quotes = {}
_symbol_quotes = {}
_meta = {
    "connected": False,
    "last_tick_time": 0.0,
}
_active_engine = None
_active_engine_lock = threading.Lock()


def mark_connected(is_connected):
    with _cache_lock:
        _meta["connected"] = bool(is_connected)
        if is_connected:
            _meta["last_tick_time"] = time.time()


def update_token_quote(token, quote):
    if token is None or not isinstance(quote, dict):
        return

    now = time.time()
    payload = dict(quote)
    payload["ts"] = now

    with _cache_lock:
        _token_quotes[str(token)] = payload
        _meta["last_tick_time"] = now


def update_symbol_quote(symbol, quote):
    if not symbol or not isinstance(quote, dict):
        return

    now = time.time()
    payload = dict(quote)
    payload["ts"] = now

    with _cache_lock:
        _symbol_quotes[symbol] = payload
        _meta["last_tick_time"] = now


def get_token_quotes(tokens, max_age_seconds=15):
    now = time.time()
    fresh = {}
    wanted = {str(token) for token in tokens}

    with _cache_lock:
        for token in wanted:
            data = _token_quotes.get(token)
            if data and now - data.get("ts", 0) <= max_age_seconds:
                fresh[token] = dict(data)
    return fresh


def get_symbol_quotes(symbols, max_age_seconds=15):
    now = time.time()
    fresh = {}

    with _cache_lock:
        for symbol in symbols:
            data = _symbol_quotes.get(symbol)
            if data and now - data.get("ts", 0) <= max_age_seconds:
                fresh[symbol] = dict(data)
    return fresh


def get_ws_status():
    with _cache_lock:
        return dict(_meta)


def restart_active_flow_engine(reason="manual"):
    print(
        "WebSocket restart skipped: KiteTicker uses Twisted reactor, "
        f"which cannot be restarted inside the same process ({reason})."
    )
    return False


class FlowEngine:
    def __init__(self, kite, access_token=None, tokens=None):
        self.kite = kite
        self.kws = None
        self._started = False
        self._lock = threading.Lock()
        self._tokens = list(tokens or [])
        self._static_tokens = tokens is not None
        self._base_tokens = set()
        self._option_tokens = set()
        self._symbol_by_token = {}
        self._access_token_override = access_token
        self._auth_failed = False
        self._refresh_thread = None
        self._refresh_seconds = 60
        self._index_rows = None
        self._equity_rows = None

    def start(self):
        with self._lock:
            if self._started:
                print("WebSocket collector already running. Skipping duplicate start.")
                return True

            access_token = self._access_token_override or getattr(self.kite, "access_token", None)
            if not access_token:
                print("WebSocket collector not started: access token missing.")
                return False

            tokens, symbol_by_token, base_tokens, option_tokens = self._build_subscription_map()
            if not tokens:
                print("WebSocket collector not started: no tokens selected.")
                return False

            if symbol_by_token:
                self._symbol_by_token = symbol_by_token
            if tokens:
                self._tokens = tokens
            self._base_tokens = set(base_tokens)
            self._option_tokens = set(option_tokens)
            self._auth_failed = False
            self.kws = KiteTicker(API_KEY, access_token)
            self.kws.on_connect = self.on_connect
            self.kws.on_ticks = self.on_ticks
            self.kws.on_close = self.on_close
            self.kws.on_error = self.on_error
            self.kws.on_reconnect = self.on_reconnect
            self.kws.on_noreconnect = self.on_noreconnect
            self.kws.connect(threaded=True)
            self._started = True
            self._register_active_engine()
            self._start_refresh_thread()
            print(f"WebSocket collector started with {len(tokens)} subscriptions.")
            return True

    def restart(self, reason="manual"):
        with self._lock:
            print(f"Restarting WebSocket collector: {reason}")
            old_ws = self.kws
            self.kws = None
            self._started = False
            self._auth_failed = False
            mark_connected(False)

        self._close_socket(old_ws)
        return self.start()

    def _register_active_engine(self):
        global _active_engine
        with _active_engine_lock:
            _active_engine = self

    def _close_socket(self, ws):
        if not ws:
            return

        for method_name in ("stop", "close"):
            method = getattr(ws, method_name, None)
            if not method:
                continue
            try:
                method()
                return
            except Exception as e:
                print(f"WebSocket {method_name} during restart failed: {e}")

    def _build_subscription_map(self):
        from heatmap_engine import (
            _get_active_stock_future_contracts,
            get_burst_futures,
            get_burst_option_strike_range,
            get_burst_subscription_names,
            get_relevant_options,
            load_futures_data,
            load_options_data,
        )

        if self._static_tokens and self._tokens:
            return self._tokens, self._symbol_by_token, set(self._tokens), set()

        symbol_by_token = {}
        tokens = set()
        base_tokens = set()
        option_tokens = set()

        futures = load_futures_data()
        options = load_options_data()
        if futures is None or futures.empty or options is None or options.empty:
            return [], {}, set(), set()

        burst_names = get_burst_subscription_names()
        fut_symbols = get_burst_futures(self.kite, burst_names)
        include_nse_market_tokens = any(name in {"BANKNIFTY", "NIFTY"} for name in burst_names)

        # Exact prefix match to avoid substring collisions (e.g. "NIFTY" vs "BANKNIFTY").
        fut_by_name = {}
        for sym in fut_symbols:
            parts = sym.split(":", 1)
            if len(parts) != 2:
                continue
            tsym = parts[1]
            for name in burst_names:
                if tsym.startswith(name):
                    fut_by_name[name] = sym

        symbol_quotes = get_symbol_quotes(fut_symbols, max_age_seconds=60)
        missing_fut_symbols = [symbol for symbol in fut_symbols if symbol not in symbol_quotes]
        try:
            if missing_fut_symbols:
                symbol_quotes.update(kite_quote(self.kite, missing_fut_symbols))
        except Exception as e:
            print(f"WebSocket bootstrap quote failed: {e}")

        for symbol in fut_symbols:
            tradingsymbol = symbol.split(":", 1)[1]
            rows = futures[futures["tradingsymbol"] == tradingsymbol]
            if rows.empty:
                continue
            token = int(rows.iloc[0]["instrument_token"])
            tokens.add(token)
            base_tokens.add(token)
            symbol_by_token[token] = symbol

        if include_nse_market_tokens:
            index_rows = self._load_index_rows()
            if index_rows is not None and not index_rows.empty:
                row = index_rows.iloc[0]
                index_token = int(row["instrument_token"])
                tokens.add(index_token)
                base_tokens.add(index_token)
                symbol_by_token[index_token] = INDEX_SYMBOL

            equity_rows = self._load_equity_rows()
            equity_token_by_symbol = {}
            if equity_rows is not None and not equity_rows.empty:
                for _, row in equity_rows.iterrows():
                    equity_token_by_symbol[str(row["tradingsymbol"])] = int(row["instrument_token"])

            for contract in _get_active_stock_future_contracts():
                token = int(contract["token"])
                tokens.add(token)
                base_tokens.add(token)
                symbol_by_token[token] = contract["symbol"]

                next_token = contract.get("next_token")
                next_symbol = contract.get("next_symbol")
                if next_token and next_symbol:
                    next_token = int(next_token)
                    tokens.add(next_token)
                    base_tokens.add(next_token)
                    symbol_by_token[next_token] = next_symbol

                spot_token = equity_token_by_symbol.get(contract["name"])
                if spot_token:
                    tokens.add(spot_token)
                    base_tokens.add(spot_token)
                    symbol_by_token[spot_token] = f"NSE:{contract['name']}"

        for name in burst_names:
            base_symbol = fut_by_name.get(name, "")
            u_ltp = symbol_quotes.get(base_symbol, {}).get("last_price", 0)
            if u_ltp <= 0:
                continue

            df = get_relevant_options(name, u_ltp, strike_range=get_burst_option_strike_range(name))
            if df.empty:
                continue

            for token in df["instrument_token"].tolist():
                token = int(token)
                tokens.add(token)
                option_tokens.add(token)

        return sorted(tokens), symbol_by_token, base_tokens, option_tokens

    def _load_index_rows(self):
        if self._index_rows is not None:
            return self._index_rows

        try:
            df = pd.read_csv("instruments.csv", low_memory=False)
        except Exception as e:
            print(f"Error loading index rows for websocket: {e}")
            return None

        rows = df[
            (df["segment"] == "INDICES")
            & (df["exchange"] == "NSE")
            & (df["tradingsymbol"] == "NIFTY BANK")
        ]
        self._index_rows = rows if not rows.empty else None
        return self._index_rows

    def _load_equity_rows(self):
        if self._equity_rows is not None:
            return self._equity_rows

        try:
            df = pd.read_csv("instruments.csv", low_memory=False)
        except Exception as e:
            print(f"Error loading equity rows for websocket: {e}")
            return None

        rows = df[
            (df["segment"] == "NSE")
            & (df["exchange"] == "NSE")
            & (df["instrument_type"] == "EQ")
        ]
        self._equity_rows = rows if not rows.empty else None
        return self._equity_rows

    def _start_refresh_thread(self):
        if self._static_tokens or (self._refresh_thread and self._refresh_thread.is_alive()):
            return

        self._refresh_thread = threading.Thread(
            target=self._refresh_subscription_loop,
            daemon=True,
        )
        self._refresh_thread.start()

    def _refresh_subscription_loop(self):
        while True:
            time.sleep(self._refresh_seconds)
            if not self._started or self._auth_failed:
                continue
            try:
                self.refresh_subscriptions()
            except Exception as e:
                print(f"WebSocket subscription refresh error: {e}")

    def refresh_subscriptions(self):
        tokens, symbol_by_token, base_tokens, option_tokens = self._build_subscription_map()
        if not tokens:
            print("WebSocket refresh skipped: no subscription tokens selected.")
            return

        new_tokens = set(tokens)
        old_tokens = set(self._tokens)

        add_tokens = sorted(new_tokens - old_tokens)
        remove_tokens = sorted(old_tokens - new_tokens)

        self._symbol_by_token = symbol_by_token
        self._tokens = sorted(new_tokens)
        self._base_tokens = set(base_tokens)
        self._option_tokens = set(option_tokens)

        if add_tokens and self.kws:
            self._subscribe_tokens(self.kws, add_tokens)
            print(f"WebSocket added {len(add_tokens)} refreshed subscriptions.")

        if remove_tokens and self.kws and hasattr(self.kws, "unsubscribe"):
            chunk_size = 3000
            for i in range(0, len(remove_tokens), chunk_size):
                self.kws.unsubscribe(remove_tokens[i:i + chunk_size])
            print(f"WebSocket removed {len(remove_tokens)} stale subscriptions.")

    def _subscribe_tokens(self, ws, tokens):
        chunk_size = 3000
        token_list = list(tokens)
        for i in range(0, len(token_list), chunk_size):
            chunk = token_list[i:i + chunk_size]
            ws.subscribe(chunk)
            ws.set_mode(ws.MODE_FULL, chunk)
            time.sleep(0.2)

    def on_connect(self, ws, response):
        mark_connected(True)
        self._auth_failed = False
        if not self._tokens:
            print("WebSocket connected, but no tokens are selected.")
            return

        print(f"WebSocket connected. Subscribing {len(self._tokens)} tokens.")
        self._subscribe_tokens(ws, self._tokens)

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
        if self._is_auth_failure(code, reason):
            self._auth_failed = True
            print("WebSocket auth failed. Check API key and access token. Stopping reconnect attempts.")
            self._stop_reconnect(ws)
        print(f"WebSocket closed: {code} {reason}")

    def on_error(self, ws, code, reason):
        if self._is_auth_failure(code, reason):
            self._auth_failed = True
            print("WebSocket auth failed during upgrade. Check API key and access token pairing.")
            self._stop_reconnect(ws)
        print(f"WebSocket error: {code} {reason}")

    def on_reconnect(self, ws, attempts_count):
        if self._auth_failed:
            self._stop_reconnect(ws)
            print("WebSocket reconnect blocked because the last failure was authentication-related.")
            return
        print(f"WebSocket reconnect attempt: {attempts_count}")

    def on_noreconnect(self, ws):
        mark_connected(False)
        print("WebSocket stopped reconnecting.")

    def _is_auth_failure(self, code, reason):
        reason_text = str(reason or "").lower()
        return code == 1006 and ("403" in reason_text or "forbidden" in reason_text)

    def _stop_reconnect(self, ws):
        try:
            if hasattr(ws, "stop_retry"):
                ws.stop_retry()
        except Exception as e:
            print(f"Failed to stop WebSocket retry loop cleanly: {e}")
