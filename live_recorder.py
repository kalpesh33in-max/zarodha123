import os
import sys
import time
import json
import datetime
from zoneinfo import ZoneInfo
from kiteconnect import KiteConnect
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import env_config
from storage_manager import save_candle, prune_storage

IST = ZoneInfo("Asia/Kolkata")

class LiveMarketRecorder:
    def __init__(self):
        self.kite = KiteConnect(api_key=env_config.API_KEY)
        self.token_file = os.path.join(BASE_DIR, "access_token.txt")
        self.instruments_file = os.path.join(BASE_DIR, "instruments.csv")
        self.running = False
        self.inst_tokens = {}
        self.prev_state = {"BANKNIFTY": {}, "CRUDEOILM": {}}
        self.cum_totals = {"BANKNIFTY": {"ce": 0, "pe": 0}, "CRUDEOILM": {"ce": 0, "pe": 0}}
        self._init_instruments()

    def _init_instruments(self):
        try:
            if not os.path.exists(self.instruments_file):
                return
            df = pd.read_csv(self.instruments_file)
            
            # 1. BankNifty
            bnf_fut = df[df['tradingsymbol'] == 'BANKNIFTY26SEPFUT']
            bnf_fut_tok = int(bnf_fut.iloc[0]['instrument_token']) if len(bnf_fut) > 0 else 0
            bnf_strikes = [57000, 57100, 57200, 57300, 57400, 57500, 57600, 57700, 57800, 57900, 58000, 58100, 58200, 58300, 58400, 58500]
            
            bnf_opts = df[(df['segment'] == 'NFO-OPT') & (df['name'] == 'BANKNIFTY')]
            earliest_bnf_exp = bnf_opts['expiry'].min()
            bnf_current = bnf_opts[bnf_opts['expiry'] == earliest_bnf_exp]
            
            bnf_strike_tokens = {}
            for s in bnf_strikes:
                ce = bnf_current[(bnf_current['strike'] == s) & (bnf_current['instrument_type'] == 'CE')]
                pe = bnf_current[(bnf_current['strike'] == s) & (bnf_current['instrument_type'] == 'PE')]
                bnf_strike_tokens[s] = {
                    'ce': int(ce.iloc[0]['instrument_token']) if len(ce) > 0 else None,
                    'pe': int(pe.iloc[0]['instrument_token']) if len(pe) > 0 else None
                }

            # 2. CrudeOilM
            crude_fut = df[df['tradingsymbol'] == 'CRUDEOILM26SEPFUT']
            crude_fut_tok = int(crude_fut.iloc[0]['instrument_token']) if len(crude_fut) > 0 else 0
            crude_strikes = [8400, 8450, 8500, 8550, 8600, 8650, 8700, 8750, 8800, 8850, 8900, 8950, 9000]
            
            crude_opts = df[(df['segment'] == 'MCX-OPT') & (df['name'] == 'CRUDEOIL')]
            earliest_crude_exp = crude_opts['expiry'].min()
            crude_current = crude_opts[crude_opts['expiry'] == earliest_crude_exp]
            
            crude_strike_tokens = {}
            for s in crude_strikes:
                ce = crude_current[(crude_current['strike'] == s) & (crude_current['instrument_type'] == 'CE')]
                pe = crude_current[(crude_current['strike'] == s) & (crude_current['instrument_type'] == 'PE')]
                crude_strike_tokens[s] = {
                    'ce': int(ce.iloc[0]['instrument_token']) if len(ce) > 0 else None,
                    'pe': int(pe.iloc[0]['instrument_token']) if len(pe) > 0 else None
                }

            self.inst_tokens = {
                "BANKNIFTY": {
                    "fut_token": bnf_fut_tok,
                    "fut_symbol": "NFO:BANKNIFTY26SEPFUT",
                    "strikes": bnf_strikes,
                    "strike_tokens": bnf_strike_tokens
                },
                "CRUDEOILM": {
                    "fut_token": crude_fut_tok,
                    "fut_symbol": "MCX:CRUDEOILM26SEPFUT",
                    "strikes": crude_strikes,
                    "strike_tokens": crude_strike_tokens
                }
            }
            print("[LiveRecorder] Instrument tokens mapped successfully.")
        except Exception as e:
            print(f"[LiveRecorder] Instrument init error: {e}")

    def refresh_kite_token(self):
        try:
            if os.path.exists(self.token_file):
                tok = open(self.token_file).read().strip()
                self.kite.set_access_token(tok)
                return True
        except Exception:
            pass
        return False

    def is_market_open(self, symbol):
        now = datetime.datetime.now(IST)
        if now.weekday() > 4: # Weekend
            return False
            
        t = now.time()
        if symbol == "BANKNIFTY":
            # 09:15 to 15:30
            return datetime.time(9, 15) <= t <= datetime.time(15, 30)
        elif symbol == "CRUDEOILM":
            # 09:00 to 23:30
            return datetime.time(9, 0) <= t <= datetime.time(23, 30)
        return False

    def record_tick(self):
        """Called every minute to record live data."""
        self.refresh_kite_token()
        now = datetime.datetime.now(IST)
        date_str = now.strftime("%d-%m-%Y")
        time_str = now.strftime("%H:%M")

        for sym in ["BANKNIFTY", "CRUDEOILM"]:
            if not self.is_market_open(sym):
                continue

            try:
                cfg = self.inst_tokens.get(sym)
                if not cfg:
                    self._init_instruments()
                    cfg = self.inst_tokens.get(sym)
                    if not cfg: continue

                # Quote futures
                fut_sym = cfg["fut_symbol"]
                q = self.kite.quote([fut_sym])
                f_data = q.get(fut_sym, {})
                ltp = f_data.get("last_price", 0.0)
                if ltp <= 0:
                    continue

                # Quote option strikes
                opt_symbols = []
                for s in cfg["strikes"]:
                    ce_t = cfg["strike_tokens"][s]["ce"]
                    pe_t = cfg["strike_tokens"][s]["pe"]
                    if ce_t: opt_symbols.append(ce_t)
                    if pe_t: opt_symbols.append(pe_t)

                opt_quotes = self.kite.quote(opt_symbols) if opt_symbols else {}

                stk_map = {}
                tot_ce_b, tot_ce_w, tot_ce_sc, tot_ce_unw = 0, 0, 0, 0
                tot_pe_b, tot_pe_w, tot_pe_sc, tot_pe_unw = 0, 0, 0, 0
                minute_ce_chg, minute_pe_chg = 0, 0

                prev_dict = self.prev_state[sym]

                for s in cfg["strikes"]:
                    ce_t = cfg["strike_tokens"][s]["ce"]
                    pe_t = cfg["strike_tokens"][s]["pe"]

                    ce_q = opt_quotes.get(str(ce_t), {})
                    pe_q = opt_quotes.get(str(pe_t), {})

                    ce_oi = ce_q.get("oi", 0)
                    pe_oi = pe_q.get("oi", 0)
                    ce_p = ce_q.get("last_price", 0.0)
                    pe_p = pe_q.get("last_price", 0.0)
                    ce_vol = ce_q.get("volume", 0)
                    pe_vol = pe_q.get("volume", 0)

                    prev_s = prev_dict.get(s, {
                        'ce_oi': ce_oi, 'pe_oi': pe_oi,
                        'ce_p': ce_p, 'pe_p': pe_p,
                        'ce_vol': ce_vol, 'pe_vol': pe_vol
                    })

                    d_ce_oi = ce_oi - prev_s['ce_oi']
                    d_pe_oi = pe_oi - prev_s['pe_oi']
                    d_ce_p = ce_p - prev_s['ce_p']
                    d_pe_p = pe_p - prev_s['pe_p']
                    d_ce_vol = max(0, ce_vol - prev_s['ce_vol'])
                    d_pe_vol = max(0, pe_vol - prev_s['pe_vol'])

                    minute_ce_chg += d_ce_oi
                    minute_pe_chg += d_pe_oi

                    # Categorize into 4 quadrants
                    ce_b = d_ce_vol if (d_ce_p >= 0 and d_ce_oi >= 0) else 0
                    ce_w = d_ce_vol if (d_ce_p < 0 and d_ce_oi >= 0) else 0
                    ce_sc = d_ce_vol if (d_ce_p >= 0 and d_ce_oi < 0) else 0
                    ce_unw = d_ce_vol if (d_ce_p < 0 and d_ce_oi < 0) else 0

                    pe_b = d_pe_vol if (d_pe_p >= 0 and d_pe_oi >= 0) else 0
                    pe_w = d_pe_vol if (d_pe_p < 0 and d_pe_oi >= 0) else 0
                    pe_sc = d_pe_vol if (d_pe_p >= 0 and d_pe_oi < 0) else 0
                    pe_unw = d_pe_vol if (d_pe_p < 0 and d_pe_oi < 0) else 0

                    tot_ce_b += ce_b; tot_ce_w += ce_w; tot_ce_sc += ce_sc; tot_ce_unw += ce_unw
                    tot_pe_b += pe_b; tot_pe_w += pe_w; tot_pe_sc += pe_sc; tot_pe_unw += pe_unw

                    # 12-element strike vector
                    stk_map[str(s)] = [
                        d_ce_oi, d_pe_oi,
                        ce_b, ce_w, ce_sc, ce_unw,
                        pe_b, pe_w, pe_sc, pe_unw,
                        ce_p, pe_p
                    ]

                    # Save state for next minute
                    prev_dict[s] = {
                        'ce_oi': ce_oi, 'pe_oi': pe_oi,
                        'ce_p': ce_p, 'pe_p': pe_p,
                        'ce_vol': ce_vol, 'pe_vol': pe_vol
                    }

                self.cum_totals[sym]["ce"] += minute_ce_chg
                self.cum_totals[sym]["pe"] += minute_pe_chg

                tot_lots = (tot_ce_b + tot_ce_w + tot_ce_sc + tot_ce_unw +
                            tot_pe_b + tot_pe_w + tot_pe_sc + tot_pe_unw)

                candle_obj = {
                    "time": time_str,
                    "price": ltp,
                    "ce_chg": minute_ce_chg,
                    "pe_chg": minute_pe_chg,
                    "cum_ce": self.cum_totals[sym]["ce"],
                    "cum_pe": self.cum_totals[sym]["pe"],
                    "ce_b": tot_ce_b, "ce_w": tot_ce_w, "ce_sc": tot_ce_sc, "ce_unw": tot_ce_unw,
                    "pe_b": tot_pe_b, "pe_w": tot_pe_w, "pe_sc": tot_pe_sc, "pe_unw": tot_pe_unw,
                    "tot_lots": tot_lots,
                    "stk": stk_map
                }

                save_candle(sym, date_str, candle_obj, [str(s) for s in cfg["strikes"]])
                print(f"[LiveRecorder] Saved live {sym} tick @ {time_str} | Price: {ltp} | dCE: {minute_ce_chg} | dPE: {minute_pe_chg}")
            except Exception as e:
                print(f"[LiveRecorder] Error recording {sym}: {e}")

    def run_loop(self):
        self.running = True
        print("[LiveRecorder] Service active. Recording BankNifty (09:15-15:30) and CrudeOilM (09:00-23:30)...")
        while self.running:
            try:
                # Align to top of minute
                now = datetime.datetime.now()
                sec_to_next_min = 60 - now.second
                time.sleep(sec_to_next_min)
                self.record_tick()
            except Exception as ex:
                print(f"[LiveRecorder] Loop exception: {ex}")
                time.sleep(5)

if __name__ == "__main__":
    recorder = LiveMarketRecorder()
    recorder.record_tick()
