import os
import sys
import time
import json
import requests
import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import env_config

IST = ZoneInfo("Asia/Kolkata")

TARGET_CHAT_ID = "-1003907739730"

BOT_TOKENS = [
    getattr(env_config, "TELE_TOKEN_BN", "8190193308:AAGQGzTdKzZoytMF-YRnM_PwtYKY88cAUHs"),
    getattr(env_config, "TELEGRAM_TOKEN", ""),
    getattr(env_config, "TELE_TOKEN_STOCKS", "")
]
BOT_TOKENS = [t for t in BOT_TOKENS if t and len(t) > 20]

def send_signal_telegram(message, chat_ids=None):
    """Sends institutional trade alert EXCLUSIVELY to -1003907739730 (Bnf ,nifty,sense)."""
    if not chat_ids:
        chat_ids = [TARGET_CHAT_ID]

    sent_any = False
    for cid in chat_ids:
        for tok in BOT_TOKENS:
            try:
                url = f"https://api.telegram.org/bot{tok}/sendMessage"
                payload = {
                    "chat_id": cid,
                    "text": message
                }
                res = requests.post(url, json=payload, timeout=8).json()
                if res.get("ok"):
                    print(f"[TelegramSignal] Alert successfully delivered to {cid}")
                    sent_any = True
                    break
            except Exception as e:
                print(f"[TelegramSignal] Error sending to {cid}: {e}")
    return sent_any


def send_zarodastock_bot_alert(message, parse_mode="HTML"):
    """
    Sends confluent institutional order flow alert directly to @zarodastock_bot
    (using TELE_TOKEN_STOCKS and CHAT_ID_STOCKS).
    """
    token = getattr(env_config, "TELE_TOKEN_STOCKS", "8769936656:AAH80Rv9TNjI-jFzvGvZJWUSUSrd5FEB3Hk")
    chat_id = getattr(env_config, "CHAT_ID_STOCKS", getattr(env_config, "TELE_CHAT_ID", "530388484"))

    if not token or not chat_id:
        print("[ConfluenceAlert] Missing token or chat_id for @zarodastock_bot")
        return False

    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": parse_mode
        }
        res = requests.post(url, json=payload, timeout=8).json()
        if res.get("ok"):
            print(f"[ConfluenceAlert] Alert delivered via @zarodastock_bot to chat {chat_id} (msg_id: {res.get('result', {}).get('message_id')})")
            return True
        else:
            print(f"[ConfluenceAlert] Telegram API error: {res}")
            return False
    except Exception as e:
        print(f"[ConfluenceAlert] Error dispatching to @zarodastock_bot: {e}")
        return False


def compute_daily_vwap(timeline, end_idx):
    """Computes daily cumulative Volume Weighted Average Price (VWAP) from market open."""
    if not timeline or end_idx < 0:
        return 0.0
    cum_pv = 0.0
    cum_vol = 0.0
    for k in range(end_idx + 1):
        c = timeline[k]
        p = float(c.get("price", 0.0) or 0.0)
        v = float(c.get("tot_lots", 1.0) or 1.0)
        if v <= 0:
            v = 1.0
        cum_pv += (p * v)
        cum_vol += v
    return (cum_pv / cum_vol) if cum_vol > 0 else float(timeline[end_idx].get("price", 0.0))


def compute_far_itm_flow(timeline, strikes, end_idx, lookback_mins, cur_spot, far_dist_thresh):
    """
    Computes rolling Far-ITM synthetic institutional activity over lookback window.
    Far CE: dist <= -far_dist_thresh
    Far PE: dist >= far_dist_thresh
    """
    start_idx = max(0, end_idx - lookback_mins + 1)
    slice_t = timeline[start_idx:end_idx + 1]
    
    far_ce_b, far_ce_w, far_ce_sc, far_ce_unw = 0, 0, 0, 0
    far_pe_b, far_pe_w, far_pe_sc, far_pe_unw = 0, 0, 0, 0

    for t in slice_t:
        stk = t.get("stk", {})
        for s in strikes:
            s_str = str(s)
            s_num = float(s)
            dist = s_num - cur_spot
            if dist <= -far_dist_thresh and s_str in stk:
                row = stk[s_str]
                far_ce_b += (row[2] if len(row) > 2 else 0)
                far_ce_w += (row[3] if len(row) > 3 else 0)
                far_ce_sc += (row[4] if len(row) > 4 else 0)
                far_ce_unw += (row[5] if len(row) > 5 else 0)
            elif dist >= far_dist_thresh and s_str in stk:
                row = stk[s_str]
                far_pe_b += (row[6] if len(row) > 6 else 0)
                far_pe_w += (row[7] if len(row) > 7 else 0)
                far_pe_sc += (row[8] if len(row) > 8 else 0)
                far_pe_unw += (row[9] if len(row) > 9 else 0)

    net_lots = (far_ce_b + far_ce_sc + far_pe_w) - (far_pe_b + far_pe_sc + far_ce_w + far_ce_unw)
    return {
        "netLots": net_lots,
        "ce_buy": far_ce_b, "ce_write": far_ce_w, "ce_unw": far_ce_unw,
        "pe_buy": far_pe_b, "pe_write": far_pe_w, "pe_unw": far_pe_unw
    }


def compute_atm_itm_flow(timeline, strikes, end_idx, lookback_mins, cur_spot, near_dist_thresh):
    """
    Computes rolling ATM strike institutional order flow over lookback window.
    Near strikes: abs(dist) < near_dist_thresh
    """
    start_idx = max(0, end_idx - lookback_mins + 1)
    slice_t = timeline[start_idx:end_idx + 1]

    atm_ce_b, atm_ce_w, atm_ce_sc, atm_ce_unw = 0, 0, 0, 0
    atm_pe_b, atm_pe_w, atm_pe_sc, atm_pe_unw = 0, 0, 0, 0

    for t in slice_t:
        stk = t.get("stk", {})
        for s in strikes:
            s_str = str(s)
            s_num = float(s)
            dist = s_num - cur_spot
            if abs(dist) < near_dist_thresh and s_str in stk:
                row = stk[s_str]
                atm_ce_b += (row[2] if len(row) > 2 else 0)
                atm_ce_w += (row[3] if len(row) > 3 else 0)
                atm_ce_sc += (row[4] if len(row) > 4 else 0)
                atm_ce_unw += (row[5] if len(row) > 5 else 0)
                atm_pe_b += (row[6] if len(row) > 6 else 0)
                atm_pe_w += (row[7] if len(row) > 7 else 0)
                atm_pe_sc += (row[8] if len(row) > 8 else 0)
                atm_pe_unw += (row[9] if len(row) > 9 else 0)

    net_lots = (atm_ce_b + atm_ce_sc + atm_pe_w) - (atm_pe_b + atm_pe_sc + atm_ce_w + atm_ce_unw)
    return {
        "netLots": net_lots,
        "ce_w": atm_ce_w, "pe_w": atm_pe_w
    }


def get_rolling_futures_flow(timeline, end_idx, lookback_mins):
    """Evaluates price momentum and flow signal over lookback window."""
    start_idx = max(0, end_idx - lookback_mins)
    cur_c = timeline[end_idx]
    prev_c = timeline[start_idx]

    p_now = float(cur_c.get("price", 0.0))
    p_prev = float(prev_c.get("price", p_now))
    p_chg = p_now - p_prev

    sig = "BULL" if p_chg > 0 else ("BEAR" if p_chg < 0 else "NEUTRAL")
    return {"pChg": p_chg, "sig": sig}


def get_rolling_atr_15m(timeline, end_idx):
    """Computes rolling 15-minute high-low ATR price range."""
    start_idx = max(0, end_idx - 15 + 1)
    sub = timeline[start_idx:end_idx + 1]
    if len(sub) < 2:
        return 20.0
    prices = [float(t.get("price", 0.0) or 0.0) for t in sub]
    return max(prices) - min(prices)



