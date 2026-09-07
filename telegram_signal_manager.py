import os
import sys
import time
import json
import requests
import datetime
from zoneinfo import ZoneInfo

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import env_config

IST = ZoneInfo("Asia/Kolkata")

PRIMARY_CHAT_ID = "-1003907739730"
FALLBACK_CHAT_ID = getattr(env_config, "CHAT_ID_BN", "-1003665271298")

BOT_TOKENS = [
    getattr(env_config, "TELE_TOKEN_BN", "8190193308:AAGQGzTdKzZoytMF-YRnM_PwtYKY88cAUHs"),
    getattr(env_config, "TELEGRAM_TOKEN", ""),
    getattr(env_config, "TELE_TOKEN_STOCKS", "")
]
BOT_TOKENS = [t for t in BOT_TOKENS if t and len(t) > 20]

def send_signal_telegram(message, chat_ids=None):
    """Sends institutional trade alert to designated Telegram channels/groups."""
    if not chat_ids:
        chat_ids = [PRIMARY_CHAT_ID]
        if FALLBACK_CHAT_ID and FALLBACK_CHAT_ID not in chat_ids:
            chat_ids.append(FALLBACK_CHAT_ID)

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


class InstitutionalSignalEngine:
    def __init__(self):
        self.session_states = {}

    def process_live_timeline(self, sym, date_str, timeline, strikes):
        if not timeline or len(timeline) < 5:
            return

        cur_t = timeline[-1]["time"]
        cur_p = timeline[-1]["price"]

        if sym == "BANKNIFTY":
            sessions = [
                {"name": "MORNING SWING #1", "start": "09:20", "end": "11:30"},
                {"name": "AFTERNOON SWING #2", "start": "11:30", "end": "15:15"}
            ]
            dist_thresh = 250
            near_thresh = 200
            s_step = 100
            net_thresh = 2200
            pchg_thresh = 35
            sl_pts = 75
            tgt_pts = 120
            tag = "#BNF #BANKNIFTY"
        elif sym == "CRUDEOILM":
            sessions = [
                {"name": "DAY SWING #1", "start": "09:15", "end": "17:00"},
                {"name": "EVENING MCX SWING #2", "start": "17:00", "end": "23:15"}
            ]
            dist_thresh = 100
            near_thresh = 50
            s_step = 50
            net_thresh = 250
            pchg_thresh = 10
            sl_pts = 20
            tgt_pts = 35
            tag = "#CRUDEOIL #MCX"
        else:
            return

        m_range = 999999
        if sym == "BANKNIFTY":
            m_sub = [t for t in timeline if "09:20" <= t["time"] <= "11:30"]
            if m_sub:
                m_high = max(t["price"] for t in m_sub)
                m_low = min(t["price"] for t in m_sub)
                m_range = m_high - m_low

        for sess in sessions:
            if not (sess["start"] <= cur_t <= sess["end"]):
                continue

            sess_key = f"{sym}_{date_str}_{sess['name']}"
            if sess_key not in self.session_states:
                self.session_states[sess_key] = {
                    "trade": None,
                    "entry_sent": False,
                    "exit_sent": False,
                    "cStreak": 0,
                    "pStreak": 0
                }
            st = self.session_states[sess_key]

            # If afternoon BankNifty and morning was a tight box (< 200 pts), filter chop
            if sym == "BANKNIFTY" and sess["name"] == "AFTERNOON SWING #2" and m_range < 200:
                continue

            sub = [t for t in timeline if sess["start"] <= t["time"] <= sess["end"]]
            if len(sub) < 3:
                continue

            p0 = sub[0]["price"]

            if not st["trade"]:
                sliceAvg = sum(x["price"] for x in sub) / len(sub)
                bull, bear = 0, 0
                near_ce_w, near_pe_w = 0, 0

                for pt in sub:
                    stk = pt.get("stk", {})
                    for s in strikes:
                        s_num = float(s)
                        dist = s_num - sliceAvg
                        if dist <= -dist_thresh and str(s) in stk:
                            bull += stk[str(s)][2] + stk[str(s)][4]
                            bear += stk[str(s)][3] + stk[str(s)][5]
                        if dist >= dist_thresh and str(s) in stk:
                            bull += stk[str(s)][7] + stk[str(s)][8]
                            bear += stk[str(s)][6] + stk[str(s)][9]
                        if abs(dist) <= near_thresh and str(s) in stk:
                            near_ce_w += stk[str(s)][3]
                            near_pe_w += stk[str(s)][7]

                net_lots = bull - bear
                p_chg = cur_p - p0

                is_call = (net_lots >= net_thresh) and (p_chg >= pchg_thresh) and (near_pe_w >= near_ce_w * 0.85)
                is_put = (net_lots <= -net_thresh) and (p_chg <= -pchg_thresh) and (near_ce_w >= near_pe_w * 0.85)

                if is_call:
                    st["cStreak"] += 1
                    st["pStreak"] = 0
                    if st["cStreak"] == 3:
                        atm_strike = round(cur_p / s_step) * s_step
                        st["trade"] = {
                            "dir": "CALL",
                            "strike": f"{atm_strike} CE",
                            "entry_time": cur_t,
                            "entry_price": cur_p,
                            "sl": cur_p - sl_pts,
                            "tgt": cur_p + tgt_pts,
                            "outcome": "RUNNING"
                        }
                elif is_put:
                    st["pStreak"] += 1
                    st["cStreak"] = 0
                    if st["pStreak"] == 3:
                        atm_strike = round(cur_p / s_step) * s_step
                        st["trade"] = {
                            "dir": "PUT",
                            "strike": f"{atm_strike} PE",
                            "entry_time": cur_t,
                            "entry_price": cur_p,
                            "sl": cur_p + sl_pts,
                            "tgt": cur_p - tgt_pts,
                            "outcome": "RUNNING"
                        }
                else:
                    st["cStreak"] = 0
                    st["pStreak"] = 0

                if st["trade"] and not st["entry_sent"]:
                    tr = st["trade"]
                    opt_tgt = "+60+ pts" if sym == "BANKNIFTY" else "+18 pts"
                    opt_sl = "-35 pts" if sym == "BANKNIFTY" else "-10 pts"
                    
                    entry_msg = (
                        f"🚀 *INSTITUTIONAL SIGNAL: BUY {tr['dir']}* 🚀\n"
                        f"🏷 {tag}\n\n"
                        f"Instrument  : *{sym}*\n"
                        f"Session     : *{sess['name']}*\n"
                        f"Action      : *BUY {tr['strike']}*\n"
                        f"Entry Price : *₹{tr['entry_price']:.1f}* (Future Ref)\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🎯 Target   : *₹{tr['tgt']:.1f}* (+{tgt_pts} pts Fut | ~{opt_tgt} Opt)\n"
                        f"🛑 Stop Loss: *₹{tr['sl']:.1f}* (-{sl_pts} pts Fut | ~{opt_sl} Opt)\n"
                        f"Risk/Reward : *1:2 Disciplined Confluence*\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"⏰ Time     : *{cur_t} IST*\n"
                        f"📊 Order Flow: 3-Min Latch Confirmed + Writing Support"
                    )
                    send_signal_telegram(entry_msg)
                    st["entry_sent"] = True

            if st["trade"] and st["trade"]["outcome"] == "RUNNING":
                tr = st["trade"]
                cur_pnl = (cur_p - tr["entry_price"]) if tr["dir"] == "CALL" else (tr["entry_price"] - cur_p)

                if cur_pnl >= tgt_pts:
                    tr["outcome"] = "TARGET_HIT"
                    if not st["exit_sent"]:
                        opt_gain = f"+{cur_pnl * 0.5:.1f} pts" if sym == "BANKNIFTY" else f"+{cur_pnl:.1f} pts"
                        tgt_msg = (
                            f"🎯 *TARGET ACHIEVED — PROFIT BOOKED!* 🎯\n"
                            f"🏷 {tag}\n\n"
                            f"Instrument  : *{sym}*\n"
                            f"Trade       : *BUY {tr['strike']}*\n"
                            f"Exit Price  : *₹{cur_p:.1f}*\n"
                            f"Profit      : 💰 *+{cur_pnl:.1f} PTS FUTURE ({opt_gain} OPTION)*\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"✅ Status    : *1:2 TARGET HIT — PROFIT LOCKED*\n"
                            f"🔒 Session   : *LOCKED (Discipline forbids overtrading)*\n"
                            f"⏰ Time      : *{cur_t} IST*"
                        )
                        send_signal_telegram(tgt_msg)
                        st["exit_sent"] = True

                elif cur_pnl <= -sl_pts:
                    tr["outcome"] = "SL_HIT"
                    if not st["exit_sent"]:
                        opt_loss = f"-{abs(cur_pnl) * 0.5:.1f} pts" if sym == "BANKNIFTY" else f"-{abs(cur_pnl):.1f} pts"
                        sl_msg = (
                            f"🛑 *STOP LOSS EXECUTED* 🛑\n"
                            f"🏷 {tag}\n\n"
                            f"Instrument  : *{sym}*\n"
                            f"Trade       : *BUY {tr['strike']}*\n"
                            f"Exit Price  : *₹{cur_p:.1f}*\n"
                            f"Loss        : *{cur_pnl:.1f} PTS FUTURE ({opt_loss} OPTION)*\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"🛡️ Action    : *Capital Preserved. Stand Aside!*\n"
                            f"🔒 Session   : *LOCKED (Zero revenge trading)*\n"
                            f"⏰ Time      : *{cur_t} IST*"
                        )
                        send_signal_telegram(sl_msg)
                        st["exit_sent"] = True

signal_engine = InstitutionalSignalEngine()
