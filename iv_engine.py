import math
from datetime import datetime, timedelta
import collections
from telegram_utils import send_telegram_message
from env_config import TELE_TOKEN_REPORTS, TELE_CHAT_ID_REPORTS

# Risk-free rate (approximate for India)
RISK_FREE_RATE = 0.07

def norm_cdf(x):
    """Cumulative distribution function for standard normal distribution."""
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0

def norm_pdf(x):
    """Probability density function for standard normal distribution."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)

def black_scholes_price(S, K, T, r, sigma, option_type="CE"):
    """
    Calculate the Black-Scholes option price.
    S: Underlying price (Future LTP)
    K: Strike price
    T: Time to expiry in years
    r: Risk-free rate
    sigma: Implied volatility
    """
    if T <= 0 or sigma <= 0:
        return max(0.0, S - K) if option_type == "CE" else max(0.0, K - S)

    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    if option_type == "CE":
        price = S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
    else:
        price = K * math.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1)
    
    return price

def calculate_iv(target_price, S, K, T, r=RISK_FREE_RATE, option_type="CE", max_iterations=100, tolerance=1e-5):
    """
    Calculate Implied Volatility using the Newton-Raphson method.
    """
    # Handle edge cases (deep in the money or expiry)
    intrinsic = max(0.0, S - K) if option_type == "CE" else max(0.0, K - S)
    if target_price <= intrinsic:
        return 0.001  # Minimum IV
    
    if T <= 0:
        return 0.001

    sigma = 0.3  # Initial guess (30% IV)
    
    for _ in range(max_iterations):
        price = black_scholes_price(S, K, T, r, sigma, option_type)
        diff = price - target_price
        
        if abs(diff) < tolerance:
            return sigma
            
        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        vega = S * norm_pdf(d1) * math.sqrt(T)
        
        if vega == 0:
            return sigma
            
        sigma -= diff / vega
        
        if sigma <= 0:
            sigma = 0.001  # Prevent negative IV
            
    return sigma


import threading
import time

# ---- 1-MINUTE ENGINE ----

class DirectionEngine:
    def __init__(self):
        # Store 1-minute snapshots per token
        self.snapshots = collections.defaultdict(dict)
        # Store active IV and ROC for fast lookups
        self.current_iv = {}
        self.iv_roc = {}
        
        # Start a background thread to log scores for BankNifty
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._logging_loop, daemon=True)
        self._thread.start()
        
    def _logging_loop(self):
        """Silently logs Direction Score every minute for BANKNIFTY (day session) and CRUDEOIL/CRUDEOILM (after 15:30)."""
        while not self._stop_event.is_set():
            time.sleep(60)
            try:
                # Determine target underlyings based on time (BANKNIFTY during day, CRUDEOIL/CRUDEOILM after 15:30)
                now = datetime.now()
                target_names = ["BANKNIFTY"]
                if now.hour >= 15 and now.minute >= 30 or now.hour > 15:
                    target_names = ["CRUDEOIL", "CRUDEOILM", "BANKNIFTY"]

                for name in target_names:
                    fut_symbol = next((sym for sym in self.snapshots if sym.startswith(name) and ("FUT" in sym or sym.endswith("-I"))), None)
                    if not fut_symbol:
                        continue
                        
                    fut_price = self.snapshots[fut_symbol].get("close_price", 0)
                    if fut_price <= 0:
                        continue
                        
                    ce_syms = [s for s in self.snapshots if s.endswith("CE") and name in s]
                    pe_syms = [s for s in self.snapshots if s.endswith("PE") and name in s]
                    
                    if not ce_syms or not pe_syms:
                        continue
                        
                    def get_strike(sym):
                        import re
                        match = re.search(r'(\d+)(CE|PE)$', sym)
                        return int(match.group(1)) if match else 0
                        
                    closest_ce = min(ce_syms, key=lambda s: abs(get_strike(s) - fut_price))
                    closest_pe = min(pe_syms, key=lambda s: abs(get_strike(s) - fut_price))
                    
                    score = self.calculate_score(
                        future_symbol=fut_symbol,
                        ce_symbol=closest_ce,
                        pe_symbol=closest_pe,
                        future_data=self.snapshots[fut_symbol],
                        ce_data=self.snapshots[closest_ce],
                        pe_data=self.snapshots[closest_pe]
                    )
                    
                    signal_label = "⚪ NEUTRAL"
                    if score > 50:
                        signal_label = "🟢 BULLISH TRIAL (>50)"
                    elif score < 50:
                        signal_label = "🔴 BEARISH TRIAL (<50)"
                    
                    msg = (f"[IV ENGINE] {name} Score: {score:.1f}/100 {signal_label} | "
                           f"FUT: {fut_price} | "
                           f"CE ROC: {self.iv_roc.get(closest_ce, 0):.2f}% | "
                           f"PE ROC: {self.iv_roc.get(closest_pe, 0):.2f}%")
                    print(msg)
                    
                    # Send to Reports Telegram channel if Bullish (>50) or Bearish (<50)
                    if score != 50:
                        try:
                            send_telegram_message(msg, chat_id=TELE_CHAT_ID_REPORTS, token=TELE_TOKEN_REPORTS)
                        except Exception as te:
                            print(f"Failed to send IV ROC alert to telegram: {te}")
                          
            except Exception as e:
                print(f"[IV ENGINE] Error in logging loop: {e}")

        
    def _get_time_to_expiry_years(self, expiry_date):
        """Calculate T in years from now until 15:30 IST on expiry date."""
        now = datetime.now()
        
        # Ensure expiry is a datetime object
        if isinstance(expiry_date, str):
            try:
                expiry = datetime.strptime(expiry_date, "%d-%m-%Y")
            except ValueError:
                return 0.01 # Fallback
        elif hasattr(expiry_date, "date"):
            # It's already a datetime or timestamp
            expiry = expiry_date
        else:
            # It's likely a datetime.date object
            expiry = datetime.combine(expiry_date, datetime.min.time())
            
        # Standardize the expiry time to end of trading day (15:30)
        if isinstance(expiry, datetime):
            expiry = expiry.replace(hour=15, minute=30, second=0, microsecond=0)
            
        diff = (expiry - now).total_seconds()
        years = max(diff / (365 * 24 * 3600), 0.00001)
        return years

    def process_tick(self, symbol, ltp, volume, instrument_data):
        """
        Takes raw tick data, calculates IV, stores 1-minute intervals, 
        and calculates IV ROC.
        """
        now = datetime.now()
        current_minute = now.strftime("%Y-%m-%d %H:%M")
        
        is_option = instrument_data.get("instrument_type") in ["CE", "PE"]
        
        # Snapshot Logic (1-minute) for both futures and options
        state = self.snapshots[symbol]
        
        iv = 0
        if is_option:
            option_type = instrument_data.get("instrument_type")
            strike = instrument_data.get("strike")
            expiry = instrument_data.get("expiry")
            
            underlying_ltp = instrument_data.get("u_ltp")
            if underlying_ltp and underlying_ltp > 0:
                T = self._get_time_to_expiry_years(expiry)
                iv = calculate_iv(ltp, underlying_ltp, strike, T, option_type=option_type)
                self.current_iv[symbol] = iv

        if state.get("minute") != current_minute:
            if "minute" in state and is_option:
                prev_iv = state.get("open_iv", iv)
                close_iv = state.get("close_iv", iv)
                if prev_iv > 0:
                    roc = ((close_iv - prev_iv) / prev_iv) * 100
                    self.iv_roc[symbol] = roc
            
            state["minute"] = current_minute
            state["open_iv"] = iv
            state["open_price"] = ltp
            state["open_volume"] = volume
            
        state["close_iv"] = iv
        state["close_price"] = ltp
        state["close_volume"] = volume


    def calculate_score(self, future_symbol, ce_symbol, pe_symbol, future_data, ce_data, pe_data):
        """
        Calculates the BankNifty Direction Score (0-100).
        Weights:
        - Futures price/momentum: 30
        - Futures volume: 20
        - CE/PE price pressure: 20
        - CE/PE volume: 15
        - IV: 7
        - IV ROC: 8
        """
        score = 50  # Neutral baseline
        
        # 1. Futures Momentum (30 pts)
        fut_ltp = future_data.get("close_price", 0)
        fut_open = future_data.get("open_price", fut_ltp)
        if fut_ltp > fut_open:
            score += 15
        elif fut_ltp < fut_open:
            score -= 15
            
        # 2. Futures Volume (20 pts) -> Need average volume for "2.4x normal", simplistic for V1
        fut_vol = future_data.get("close_volume", 0)
        
        # 3. CE/PE Price Pressure (20 pts)
        ce_ltp = ce_data.get("close_price", 0)
        ce_open = ce_data.get("open_price", ce_ltp)
        pe_ltp = pe_data.get("close_price", 0)
        pe_open = pe_data.get("open_price", pe_ltp)
        
        if ce_ltp > ce_open and pe_ltp < pe_open:
            score += 10
        elif pe_ltp > pe_open and ce_ltp < ce_open:
            score -= 10
            
        # 4. CE/PE Volume (15 pts)
        ce_vol = ce_data.get("close_volume", 0)
        pe_vol = pe_data.get("close_volume", 0)
        if ce_vol > pe_vol * 1.5:
            score += 7.5
        elif pe_vol > ce_vol * 1.5:
            score -= 7.5
            
        # 5. IV & IV ROC (15 pts)
        ce_roc = self.iv_roc.get(ce_symbol, 0)
        pe_roc = self.iv_roc.get(pe_symbol, 0)
        
        if ce_roc > 5 and pe_roc <= 0:
            # Bullish IV expansion
            score += 7.5
        elif pe_roc > 5 and ce_roc <= 0:
            # Bearish IV expansion
            score -= 7.5

        return min(max(score, 0), 100) # Clamp between 0 and 100

direction_engine = DirectionEngine()
