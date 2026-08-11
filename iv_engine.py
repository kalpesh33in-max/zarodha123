import math
from datetime import datetime, timedelta
import collections

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
        """Silently logs the BankNifty Direction Score every minute for testing."""
        while not self._stop_event.is_set():
            time.sleep(60)
            try:
                # We need the symbols for BN Futures, CE and PE. 
                # Since we don't have them hardcoded, we will infer them from the snapshots.
                bn_fut = next((sym for sym in self.snapshots if sym.startswith("BANKNIFTY") and sym.endswith("FUT")), None)
                if not bn_fut:
                    continue
                    
                # Find ATM Options (closest strike to Future price)
                fut_price = self.snapshots[bn_fut].get("close_price", 0)
                if fut_price <= 0:
                    continue
                    
                ce_syms = [s for s in self.snapshots if s.endswith("CE") and "BANKNIFTY" in s]
                pe_syms = [s for s in self.snapshots if s.endswith("PE") and "BANKNIFTY" in s]
                
                if not ce_syms or not pe_syms:
                    continue
                    
                # Parse strikes from symbols (e.g. BANKNIFTY26AUG45000CE)
                def get_strike(sym):
                    import re
                    match = re.search(r'(\d+)(CE|PE)$', sym)
                    return int(match.group(1)) if match else 0
                    
                closest_ce = min(ce_syms, key=lambda s: abs(get_strike(s) - fut_price))
                closest_pe = min(pe_syms, key=lambda s: abs(get_strike(s) - fut_price))
                
                score = self.calculate_score(
                    future_symbol=bn_fut,
                    ce_symbol=closest_ce,
                    pe_symbol=closest_pe,
                    future_data=self.snapshots[bn_fut],
                    ce_data=self.snapshots[closest_ce],
                    pe_data=self.snapshots[closest_pe]
                )
                
                print(f"[IV ENGINE] BANKNIFTY Direction Score: {score:.1f}/100 | "
                      f"FUT: {fut_price} | "
                      f"CE ROC: {self.iv_roc.get(closest_ce, 0):.2f}% | "
                      f"PE ROC: {self.iv_roc.get(closest_pe, 0):.2f}%")
                      
            except Exception as e:
                print(f"[IV ENGINE] Error in logging loop: {e}")

        
    def _get_time_to_expiry_years(self, expiry_date):
        """Calculate T in years from now until 15:30 IST on expiry date."""
        now = datetime.now()
        # Parse expiry date (assuming format "DD-MM-YYYY")
        if isinstance(expiry_date, str):
            try:
                expiry = datetime.strptime(expiry_date, "%d-%m-%Y").replace(hour=15, minute=30, second=0)
            except ValueError:
                return 0.01 # Fallback
        else:
            expiry = expiry_date
            
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
        fut_open = future_data.get("open", future_data["ltp"])
        fut_ltp = future_data["ltp"]
        if fut_ltp > fut_open:
            score += 15
        elif fut_ltp < fut_open:
            score -= 15
            
        # 2. Futures Volume (20 pts) -> Need average volume for "2.4x normal", simplistic for V1
        fut_vol = future_data.get("volume", 0)
        
        # 3. CE/PE Price Pressure (20 pts)
        ce_ltp = ce_data.get("ltp", 0)
        ce_open = ce_data.get("open", ce_ltp)
        pe_ltp = pe_data.get("ltp", 0)
        pe_open = pe_data.get("open", pe_ltp)
        
        if ce_ltp > ce_open and pe_ltp < pe_open:
            score += 10
        elif pe_ltp > pe_open and ce_ltp < ce_open:
            score -= 10
            
        # 4. CE/PE Volume (15 pts)
        ce_vol = ce_data.get("volume", 0)
        pe_vol = pe_data.get("volume", 0)
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
