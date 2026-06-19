import itertools
import os
import queue
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from env_config import (
    TELE_CHAT_ID_BN, TELE_TOKEN_BN, MATRIX_ROOM_ID_BN,
    TELE_CHAT_ID_STOCKS, TELE_TOKEN_STOCKS, MATRIX_ROOM_ID_STOCKS
)
from heatmap_engine import (
    calculate_burst_alerts,
    calculate_first_30m_alerts,
    calculate_gap_alerts,
    calculate_other_historical_alerts,
    get_burst_monitor_status,
    get_burst_quote_status,
    is_burst_session_open,
)
from telegram_utils import send_telegram_message
from matrix_utils import send_matrix_message
from websocket_flow import get_ws_status


IST = ZoneInfo("Asia/Kolkata")
BURST_SCAN_INTERVAL_SECONDS = 1
GAP_BATCH_INTERVAL_SECONDS = 30
HISTORICAL_SCAN_INTERVAL_SECONDS = 30
WS_HEARTBEAT_INTERVAL_SECONDS = 30
WS_STALE_SECONDS = 60
WS_ALERT_COOLDOWN_SECONDS = 300
WS_STARTUP_GRACE_SECONDS = 30
BURST_FALLBACK_STATUS_COOLDOWN_SECONDS = 300
MCX_MONITOR_STATUS_COOLDOWN_SECONDS = 900
ERROR_ALERT_COOLDOWN_SECONDS = 300
SILENT_LOG_INTERVAL_SECONDS = 300


def _env_flag(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in ("true", "1", "yes", "on")


SEND_WS_HEARTBEAT_STATUS = _env_flag("SEND_WS_HEARTBEAT_STATUS", False)
SEND_REST_FALLBACK_STATUS = _env_flag("SEND_REST_FALLBACK_STATUS", False)
SEND_MCX_MONITOR_STATUS = _env_flag("SEND_MCX_MONITOR_STATUS", False)

PRIORITY_BURST = 1
PRIORITY_GAP = 2
PRIORITY_FIRST_30M = 3
PRIORITY_HISTORICAL = 4
PRIORITY_STATUS = 5


class AlertDispatcher:
    def __init__(self):
        self._queue = queue.PriorityQueue()
        self._counter = itertools.count()
        self._thread = None

    def start(self, stop_event):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._worker,
            args=(stop_event,),
            daemon=True,
        )
        self._thread.start()

    def send(self, priority, message, chat_id=None, token=None, room_id=None):
        self._queue.put((priority, next(self._counter), message, chat_id, token, room_id))

    def _worker(self, stop_event):
        while not stop_event.is_set():
            try:
                priority, _, message, chat_id, token, room_id = self._queue.get(timeout=1)
            except queue.Empty:
                continue

            # Send to Matrix (Priority)
            try:
                send_matrix_message(message, room_id=room_id)
            except Exception as e:
                print(f"Matrix send failed at priority {priority}: {e}")

            # Send to Telegram
            try:
                send_telegram_message(message, chat_id=chat_id, token=token)
            except Exception as e:
                print(f"Telegram send failed at priority {priority}: {e}")

            self._queue.task_done()


def _is_market_open(now):
    start_time = datetime.strptime("09:00", "%H:%M").time()
    end_time = datetime.strptime("15:30", "%H:%M").time()
    return now.weekday() <= 4 and start_time <= now.time() <= end_time


def _is_any_scanner_session(now):
    return _is_market_open(now) or is_burst_session_open(now)


def _wait(stop_event, seconds):
    return stop_event.wait(seconds)


def _send_error(dispatcher, label, error, state):
    now_ts = time.time()
    if now_ts - state.get("last_error_alert", 0) < ERROR_ALERT_COOLDOWN_SECONDS:
        return
    state["last_error_alert"] = now_ts
    dispatcher.send(PRIORITY_STATUS, f"{label} Error: {error}")


