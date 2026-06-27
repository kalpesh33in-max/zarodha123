import os
import threading
import time


KITE_REST_MIN_INTERVAL_SECONDS = float(
    os.getenv("KITE_REST_MIN_INTERVAL_SECONDS", "1.05")
)
KITE_QUOTE_MIN_INTERVAL_SECONDS = float(
    os.getenv("KITE_QUOTE_MIN_INTERVAL_SECONDS", "1.05")
)
KITE_HISTORICAL_MIN_INTERVAL_SECONDS = float(
    os.getenv("KITE_HISTORICAL_MIN_INTERVAL_SECONDS", "1.05")
)

_rate_lock = threading.Lock()
_last_call = {
    "global": 0.0,
    "quote": 0.0,
    "historical": 0.0,
}


def throttle_kite_rest(endpoint, min_interval_seconds):
    endpoint_min = max(0.0, float(min_interval_seconds or 0.0))
    global_min = max(0.0, KITE_REST_MIN_INTERVAL_SECONDS)
    if endpoint_min <= 0 and global_min <= 0:
        return

    with _rate_lock:
        while True:
            now = time.time()
            wait_seconds = max(
                (_last_call.get("global", 0.0) + global_min) - now,
                (_last_call.get(endpoint, 0.0) + endpoint_min) - now,
                0.0,
            )
            if wait_seconds <= 0:
                break
            time.sleep(wait_seconds)

        ts = time.time()
        _last_call["global"] = ts
        _last_call[endpoint] = ts


def kite_quote(kite, symbols):
    throttle_kite_rest("quote", KITE_QUOTE_MIN_INTERVAL_SECONDS)
    return kite.quote(symbols)


def kite_historical_data(kite, token, from_time, to_time, interval):
    throttle_kite_rest("historical", KITE_HISTORICAL_MIN_INTERVAL_SECONDS)
    return kite.historical_data(token, from_time, to_time, interval)