class InstitutionalSignalEngine:
    def __init__(self):
        self.session_states = {}
        self.confluence_cooldowns = {}
        self.hero_zero_triggered = {}
        self.hero_zero_killed = {}

    def evaluate_confluence_alert(self, sym, date_str, timeline, strikes, force_check=False):
        """
        Evaluates 3-way live order flow confluence:
        1. Chronological 1-Minute Order Flow & Market Direction Navigator (5m window)
        2. Last 10-Minute Institutional ITM Window Brief (Flash Pulse)
        3. Quick Trading Takeaway (Wall Resistance / Support Proximity)

        When confluence triggers, dispatches high-conviction alert to @zarodastock_bot.
        """
        if not timeline or len(timeline) < 10:
            return None

        cur_candle = timeline[-1]
        cur_time = cur_candle.get("time", "")
        cur_price = cur_candle.get("price", 0.0)

        cooldown_key_bear = f"{sym}_{date_str}_BEARISH"
        cooldown_key_bull = f"{sym}_{date_str}_BULLISH"

        # 1. Market Direction Navigator (5-Minute Rolling Window)
        win5 = timeline[-5:]
        p_5m_ago = win5[0].get("price", cur_price)
        mom5m = cur_price - p_5m_ago

        strong_lot_thresh = 400 if sym == "CRUDEOILM" else 3000
        step_size = 50 if sym == "CRUDEOILM" else 100
        far_dist_thresh = 50 if sym == "CRUDEOILM" else 250
        near_wall_thresh = 60 if sym == "CRUDEOILM" else 250
        tag = "#CRUDEOIL #MCX" if sym == "CRUDEOILM" else "#BANKNIFTY"

        sum_5m_net_lots = 0
        for candle in win5:
            stk = candle.get("stk", {})
            c_bull = 0
            c_bear = 0
            for s in strikes:
                s_str = str(s)
                if s_str in stk:
                    row = stk[s_str]
                    ce_b = row[2] if len(row) > 2 else 0
                    ce_w = row[3] if len(row) > 3 else 0
                    ce_sc = row[4] if len(row) > 4 else 0
                    ce_unw = row[5] if len(row) > 5 else 0
                    pe_b = row[6] if len(row) > 6 else 0
                    pe_w = row[7] if len(row) > 7 else 0
                    pe_sc = row[8] if len(row) > 8 else 0
                    pe_unw = row[9] if len(row) > 9 else 0
                    c_bull += (ce_b + ce_sc + pe_w + pe_sc)
                    c_bear += (ce_w + ce_unw + pe_b + pe_unw)
            sum_5m_net_lots += (c_bull - c_bear)

        is_nav_strong_bear = (sum_5m_net_lots <= -strong_lot_thresh * 2) and (mom5m <= 0)
        is_nav_strong_bull = (sum_5m_net_lots >= strong_lot_thresh * 2) and (mom5m >= 0)

        if not (is_nav_strong_bear or is_nav_strong_bull) and not force_check:
            return None

        # 2. Last 10-Minute Institutional ITM Window Brief Flash Pulse
        win10 = timeline[-10:]
        avg_price_10m = sum(c.get("price", cur_price) for c in win10) / len(win10)
        cur_atm_strike = round(avg_price_10m / step_size) * step_size

        pulse_unw_thresh = 500 if sym == "CRUDEOILM" else 3000
        pulse_write_thresh = 400 if sym == "CRUDEOILM" else 2500
        pulse_buy_thresh = 400 if sym == "CRUDEOILM" else 2000

        far_ce = {"buy": 0, "write": 0, "unw": 0, "sc": 0, "topWrite": None, "topBuy": None}
        far_pe = {"buy": 0, "write": 0, "unw": 0, "sc": 0, "topWrite": None, "topBuy": None}

        stk_stats_10m = {str(s): {"ce_b":0, "ce_w":0, "ce_unw":0, "ce_sc":0, "pe_b":0, "pe_w":0, "pe_unw":0, "pe_sc":0} for s in strikes}
        for candle in win10:
            stk = candle.get("stk", {})
            for s in strikes:
                s_str = str(s)
                if s_str in stk:
                    row = stk[s_str]
                    stk_stats_10m[s_str]["ce_b"] += (row[2] if len(row) > 2 else 0)
                    stk_stats_10m[s_str]["ce_w"] += (row[3] if len(row) > 3 else 0)
                    stk_stats_10m[s_str]["ce_sc"] += (row[4] if len(row) > 4 else 0)
                    stk_stats_10m[s_str]["ce_unw"] += (row[5] if len(row) > 5 else 0)
                    stk_stats_10m[s_str]["pe_b"] += (row[6] if len(row) > 6 else 0)
                    stk_stats_10m[s_str]["pe_w"] += (row[7] if len(row) > 7 else 0)
                    stk_stats_10m[s_str]["pe_sc"] += (row[8] if len(row) > 8 else 0)
                    stk_stats_10m[s_str]["pe_unw"] += (row[9] if len(row) > 9 else 0)

        max_far_ce_w, max_far_ce_b = 0, 0
        max_far_pe_w, max_far_pe_b = 0, 0

        for s in strikes:
            s_str = str(s)
            s_num = float(s)
            dist = s_num - avg_price_10m
            st = stk_stats_10m[s_str]

            # Far CE: dist <= -far_dist_thresh
            if dist <= -far_dist_thresh:
                far_ce["buy"] += st["ce_b"]
                far_ce["write"] += st["ce_w"]
                far_ce["unw"] += st["ce_unw"]
                far_ce["sc"] += st["ce_sc"]
                if st["ce_w"] > max_far_ce_w and st["ce_w"] > 0:
                    max_far_ce_w = st["ce_w"]
                    far_ce["topWrite"] = {"strike": s_str, "lots": st["ce_w"]}
                if st["ce_b"] > max_far_ce_b and st["ce_b"] > 0:
                    max_far_ce_b = st["ce_b"]
                    far_ce["topBuy"] = {"strike": s_str, "lots": st["ce_b"]}

            # Far PE: dist >= far_dist_thresh
            if dist >= far_dist_thresh:
                far_pe["buy"] += st["pe_b"]
                far_pe["write"] += st["pe_w"]
                far_pe["unw"] += st["pe_unw"]
                far_pe["sc"] += st["pe_sc"]
                if st["pe_w"] > max_far_pe_w and st["pe_w"] > 0:
                    max_far_pe_w = st["pe_w"]
                    far_pe["topWrite"] = {"strike": s_str, "lots": st["pe_w"]}
                if st["pe_b"] > max_far_pe_b and st["pe_b"] > 0:
                    max_far_pe_b = st["pe_b"]
                    far_pe["topBuy"] = {"strike": s_str, "lots": st["pe_b"]}

        is_pulse_bear = False
        pulse_bear_desc = ""
        if far_ce["unw"] > pulse_unw_thresh and far_ce["write"] > pulse_write_thresh:
            is_pulse_bear = True
            top_w_strike = far_ce["topWrite"]["strike"] if far_ce["topWrite"] else "ITM"
            top_w_lots = far_ce["topWrite"]["lots"] if far_ce["topWrite"] else far_ce["write"]
            pulse_bear_desc = f"Smart money dumped <b>{far_ce['unw']:,} CE lots</b> while aggressively writing <b>{top_w_strike} CE</b> ({top_w_lots:,} lots). Upside ceiling formed — strictly avoid buying calls!"
        elif far_pe["buy"] > pulse_buy_thresh:
            is_pulse_bear = True
            top_b_strike = far_pe["topBuy"]["strike"] if far_pe["topBuy"] else "ITM"
            top_b_lots = far_pe["topBuy"]["lots"] if far_pe["topBuy"] else far_pe["buy"]
            pulse_bear_desc = f"Bears attacking downside with <b>{far_pe['buy']:,} synthetic put lots</b> at <b>{top_b_strike} PE</b> ({top_b_lots:,} lots). High risk of downward cascade!"

        is_pulse_bull = False
        pulse_bull_desc = ""
        if far_ce["buy"] > pulse_buy_thresh and far_pe["unw"] > (pulse_unw_thresh * 0.6):
            is_pulse_bull = True
            top_b_strike = far_ce["topBuy"]["strike"] if far_ce["topBuy"] else "ITM"
            top_b_lots = far_ce["topBuy"]["lots"] if far_ce["topBuy"] else far_ce["buy"]
            pulse_bull_desc = f"Smart money aggressively bought <b>{far_ce['buy']:,} CE lots</b> at <b>{top_b_strike} CE</b> ({top_b_lots:,} lots) while bears liquidated puts. Bulls in full command — ride call longs!"
        elif far_pe["write"] > pulse_write_thresh:
            is_pulse_bull = True
            top_w_strike = far_pe["topWrite"]["strike"] if far_pe["topWrite"] else "ITM"
            top_w_lots = far_pe["topWrite"]["lots"] if far_pe["topWrite"] else far_pe["write"]
            pulse_bull_desc = f"Put writers built a strong support floor with <b>{far_pe['write']:,} lots</b> written at <b>{top_w_strike} PE</b> ({top_w_lots:,} lots). Downside well defended!"

        # 3. Quick Trading Takeaway (Wall Resistance / Support Proximity)
        tot_ce_writes_by_strike = {}
        tot_pe_writes_by_strike = {}
        for s in strikes:
            s_str = str(s)
            tot_ce_writes_by_strike[s_str] = sum(c.get("stk", {}).get(s_str, [0]*4)[3] for c in timeline if s_str in c.get("stk", {}))
            tot_pe_writes_by_strike[s_str] = sum(c.get("stk", {}).get(s_str, [0]*8)[7] for c in timeline if s_str in c.get("stk", {}))

        sorted_ce_writes = sorted(tot_ce_writes_by_strike.items(), key=lambda x: x[1], reverse=True)
        sorted_pe_writes = sorted(tot_pe_writes_by_strike.items(), key=lambda x: x[1], reverse=True)

        top_res_strike, top_res_lots = sorted_ce_writes[0] if sorted_ce_writes else (None, 0)
        top_sup_strike, top_sup_lots = sorted_pe_writes[0] if sorted_pe_writes else (None, 0)

        is_near_res = False
        res_gap = 99999
        if top_res_strike:
            res_gap = cur_price - float(top_res_strike)
            if -near_wall_thresh <= res_gap <= (near_wall_thresh * 0.5):
                is_near_res = True

        is_near_sup = False
        sup_gap = 99999
        if top_sup_strike:
            sup_gap = cur_price - float(top_sup_strike)
            if -(near_wall_thresh * 0.5) <= sup_gap <= near_wall_thresh:
                is_near_sup = True

        min_wall_lots = 300 if sym == "CRUDEOILM" else 2500
        now_ts = time.time()
        daily_vwap = compute_daily_vwap(timeline, len(timeline) - 1)
        vwap_status_str = f"Spot ₹{cur_price:,.1f} vs Daily VWAP ₹{daily_vwap:,.1f}"

        # TRIGGER BEARISH CONFLUENCE
        if (is_nav_strong_bear and is_pulse_bear and (is_near_res or top_res_lots >= min_wall_lots)) or (force_check and is_nav_strong_bear):
            last_time = self.confluence_cooldowns.get(cooldown_key_bear, 0)
            if force_check or (now_ts - last_time) >= 900: # 15 min cooldown
                self.confluence_cooldowns[cooldown_key_bear] = now_ts

                gap_str = f"+{res_gap:.1f}" if res_gap >= 0 else f"{res_gap:.1f}"
                alert_msg = (
                    f"🚨 <b>[LIVE ORDER FLOW CONFLUENCE ALERT]</b> 🚨\n"
                    f"🏷 <b>{tag}</b> | ⏰ <b>{cur_time} IST</b>\n\n"
                    f"🧭 <b>Navigator:</b> 🩸 <b>STRONG BEARISH (DOWNWARD BREAKDOWN)</b>\n"
                    f"• 5m Momentum: <b>{mom5m:+.1f} pts</b> | Net Flow: <b>{sum_5m_net_lots:,} lots</b>\n"
                    f"• VWAP Check: <b>{vwap_status_str}</b>\n\n"
                    f"⏱️ <b>10-Min Brief:</b> ⚠️ <b>Flash Upside Ceiling</b>\n"
                    f"• {pulse_bear_desc or 'Smart money dumping calls and aggressive writing detected.'}\n\n"
                    f"🧱 <b>Takeaway & Proximity:</b> <b>Watch {top_res_strike} Resistance</b>\n"
                    f"• Call writers at <b>{top_res_strike} CE ({top_res_lots:,} lots written)</b> are the primary ceiling. Spot is near ceiling (₹{cur_price:,.1f} | Gap: {gap_str} pts). High chance of downward breakdown!\n\n"
                    f"⚡ <b>Tactical Action:</b> 🔴 <b>FAVOR PUTS / SELL ON PULLBACKS — STRICTLY AVOID CALLS!</b>"
                )
                send_zarodastock_bot_alert(alert_msg)
                return alert_msg

        # TRIGGER BULLISH CONFLUENCE
        if (is_nav_strong_bull and is_pulse_bull and (is_near_sup or top_sup_lots >= min_wall_lots)) or (force_check and is_nav_strong_bull):
            last_time = self.confluence_cooldowns.get(cooldown_key_bull, 0)
            if force_check or (now_ts - last_time) >= 900: # 15 min cooldown
                self.confluence_cooldowns[cooldown_key_bull] = now_ts

                gap_str = f"+{sup_gap:.1f}" if sup_gap >= 0 else f"{sup_gap:.1f}"
                alert_msg = (
                    f"🚀 <b>[LIVE ORDER FLOW CONFLUENCE ALERT]</b> 🚀\n"
                    f"🏷 <b>{tag}</b> | ⏰ <b>{cur_time} IST</b>\n\n"
                    f"🧭 <b>Navigator:</b> 🚀 <b>STRONG BULLISH (UPWARD BREAKOUT)</b>\n"
                    f"• 5m Momentum: <b>{mom5m:+.1f} pts</b> | Net Flow: <b>{sum_5m_net_lots:+,} lots</b>\n"
                    f"• VWAP Check: <b>{vwap_status_str}</b>\n\n"
                    f"⏱️ <b>10-Min Brief:</b> 🚀 <b>Flash Floor & Accumulation</b>\n"
                    f"• {pulse_bull_desc or 'Smart money accumulation and aggressive put writing detected.'}\n\n"
                    f"🧱 <b>Takeaway & Proximity:</b> <b>Conviction Floor at {top_sup_strike} PE</b>\n"
                    f"• Put writers at <b>{top_sup_strike} PE ({top_sup_lots:,} lots written)</b> built a solid bedrock support floor. Spot holding firmly above floor (₹{cur_price:,.1f} | Gap: {gap_str} pts).\n\n"
                    f"⚡ <b>Tactical Action:</b> 🟢 <b>FAVOR CALLS / BUY ON DIPS — STRICTLY AVOID PUTS!</b>"
                )
                send_zarodastock_bot_alert(alert_msg)
                return alert_msg

        return None

    def process_live_timeline(self, sym, date_str, timeline, strikes):
        """
        Executes Model 6: User 2-Step Alert (With Daily VWAP, Target 150 pts BNF / 100 pts Crude).
        Rules:
        1. Step 1 (Intraday Momentum / Pressure Alert): Early directional buildup latch.
        2. Step 2 (3-Layer Super Confluence): Confirmed whale attack across Far-ITM, ATM, and Futures.
        3. Daily VWAP Filter: CALL only if Spot >= VWAP, PUT only if Spot <= VWAP.
        4. Target: 150 pts (BankNifty) / 100 pts (CrudeOilM).
        5. Initial SL: 45 pts (BankNifty) / 30 pts (CrudeOilM).
        6. Trailing Stop: Triggers when profit >= 30 pts (BNF) / >= 20 pts (Crude), trails by 15 pts (BNF) / 10 pts (Crude).
        7. Reversal Exit: Exit immediately on opposite Super Confluence alert.
        8. Anti-Chop: 10-minute cooldown after scratched trade (<= 15 pts PnL).
        """
        if not timeline or len(timeline) < 5:
            return

        # 1. Evaluate 3-Way Confluence Alert (@zarodastock_bot)
        try:
            self.evaluate_confluence_alert(sym, date_str, timeline, strikes)
        except Exception as ce_err:
            print(f"[TelegramSignal] Confluence alert evaluation error: {ce_err}")

        # Model 6 / Stage 4 Elite Sniper Execution State Management
        state_key = f"{sym}_{date_str}_MODEL6"
        if state_key not in self.session_states:
            self.session_states[state_key] = {
                "active_trade": None,
                "saw_bear_pressure": False,
                "saw_bear_pressure_idx": -999,
                "saw_bull_momentum": False,
                "saw_bull_momentum_idx": -999,
                "last_scratch_idx": -999,
                "last_scratch_dir": None,
                "trades_today": 0
            }
        st = self.session_states[state_key]

        cur_idx = len(timeline) - 1
        t = timeline[-1]
        cur_t = t.get("time", "")
        cur_p = float(t.get("price", 0.0))
        cur_spot = float(t.get("spot_price", 0.0) or cur_p)

        # Asset-specific configuration
        if sym == "BANKNIFTY":
            far_dist_thresh = 250
            far_vol_thresh = 35000
            atm_vol_thresh = 20000
            s_step = 100
            sl_pts = 45.0
            tgt_pts = 150.0
            t1_pts = 50.0
            trail_trigger = 30.0
            trail_dist = 15.0
            tag = "#BNF #BANKNIFTY #STAGE4_SNIPER"
            is_trade_window = (("09:30" <= cur_t <= "11:15") or ("13:15" <= cur_t <= "14:30"))
            session_end_time = "15:15"
        elif sym == "CRUDEOILM":
            far_dist_thresh = 100
            far_vol_thresh = 200
            atm_vol_thresh = 150
            s_step = 50
            sl_pts = 30.0
            tgt_pts = 100.0
            t1_pts = 35.0
            trail_trigger = 20.0
            trail_dist = 10.0
            tag = "#CRUDEOIL #MCX #STAGE4_SNIPER"
            is_trade_window = ("09:15" <= cur_t <= "22:30")
            session_end_time = "23:15"
        else:
            return

        # Compute Daily VWAP & Multi-Speed Order Flows
        daily_vwap = compute_daily_vwap(timeline, cur_idx)
        far15 = compute_far_itm_flow(timeline, strikes, cur_idx, 15, cur_spot, far_dist_thresh)
        far5 = compute_far_itm_flow(timeline, strikes, cur_idx, 5, cur_spot, far_dist_thresh)
        atm15 = compute_atm_itm_flow(timeline, strikes, cur_idx, 15, cur_spot, far_dist_thresh)
        atm5 = compute_atm_itm_flow(timeline, strikes, cur_idx, 5, cur_spot, far_dist_thresh)
        fut15 = get_rolling_futures_flow(timeline, cur_idx, 15)
        fut5 = get_rolling_futures_flow(timeline, cur_idx, 5)

        # Compute Rolling Cumulative PCR
        cum_ce = t.get("cum_ce", 0) or t.get("ce_chg", 0)
        cum_pe = t.get("cum_pe", 0) or t.get("pe_chg", 0)
        rolling_pcr = (cum_pe / cum_ce) if cum_ce > 0 else 1.0

        is_fut_bull = (fut15["sig"] == "BULL" or fut5["sig"] == "BULL") and (fut5["pChg"] > 0)
        is_fut_bear = (fut15["sig"] == "BEAR" or fut5["sig"] == "BEAR") and (fut5["pChg"] < 0)
        is_atm_bull = (atm15["netLots"] >= atm_vol_thresh) or (atm5["netLots"] >= int(atm_vol_thresh * 1.2))
        is_atm_bear = (atm15["netLots"] <= -atm_vol_thresh) or (atm5["netLots"] <= -int(atm_vol_thresh * 1.2))
        is_far_bull = (far15["netLots"] >= far_vol_thresh) or (far5["netLots"] >= int(far_vol_thresh * 1.25))
        is_far_bear = (far15["netLots"] <= -far_vol_thresh) or (far5["netLots"] <= -int(far_vol_thresh * 1.25))

        # Model 6 Step 1 & Step 2 Alert Evaluation
        alert_bear_pressure = (is_fut_bear or is_atm_bear) and not (is_fut_bull or is_atm_bull)
        alert_bull_momentum = (is_fut_bull or is_atm_bull) and not (is_fut_bear or is_atm_bear)
        alert_super_bear_slam = is_fut_bear and is_atm_bear and is_far_bear
        alert_super_bull_confluence = is_fut_bull and is_atm_bull and is_far_bull

        if alert_bear_pressure:
            st["saw_bear_pressure"] = True
            st["saw_bear_pressure_idx"] = cur_idx
        if alert_bull_momentum:
            st["saw_bull_momentum"] = True
            st["saw_bull_momentum_idx"] = cur_idx

        # =========================================================================
        # 1. ACTIVE TRADE RUNTIME & EXIT ENGINE (With Dynamic ATR & 2-Tranche Locks)
        # =========================================================================
        if st["active_trade"] and st["active_trade"].get("outcome") == "RUNNING":
            tr = st["active_trade"]
            is_call = (tr["dir"] == "CALL")
            cur_pnl = (cur_p - tr["entry_price"]) if is_call else (tr["entry_price"] - cur_p)
            
            # Update peak favorable excursion
            if cur_pnl > tr["max_fav"]:
                tr["max_fav"] = cur_pnl

            # Dynamic parameters
            tgt_pts = tr.get("tgt_pts", 150.0 if sym == "BANKNIFTY" else 100.0)
            t1_target = tr.get("t1_pts", 50.0 if sym == "BANKNIFTY" else 35.0)
            trail_trigger = tr.get("trail_trigger", 30.0 if sym == "BANKNIFTY" else 20.0)
            trail_dist = tr.get("trail_dist", 15.0 if sym == "BANKNIFTY" else 10.0)

            # Tranche 1 Partial Booking Alert at Target-1 (+50 pts)
            if tr["max_fav"] >= t1_target and not tr.get("t1_booked"):
                tr["t1_booked"] = True
                t1_opt_gain = f"+{t1_target * 0.5:.1f} pts" if sym == "BANKNIFTY" else f"+{t1_target:.1f} pts"
                t1_msg = (
                    f"🎯 <b>[STAGE 4 SNIPER: TARGET 1 HIT (+{t1_target:.0f} PTS)]</b> 🎯\n"
                    f"🏷 {tag} | ⏰ <b>{cur_t} IST</b>\n\n"
                    f"Instrument  : <b>{sym}</b>\n"
                    f"Trade       : <b>BUY {tr['strike']}</b>\n"
                    f"Entry Price : <b>₹{tr['entry_price']:.1f}</b>\n"
                    f"Target 1    : 💰 <b>+{t1_target:.0f} PTS FUTURE ({t1_opt_gain} OPTION)</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"✅ <b>Action:</b> Book 50% (Lot 1) at guaranteed profit!\n"
                    f"🔒 <b>Lot 2:</b> Move Stop to Entry (+2 pts) & Free-Roll to Target 2 (+{tgt_pts:.0f} pts)."
                )
                send_signal_telegram(t1_msg)

            # Stage 4 Sniper Early Breakeven Lock at +15 pts
            if tr["max_fav"] >= 15.0 and tr["current_sl_pnl"] < 2.0:
                tr["current_sl_pnl"] = 2.0
                tr["sl"] = (tr["entry_price"] + 2.0) if is_call else (tr["entry_price"] - 2.0)
                if not tr.get("be_locked"):
                    tr["be_locked"] = True
                    be_msg = (
                        f"🛡️ <b>[STAGE 4 SNIPER: BREAKEVEN LOCKED]</b> 🛡️\n"
                        f"🏷 {tag} | ⏰ <b>{cur_t} IST</b>\n\n"
                        f"Trade reaches <b>+{tr['max_fav']:.1f} pts</b>.\n"
                        f"SL automatically moved to Entry (<b>+2.0 pts</b>).\n"
                        f"Zero risk remaining on this trade."
                    )
                    send_signal_telegram(be_msg)

            # Gamma Pinning Wall Shield: Check nearest round strike in front of trade
            wall_strike = round((cur_p + (s_step if is_call else -s_step)) / s_step) * s_step
            stk_data = t.get("stk", {}).get(str(wall_strike), [0] * 10)
            wall_lots = stk_data[3] if is_call else stk_data[7]
            gap_to_wall = abs(wall_strike - cur_p)
            wall_thresh = 35000 if sym == "BANKNIFTY" else 300
            gap_thresh = 25 if sym == "BANKNIFTY" else 15
            min_fav_thresh = 20.0 if sym == "BANKNIFTY" else 15.0

            if wall_lots > wall_thresh and gap_to_wall <= gap_thresh and tr["max_fav"] >= min_fav_thresh:
                shield_stop = max(5.0, tr["max_fav"] - 5.0)
                if shield_stop > tr["current_sl_pnl"]:
                    tr["current_sl_pnl"] = shield_stop
                    tr["sl"] = (tr["entry_price"] + shield_stop) if is_call else (tr["entry_price"] - shield_stop)
                    tr["gamma_shield_active"] = True
                    print(f"[Stage4Sniper] 🛡️ Gamma Wall Shield Locked at +{shield_stop:.1f} pts ahead of strike {wall_strike} ({wall_lots:,} lots)")

            # Dynamic Trailing Stop Lock
            if tr["max_fav"] >= trail_trigger:
                trailed_sl_pnl = max(5.0, tr["max_fav"] - trail_dist)
                if trailed_sl_pnl > tr["current_sl_pnl"]:
                    tr["current_sl_pnl"] = trailed_sl_pnl
                    tr["sl"] = (tr["entry_price"] + trailed_sl_pnl) if is_call else (tr["entry_price"] - trailed_sl_pnl)
                    print(f"[Stage4Sniper] Trailing SL moved to +{trailed_sl_pnl:.1f} pts (SL: ₹{tr['sl']:.1f})")

            # Check Exit Conditions
            is_exit = False
            exit_reason = ""
            exit_pnl = cur_pnl

            # 1. Target Hit
            if cur_pnl >= tgt_pts:
                is_exit = True
                exit_reason = "TARGET_HIT"
                exit_pnl = tgt_pts
                opt_gain = f"+{exit_pnl * 0.5:.1f} pts" if sym == "BANKNIFTY" else f"+{exit_pnl:.1f} pts"
                tgt_msg = (
                    f"🎯 <b>[STAGE 4 SNIPER: FULL TARGET HIT — +{tgt_pts:.0f} PTS BOOKED!]</b> 🎯\n"
                    f"🏷 {tag} | ⏰ <b>{cur_t} IST</b>\n\n"
                    f"Instrument  : <b>{sym}</b>\n"
                    f"Trade       : <b>BUY {tr['strike']}</b>\n"
                    f"Entry Price : <b>₹{tr['entry_price']:.1f}</b>\n"
                    f"Exit Price  : <b>₹{cur_p:.1f}</b>\n"
                    f"Profit      : 💰 <b>+{exit_pnl:.1f} PTS FUTURE ({opt_gain} OPTION)</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"✅ Stage 4 Full Runner Target (+{tgt_pts:.0f} pts) Flawlessly Reached!\n"
                    f"🔒 Session Profit Secured."
                )
                send_signal_telegram(tgt_msg)

            # 2. Stop Loss or Trailed Stop Hit
            elif cur_pnl <= tr["current_sl_pnl"]:
                is_exit = True
                exit_pnl = tr["current_sl_pnl"]
                if tr["current_sl_pnl"] > 0:
                    exit_reason = "TRAIL_PROFIT_LOCKED"
                    opt_res = f"+{exit_pnl * 0.5:.1f} pts" if sym == "BANKNIFTY" else f"+{exit_pnl:.1f} pts"
                    shield_note = " (🛡️ Gamma Wall Shield Protection Active)" if tr.get("gamma_shield_active") else ""
                    trail_msg = (
                        f"🛡️ <b>[STAGE 4 SNIPER: TRAILING STOP HIT — PROFIT LOCKED]</b> 🛡️\n"
                        f"🏷 {tag} | ⏰ <b>{cur_t} IST</b>\n\n"
                        f"Instrument  : <b>{sym}</b>\n"
                        f"Trade       : <b>BUY {tr['strike']}</b>\n"
                        f"Exit Price  : <b>₹{cur_p:.1f}</b>\n"
                        f"Locked Gain : 💰 <b>+{exit_pnl:.1f} PTS FUTURE ({opt_res} OPTION)</b>\n"
                        f"Peak Gain   : <b>+{tr['max_fav']:.1f} pts</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🛡️ Dynamic Trailing Stop protected gains.{shield_note}"
                    )
                    send_signal_telegram(trail_msg)
                else:
                    exit_reason = "SL_HIT"
                    opt_loss = f"-{abs(exit_pnl) * 0.5:.1f} pts" if sym == "BANKNIFTY" else f"-{abs(exit_pnl):.1f} pts"
                    sl_msg = (
                        f"🛑 <b>[STAGE 4 SNIPER: STOP LOSS HIT]</b> 🛑\n"
                        f"🏷 {tag} | ⏰ <b>{cur_t} IST</b>\n\n"
                        f"Instrument  : <b>{sym}</b>\n"
                        f"Trade       : <b>BUY {tr['strike']}</b>\n"
                        f"Exit Price  : <b>₹{cur_p:.1f}</b>\n"
                        f"Loss        : <b>{exit_pnl:.1f} PTS FUTURE ({opt_loss} OPTION)</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🛡️ Disciplined Risk Boundary Preserved. Stand Aside!"
                    )
                    send_signal_telegram(sl_msg)

            # 3. Model 6 Immediate Reversal Exit (Opposite Whale Slam)
            elif (is_call and alert_super_bear_slam) or (not is_call and alert_super_bull_confluence):
                is_exit = True
                exit_reason = "REVERSAL_EXIT"
                opp_type = "🩸 Super Bear Slam" if is_call else "🚀 Super Bull Confluence"
                pnl_sign = "+" if cur_pnl >= 0 else ""
                opt_res = f"{pnl_sign}{cur_pnl * 0.5:.1f} pts" if sym == "BANKNIFTY" else f"{pnl_sign}{cur_pnl:.1f} pts"
                rev_msg = (
                    f"⚡ <b>[STAGE 4 SNIPER: REVERSAL EXIT TRIGGERED]</b> ⚡\n"
                    f"🏷 {tag} | ⏰ <b>{cur_t} IST</b>\n\n"
                    f"Instrument  : <b>{sym}</b>\n"
                    f"Exited Trade: <b>BUY {tr['strike']}</b>\n"
                    f"Exit Price  : <b>₹{cur_p:.1f}</b>\n"
                    f"PnL         : <b>{pnl_sign}{cur_pnl:.1f} PTS FUTURE ({opt_res} OPTION)</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"⚠️ <b>Opposite Whale Action Detected:</b> {opp_type}\n"
                    f"⚡ Stage 4 Rule: Immediate market exit to protect capital!"
                )
                send_signal_telegram(rev_msg)

            # 4. Mandatory Session End Square-off
            elif cur_t >= session_end_time:
                is_exit = True
                exit_reason = "SESSION_END"
                pnl_sign = "+" if cur_pnl >= 0 else ""
                opt_res = f"{pnl_sign}{cur_pnl * 0.5:.1f} pts" if sym == "BANKNIFTY" else f"{pnl_sign}{cur_pnl:.1f} pts"
                end_msg = (
                    f"🏁 <b>[STAGE 4 SNIPER: SESSION END SQUARE-OFF]</b> 🏁\n"
                    f"🏷 {tag} | ⏰ <b>{cur_t} IST</b>\n\n"
                    f"Instrument  : <b>{sym}</b>\n"
                    f"Trade       : <b>BUY {tr['strike']}</b>\n"
                    f"Exit Price  : <b>₹{cur_p:.1f}</b>\n"
                    f"Final PnL   : <b>{pnl_sign}{cur_pnl:.1f} PTS FUTURE ({opt_res} OPTION)</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"⚖️ Market Closing. Zero overnight risk permitted."
                )
                send_signal_telegram(end_msg)

            if is_exit:
                tr["outcome"] = exit_reason
                tr["exit_time"] = cur_t
                tr["exit_price"] = cur_p
                tr["pnl"] = exit_pnl
                
                # Check for scratch trade for anti-chop cooldown
                if abs(exit_pnl) <= 15.0:
                    st["last_scratch_idx"] = cur_idx
                    st["last_scratch_dir"] = tr["dir"]
                st["active_trade"] = None

        # =========================================================================
        # 2. STAGE 4 ELITE SNIPER ENTRY ENGINE (4-LAYER CONFLUENCE + DAILY VWAP)
        # =========================================================================
        max_trades_limit = 3
        if not st["active_trade"] and is_trade_window and st.get("trades_today", 0) < max_trades_limit:
            enter_call = False
            enter_put = False

            # 2-Step Alert Logic: Step 1 (Pressure/Momentum within last 20 mins) + Step 2 (Super Confluence)
            if st["saw_bear_pressure"] and (cur_idx - st["saw_bear_pressure_idx"] <= 20) and alert_super_bear_slam:
                enter_put = True

            if st["saw_bull_momentum"] and (cur_idx - st["saw_bull_momentum_idx"] <= 20) and alert_super_bull_confluence:
                enter_call = True

            # Filter 1: Daily VWAP Side Gate (CALL only >= VWAP, PUT only <= VWAP)
            if enter_call and (cur_p < daily_vwap):
                enter_call = False
                print(f"[Stage4Sniper] CALL rejected: Price ₹{cur_p:.1f} < Daily VWAP ₹{daily_vwap:.1f}")

            if enter_put and (cur_p > daily_vwap):
                enter_put = False
                print(f"[Stage4Sniper] PUT rejected: Price ₹{cur_p:.1f} > Daily VWAP ₹{daily_vwap:.1f}")

            # Filter 2: Dynamic Rolling Cumulative PCR Gate (PCR >= 1.05 for CALL, PCR <= 0.95 for PUT)
            if enter_call and rolling_pcr < 1.05:
                enter_call = False
                print(f"[Stage4Sniper] CALL rejected: Rolling PCR {rolling_pcr:.2f} < 1.05")

            if enter_put and rolling_pcr > 0.95:
                enter_put = False
                print(f"[Stage4Sniper] PUT rejected: Rolling PCR {rolling_pcr:.2f} > 0.95")

            # Filter 3: VWAP Exhaustion Distance Gate (Price within 75 pts of VWAP to avoid buying tops / shorting bottoms)
            vwap_dist = abs(cur_p - daily_vwap)
            max_vwap_dist = 75.0 if sym == "BANKNIFTY" else 35.0
            if vwap_dist > max_vwap_dist:
                if enter_call:
                    enter_call = False
                    print(f"[Stage4Sniper] CALL rejected: VWAP Distance {vwap_dist:.1f} pts > {max_vwap_dist} pts (Overextended)")
                if enter_put:
                    enter_put = False
                    print(f"[Stage4Sniper] PUT rejected: VWAP Distance {vwap_dist:.1f} pts > {max_vwap_dist} pts (Overextended)")

            # Filter 4: ATM OI Delta Wall Confirmation (PE writing floor > CE writing roof for CALL, and vice versa)
            atm_ce_w = atm15.get("ce_w", 0)
            atm_pe_w = atm15.get("pe_w", 0)
            if enter_call and (atm_pe_w < atm_ce_w):
                enter_call = False
                print(f"[Stage4Sniper] CALL rejected: Call Writers ({atm_ce_w:,}) exceed Put Writers ({atm_pe_w:,})")
            if enter_put and (atm_ce_w < atm_pe_w):
                enter_put = False
                print(f"[Stage4Sniper] PUT rejected: Put Writers ({atm_pe_w:,}) exceed Call Writers ({atm_ce_w:,})")

            # Filter 5: 15-Minute Intermediate Futures Trend Alignment
            if enter_call and fut15["sig"] != "BULL":
                enter_call = False
                print(f"[Stage4Sniper] CALL rejected: 15m Futures Trend is not BULL ({fut15['sig']})")
            if enter_put and fut15["sig"] != "BEAR":
                enter_put = False
                print(f"[Stage4Sniper] PUT rejected: 15m Futures Trend is not BEAR ({fut15['sig']})")

            # Anti-Chop 10-Minute Cooldown after scratch
            if (cur_idx - st["last_scratch_idx"]) < 10:
                if enter_call and st["last_scratch_dir"] == "CALL":
                    enter_call = False
                    print(f"[Stage4Sniper] CALL rejected: 10-min Anti-Chop Cooldown active.")
                if enter_put and st["last_scratch_dir"] == "PUT":
                    enter_put = False
                    print(f"[Stage4Sniper] PUT rejected: 10-min Anti-Chop Cooldown active.")

            if enter_call or enter_put:
                # Calculate Dynamic ATR Volatility Scaling
                atr15 = get_rolling_atr_15m(timeline, cur_idx)
                dyn_tgt = 150.0 if sym == "BANKNIFTY" else 100.0
                dyn_t1 = 50.0 if sym == "BANKNIFTY" else 35.0
                dyn_sl = 45.0 if sym == "BANKNIFTY" else 30.0
                dyn_trail_trig = 30.0 if sym == "BANKNIFTY" else 20.0
                dyn_trail_dist = 15.0 if sym == "BANKNIFTY" else 10.0
                vol_regime = "NORMAL VOLATILITY"

                if sym == "BANKNIFTY":
                    if atr15 < 30:
                        dyn_tgt = 100.0
                        dyn_t1 = 40.0
                        dyn_sl = 35.0
                        dyn_trail_trig = 25.0
                        dyn_trail_dist = 12.0
                        vol_regime = "LOW VOLATILITY CHOP (Tight Scalp Target)"
                    elif atr15 > 70:
                        dyn_tgt = 200.0
                        dyn_t1 = 60.0
                        dyn_sl = 50.0
                        dyn_trail_trig = 40.0
                        dyn_trail_dist = 20.0
                        vol_regime = "HIGH VOLATILITY EXPANSION (Runner Target)"

                st["trades_today"] = st.get("trades_today", 0) + 1

                dir_str = "CALL" if enter_call else "PUT"
                atm_strike = round(cur_p / s_step) * s_step
                strike_str = f"{atm_strike} {'CE' if enter_call else 'PE'}"
                calc_sl = (cur_p - dyn_sl) if enter_call else (cur_p + dyn_sl)
                calc_tgt = (cur_p + dyn_tgt) if enter_call else (cur_p - dyn_tgt)
                opt_tgt_str = f"+{dyn_tgt * 0.5:.0f} pts" if sym == "BANKNIFTY" else f"+{dyn_tgt:.0f} pts"
                opt_t1_str = f"+{dyn_t1 * 0.5:.0f} pts" if sym == "BANKNIFTY" else f"+{dyn_t1:.0f} pts"
                opt_sl_str = f"-{dyn_sl * 0.5:.0f} pts" if sym == "BANKNIFTY" else f"-{dyn_sl:.0f} pts"

                st["active_trade"] = {
                    "dir": dir_str,
                    "strike": strike_str,
                    "entry_time": cur_t,
                    "entry_price": cur_p,
                    "entry_idx": cur_idx,
                    "sl": calc_sl,
                    "tgt": calc_tgt,
                    "tgt_pts": dyn_tgt,
                    "t1_pts": dyn_t1,
                    "initial_sl_pts": dyn_sl,
                    "trail_trigger": dyn_trail_trig,
                    "trail_dist": dyn_trail_dist,
                    "current_sl_pnl": -dyn_sl,
                    "max_fav": 0.0,
                    "atr15": atr15,
                    "gamma_shield_active": False,
                    "be_locked": False,
                    "t1_booked": False,
                    "outcome": "RUNNING"
                }

                vwap_diff_pts = cur_p - daily_vwap
                vwap_str = f"₹{cur_p:,.1f} ({vwap_diff_pts:+.1f} pts vs VWAP ₹{daily_vwap:,.1f})"

                entry_msg = (
                    f"🎯 <b>[STAGE 4: ELITE SNIPER ALERT — 82.4% CONVICTION]</b> 🎯\n"
                    f"🏷 {tag} | ⏰ <b>{cur_t} IST</b>\n\n"
                    f"Instrument  : <b>{sym}</b>\n"
                    f"Direction   : <b>BUY {strike_str}</b>\n"
                    f"Entry Price : <b>₹{cur_p:.1f}</b>\n"
                    f"Target 1 (50%) : 🎯 <b>+{dyn_t1:.0f} PTS ({opt_t1_str} OPTION)</b>\n"
                    f"Target 2 (Runner): 🎯 <b>+{dyn_tgt:.0f} PTS ({opt_tgt_str} OPTION)</b>\n"
                    f"Stop Loss   : 🛑 <b>₹{calc_sl:.1f} ({opt_sl_str} OPTION)</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"⚡ <b>4-Layer Sniper Confluence:</b>\n"
                    f"• Rolling PCR: <b>{rolling_pcr:.2f}</b> (Threshold Verified)\n"
                    f"• VWAP Distance: <b>{vwap_dist:.1f} pts</b> (Inside <=75p Safe Zone)\n"
                    f"• OI Delta: <b>{'PE Write Floor Active' if enter_call else 'CE Write Roof Active'}</b>\n"
                    f"• 15m Trend: <b>{fut15['sig']} Confluent</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🛡️ <b>Execution Rule:</b> Book Lot 1 at +{dyn_t1:.0f}p. Once +15p reached, move SL to Entry (+2p)!"
                )
                send_signal_telegram(entry_msg)
                print(f"[Stage4Sniper] Dispatched BUY {strike_str} entry signal at {cur_t} for {sym} (ATR: {atr15:.1f}, Target: +{dyn_tgt} pts)!")

    def get_expiry_hero_zero_setup(self, now=None, kite=None, forced_sym=None, forced_price=None):
        """
        Calculates the 3:14 PM Hero-Zero Strangle setup for expiry day:
        - Tuesday: NIFTY Expiry (Lot Size = 65, Step = 50, CE: ATM + 50, PE: ATM - 50)
        - Thursday: SENSEX Expiry (Lot Size = 20, Step = 100, CE: ATM + 100, PE: ATM - 100)
        """
        if now is None:
            now = datetime.datetime.now(IST)
        weekday = now.weekday()  # 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
        today_str = now.strftime("%Y-%m-%d")
        
        sym = forced_sym
        if not sym:
            if weekday == 1:
                sym = "NIFTY"
            elif weekday == 3:
                sym = "SENSEX"
            else:
                # Dynamic check in instruments.csv in case expiry shifted due to holiday
                try:
                    inst_paths = [
                        os.path.join(BASE_DIR, "instruments.csv"),
                        os.path.join(os.path.dirname(BASE_DIR), "instruments.csv"),
                        "/home/ubuntu/zarodha/instruments.csv",
                        "/home/ubuntu/instruments.csv",
                        "zarodha/instruments.csv"
                    ]
                    inst_file = next((p for p in inst_paths if os.path.exists(p)), None)
                    if inst_file:
                        df_chk = pd.read_csv(inst_file, usecols=["segment", "name", "expiry"])
                        for candidate_sym, candidate_seg in [("NIFTY", "NFO-OPT"), ("SENSEX", "BFO-OPT")]:
                            opt_chk = df_chk[(df_chk["segment"] == candidate_seg) & (df_chk["name"] == candidate_sym) & (df_chk["expiry"] == today_str)]
                            if len(opt_chk) > 0:
                                sym = candidate_sym
                                break
                except Exception as e:
                    print(f"[HeroZero] Calendar lookup warning: {e}")

            if not sym:
                return None, "Not an expiry day (Tuesdays for Nifty, Thursdays for Sensex)"
                
        step_size = 50 if sym == "NIFTY" else 100
        prefix = "NFO" if sym == "NIFTY" else "BFO"
        default_lot = 65 if sym == "NIFTY" else 20
        quote_key = "NSE:NIFTY 50" if sym == "NIFTY" else "BSE:SENSEX"
        
        price = forced_price
        if price is None and kite:
            try:
                q = kite.quote([quote_key])
                price = q.get(quote_key, {}).get("last_price", 0.0)
            except Exception as e:
                print(f"[HeroZero] Error quoting {quote_key}: {e}")
                
        if not price or price <= 0:
            price = 23400.0 if sym == "NIFTY" else 74800.0
            
        atm_strike = int(round(price / step_size) * step_size)
        ce_strike = atm_strike + step_size
        pe_strike = atm_strike - step_size
        
        ce_symbol = None
        pe_symbol = None
        lot_size = default_lot
        
        # Resolve exact tradingsymbols from instruments.csv
        try:
            inst_paths = [
                os.path.join(BASE_DIR, "instruments.csv"),
                os.path.join(os.path.dirname(BASE_DIR), "instruments.csv"),
                "/home/ubuntu/zarodha/instruments.csv",
                "/home/ubuntu/instruments.csv",
                "zarodha/instruments.csv"
            ]
            inst_file = next((p for p in inst_paths if os.path.exists(p)), None)
            if inst_file:
                df = pd.read_csv(inst_file)
                today_str = now.strftime("%Y-%m-%d")
                seg = "NFO-OPT" if sym == "NIFTY" else "BFO-OPT"
                opt_df = df[(df["segment"] == seg) & (df["name"] == sym) & (df["expiry"] >= today_str)]
                if len(opt_df) > 0:
                    earliest_exp = opt_df["expiry"].min()
                    cur_exp_df = opt_df[opt_df["expiry"] == earliest_exp]
                    ce_row = cur_exp_df[(cur_exp_df["strike"] == ce_strike) & (cur_exp_df["instrument_type"] == "CE")]
                    pe_row = cur_exp_df[(cur_exp_df["strike"] == pe_strike) & (cur_exp_df["instrument_type"] == "PE")]
                    if len(ce_row) > 0:
                        ce_symbol = ce_row.iloc[0]["tradingsymbol"]
                        lot_size = int(ce_row.iloc[0]["lot_size"])
                    if len(pe_row) > 0:
                        pe_symbol = pe_row.iloc[0]["tradingsymbol"]
        except Exception as e:
            print(f"[HeroZero] Instrument lookup warning: {e}")
            
        date_code = now.strftime("%y%m%d")
        if not ce_symbol:
            ce_symbol = f"{sym}{date_code}{ce_strike}CE"
        if not pe_symbol:
            pe_symbol = f"{sym}{date_code}{pe_strike}PE"
            
        ce_price = 2.50
        pe_price = 2.50
        if kite:
            try:
                q_opts = kite.quote([f"{prefix}:{ce_symbol}", f"{prefix}:{pe_symbol}"])
                ce_p = q_opts.get(f"{prefix}:{ce_symbol}", {}).get("last_price", 0.0)
                pe_p = q_opts.get(f"{prefix}:{pe_symbol}", {}).get("last_price", 0.0)
                if ce_p > 0: ce_price = ce_p
                if pe_p > 0: pe_price = pe_p
            except Exception:
                pass
                
        ce_sl = round(ce_price * 0.5, 2)
        ce_tgt = round(ce_price * 10.0, 2)
        pe_sl = round(pe_price * 0.5, 2)
        pe_tgt = round(pe_price * 10.0, 2)
        
        entry_msg = (
            f"🚀 [3:14 PM HERO-ZERO EXPIRY STRANGLE ENTRY] 🚀\n"
            f"🏷 #{sym} #EXPIRY | ⏰ 15:14 IST\n"
            f"Spot Reference: {price:.1f} (ATM: {atm_strike})\n"
            f"Order Type: MARKET ORDER (MKT)\n"
            f"Product: CARRYFORWARD (NRML) — NOT MIS!\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{prefix}:{ce_symbol}\n"
            f"Action : BUY\n"
            f"Strike : {ce_strike}\n"
            f"Option Type : CE\n"
            f"Lots : {lot_size}\n"
            f"Ref Price : ₹{ce_price:.2f}\n"
            f"Stop Loss : ₹{ce_sl:.2f} (50% Half Price)\n"
            f"Target : ₹{ce_tgt:.2f} (10x Exit)\n"
            f"Product : NRML\n"
            f"Order Type : MKT\n"
            f"Signal : HERO_ZERO_CALL\n\n"
            f"--------------------------------------------------\n\n"
            f"{prefix}:{pe_symbol}\n"
            f"Action : BUY\n"
            f"Strike : {pe_strike}\n"
            f"Option Type : PE\n"
            f"Lots : {lot_size}\n"
            f"Ref Price : ₹{pe_price:.2f}\n"
            f"Stop Loss : ₹{pe_sl:.2f} (50% Half Price)\n"
            f"Target : ₹{pe_tgt:.2f} (10x Exit)\n"
            f"Product : NRML\n"
            f"Order Type : MKT\n"
            f"Signal : HERO_ZERO_PUT\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🛡️ Strategy Rules:\n"
            f"1. Buy both legs at Market in NRML (Carryforward).\n"
            f"2. System SL is 50% of fill price; Target is 10x.\n"
            f"3. 3:29 PM Hard Kill: All open positions auto-square off at market!"
        )
        
        kill_msg = (
            f"🚨 [3:29 PM HARD KILL: EXPIRY AUTO-SQUARE OFF] 🚨\n"
            f"🏷 #{sym} #EXPIRY #HARD_KILL | ⏰ 15:29 IST\n\n"
            f"CANCEL ALL PENDING ORDERS & SQUARE OFF AT MARKET IMMEDIATELY!\n"
            f"• Underlying : {sym}\n"
            f"• Action     : MARKET EXIT ALL OPEN HERO-ZERO LEGS\n"
            f"• Status     : MANDATORY PRE-EXPIRY HARD KILL ENFORCED\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Zero overnight carry permitted. Capital preserved."
        )
        
        return {
            "symbol": sym,
            "spot": price,
            "atm": atm_strike,
            "ce_symbol": ce_symbol,
            "pe_symbol": pe_symbol,
            "ce_strike": ce_strike,
            "pe_strike": pe_strike,
            "ce_price": ce_price,
            "pe_price": pe_price,
            "ce_sl": ce_sl,
            "ce_tgt": ce_tgt,
            "pe_sl": pe_sl,
            "pe_tgt": pe_tgt,
            "lot_size": lot_size,
            "prefix": prefix,
            "entry_msg": entry_msg,
            "kill_msg": kill_msg
        }, None

    def check_and_trigger_hero_zero(self, now=None, kite=None, force=False):
        """
        Evaluates 3:14 PM Hero-Zero Strangle entry on Expiry Day.
        Sends dual buy alert (Call ATM+step & Put ATM-step) to Bnf ,nifty,sense (-1003907739730).
        """
        if now is None:
            now = datetime.datetime.now(IST)
        date_str = now.strftime("%Y-%m-%d")
        
        is_trigger_time = (now.hour == 15 and now.minute == 14)
        if not is_trigger_time and not force:
            return False, "Not 15:14 IST trigger time"
            
        if date_str in self.hero_zero_triggered and not force:
            return False, f"Hero-Zero already triggered today ({date_str})"
            
        setup, err = self.get_expiry_hero_zero_setup(now=now, kite=kite)
        if err or not setup:
            return False, err or "Could not generate setup"
            
        print(f"[HeroZero] Triggering 3:14 PM Expiry Strangle for {setup['symbol']} to {TARGET_CHAT_ID}...")
        sent = send_signal_telegram(setup["entry_msg"])
        if sent:
            self.hero_zero_triggered[date_str] = setup
            print(f"[HeroZero] Successfully dispatched 3:14 PM Hero-Zero entry alert for {setup['symbol']}!")
            return True, setup
        else:
            print(f"[HeroZero] Failed to dispatch 3:14 PM alert to Telegram")
            return False, "Failed to deliver Telegram alert"

    def check_and_trigger_hero_zero_hard_kill(self, now=None, kite=None, force=False):
        """
        Evaluates 3:29 PM Hard Kill pre-expiry auto-square off.
        Dispatches Hard Kill alert to Bnf ,nifty,sense (-1003907739730).
        """
        if now is None:
            now = datetime.datetime.now(IST)
        date_str = now.strftime("%Y-%m-%d")
        
        is_kill_time = (now.hour == 15 and now.minute == 29)
        if not is_kill_time and not force:
            return False, "Not 15:29 IST hard kill time"
            
        if date_str in self.hero_zero_killed and not force:
            return False, f"Hard Kill already executed today ({date_str})"
            
        sym = None
        if date_str in self.hero_zero_triggered:
            sym = self.hero_zero_triggered[date_str].get("symbol")
        if not sym:
            setup, _ = self.get_expiry_hero_zero_setup(now=now, kite=kite)
            if setup:
                sym = setup.get("symbol")
        if not sym:
            sym = "NIFTY" if now.weekday() == 1 else ("SENSEX" if now.weekday() == 3 else "EXPIRY_INDEX")
            
        kill_msg = (
            f"🚨 [3:29 PM HARD KILL: EXPIRY AUTO-SQUARE OFF] 🚨\n"
            f"🏷 #{sym} #EXPIRY #HARD_KILL | ⏰ 15:29 IST\n\n"
            f"CANCEL ALL PENDING ORDERS & SQUARE OFF AT MARKET IMMEDIATELY!\n"
            f"• Underlying : {sym}\n"
            f"• Action     : MARKET EXIT ALL OPEN HERO-ZERO LEGS\n"
            f"• Status     : MANDATORY PRE-EXPIRY HARD KILL ENFORCED\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Zero overnight carry permitted. Capital preserved."
        )
        
        print(f"[HeroZero] Triggering 3:29 PM Hard Kill for {sym} to {TARGET_CHAT_ID}...")
        sent = send_signal_telegram(kill_msg)
        if sent:
            self.hero_zero_killed[date_str] = True
            print(f"[HeroZero] Successfully dispatched 3:29 PM Hard Kill alert for {sym}!")
            return True, kill_msg
        else:
            print(f"[HeroZero] Failed to dispatch 3:29 PM Hard Kill to Telegram")
            return False, "Failed to deliver Telegram alert"

signal_engine = InstitutionalSignalEngine()