def _burst_loop(kite, dispatcher, stop_event):
    state = {}
    while not stop_event.is_set():
        now = datetime.now(IST)
        if is_burst_session_open(now):
            try:
                bn_alerts, stock_alerts = calculate_burst_alerts(kite)
                quote_status = get_burst_quote_status()
                monitor_status = get_burst_monitor_status()
                if (
                    quote_status.get("source") == "rest_fallback"
                    and time.time() - state.get("last_rest_fallback_alert", 0)
                    >= BURST_FALLBACK_STATUS_COOLDOWN_SECONDS
                ):
                    state["last_rest_fallback_alert"] = time.time()
                    message = (
                        "Burst scanner using REST fallback because WebSocket ticks are not fresh. "
                        f"{quote_status.get('detail', '')}"
                    )
                    if SEND_REST_FALLBACK_STATUS:
                        dispatcher.send(PRIORITY_STATUS, message)
                    else:
                        print(message)

                if (
                    monitor_status.get("session") == "mcx"
                    and not bn_alerts
                    and not stock_alerts
                    and time.time() - state.get("last_mcx_monitor_alert", 0)
                    >= MCX_MONITOR_STATUS_COOLDOWN_SECONDS
                ):
                    state["last_mcx_monitor_alert"] = time.time()
                    message = (
                        "MCX burst monitor active: "
                        f"{monitor_status.get('names', '')} | "
                        f"source={monitor_status.get('source', '')} | "
                        f"futures={monitor_status.get('future_quotes', 0)}/{monitor_status.get('future_symbols', 0)} "
                        f"oi={monitor_status.get('future_oi_quotes', 0)} | "
                        f"options={monitor_status.get('option_quotes', 0)}/{monitor_status.get('option_tokens', 0)} "
                        f"oi={monitor_status.get('option_oi_quotes', 0)} | "
                        f"max move fut/opt={monitor_status.get('max_future_tick_lots', 0)}/"
                        f"{monitor_status.get('max_option_tick_lots', 0)} lots | "
                        f"threshold={monitor_status.get('threshold', 0)} | "
                        f"reason={monitor_status.get('reason', '')}"
                    )
                    if SEND_MCX_MONITOR_STATUS:
                        dispatcher.send(PRIORITY_STATUS, message)
                    else:
                        print(message)

                for alert in bn_alerts:
                    print(f"DEBUG: Sending BN/Index alert to {TELE_CHAT_ID_BN}")
                    dispatcher.send(
                        PRIORITY_BURST,
                        alert,
                        chat_id=TELE_CHAT_ID_BN,
                        token=TELE_TOKEN_BN,
                        room_id=MATRIX_ROOM_ID_BN,
                    )
                for alert in stock_alerts:
                    print(f"DEBUG: Sending Stock/MCX alert to {TELE_CHAT_ID_BN}")
                    dispatcher.send(
                        PRIORITY_BURST,
                        alert,
                        chat_id=TELE_CHAT_ID_BN,
                        token=TELE_TOKEN_BN,
                        room_id=MATRIX_ROOM_ID_BN,
                    )
            except Exception as e:
                print(f"Error in burst scanner loop: {e}")
                _send_error(dispatcher, "Burst Scanner", e, state)

        if _wait(stop_event, BURST_SCAN_INTERVAL_SECONDS):
            break


def _gap_loop(kite, dispatcher, stop_event):
    state = {}
    batch_index = 0
    while not stop_event.is_set():
        now = datetime.now(IST)
        if _is_any_scanner_session(now):
            try:
                alerts = calculate_gap_alerts(
                    kite,
                    batch_index=batch_index,
                    max_quote_symbols=500,
                )
                batch_index += 1
                for alert in alerts:
                    dispatcher.send(PRIORITY_GAP, alert)
            except Exception as e:
                print(f"Error in gap scanner loop: {e}")
                _send_error(dispatcher, "Gap Scanner", e, state)

        if _wait(stop_event, GAP_BATCH_INTERVAL_SECONDS):
            break


def _historical_loop(kite, dispatcher, stop_event):
    state = {}
    while not stop_event.is_set():
        now = datetime.now(IST)
        if _is_market_open(now):
            try:
                # 2. First 30m Alerts
                first_30m_alerts = calculate_first_30m_alerts(kite)
                for alert in first_30m_alerts:
                    dispatcher.send(PRIORITY_FIRST_30M, alert)

                alerts = calculate_other_historical_alerts(kite)
                for alert in alerts:
                    dispatcher.send(PRIORITY_HISTORICAL, alert)
            except Exception as e:
                print(f"Error in historical scanner loop: {e}")
                _send_error(dispatcher, "Historical Scanner", e, state)

        if _wait(stop_event, HISTORICAL_SCAN_INTERVAL_SECONDS):
            break


def _websocket_heartbeat_loop(dispatcher, stop_event):
    started_at = time.time()
    last_alert_time = 0.0
    while not stop_event.is_set():
        now = datetime.now(IST)
        if is_burst_session_open(now):
            status = get_ws_status()
            connected = status.get("connected", False)
            last_tick_time = status.get("last_tick_time", 0.0)
            waiting_for_first_tick = (
                not connected
                and not last_tick_time
                and time.time() - started_at < WS_STARTUP_GRACE_SECONDS
            )
            if waiting_for_first_tick:
                if _wait(stop_event, WS_HEARTBEAT_INTERVAL_SECONDS):
                    break
                continue

            age = time.time() - last_tick_time if last_tick_time else None
            stale = age is not None and age > WS_STALE_SECONDS
            problem = not connected or stale
            if stale:
                message = f"WebSocket stale: no tick for {age:.0f}s"
            else:
                message = "WebSocket disconnected"

            if problem and time.time() - last_alert_time >= WS_ALERT_COOLDOWN_SECONDS:
                last_alert_time = time.time()
                message = (
                    f"{message}. Burst REST fallback remains active; "
                    "process restart is required to recreate KiteTicker."
                )
                if SEND_WS_HEARTBEAT_STATUS:
                    dispatcher.send(PRIORITY_STATUS, message)
                else:
                    print(message)

        if _wait(stop_event, WS_HEARTBEAT_INTERVAL_SECONDS):
            break


def run_scanner(kite, stop_event=None):
    if stop_event is None:
        stop_event = threading.Event()

    print("Scanner session initialized. Starting priority scanner loops...")
    dispatcher = AlertDispatcher()
    dispatcher.start(stop_event)
    
    start_msg = "✅ *Kite Scanner Login Successful!* Priority scanner started. Burst alerts are highest priority. NSE burst: 09:00-15:29, MCX burst: 15:30-23:30. Burst REST fallback is enabled."
    dispatcher.send(PRIORITY_STATUS, start_msg)

    threads = [
        threading.Thread(target=_burst_loop, args=(kite, dispatcher, stop_event), daemon=True),
        threading.Thread(target=_gap_loop, args=(kite, dispatcher, stop_event), daemon=True),
        threading.Thread(target=_historical_loop, args=(kite, dispatcher, stop_event), daemon=True),
        threading.Thread(target=_websocket_heartbeat_loop, args=(dispatcher, stop_event), daemon=True),
    ]
    for thread in threads:
        thread.start()

    last_silent_log_time = 0.0
    try:
        while not stop_event.is_set():
            now = datetime.now(IST)
            if not _is_any_scanner_session(now):
                now_ts = now.timestamp()
                if now_ts - last_silent_log_time >= SILENT_LOG_INTERVAL_SECONDS:
                    print(f"[{now.strftime('%H:%M:%S')}] Outside trading session. Priority scanner is silent.")
                    last_silent_log_time = now_ts
            if _wait(stop_event, 5):
                break
    finally:
        print("Scanner loop stopped.")
        msg = "🛑 *Market Scanner Process Ended.*"
        send_telegram_message(msg)
        send_matrix_message(msg)
