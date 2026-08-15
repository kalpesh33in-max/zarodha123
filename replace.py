import re

with open('heatmap_engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

# We want to remove process_volume_burst_logic, process_future_burst, and process_option_logic.
# They span from def process_volume_burst_logic to def _map_tracked_futures_by_name.

pattern = re.compile(r'def process_volume_burst_logic\(.*?def _map_tracked_futures_by_name', re.DOTALL)

replacement = '''def process_future_burst(kite, token, symbol, name, ltp, oi, alerts_list, stats=None):
    if not is_burst_underlying(name):
        return

    ltp = _normalize_burst_price(name, ltp)

    threshold = get_future_burst_threshold(name)
    lot_size = get_future_lot_size(symbol)
    if not lot_size:
        _log_missing_lot_size_once(f"future:{symbol}", symbol)
        return

    clean_symbol = symbol.split(":", 1)[1] if ":" in symbol else symbol
    if direction_engine:
        try:
            direction_engine.process_tick(
                symbol=clean_symbol,
                ltp=ltp,
                volume=oi,
                instrument_data={"instrument_type": "FUT"}
            )
        except Exception as e:
            print(f"Error in IV Engine (Future): {e}")

    now = datetime.now(IST)
    key = f"FUT_{symbol}"
    if key not in option_history:
        option_history[key] = []
    history = option_history[key]
    prev_oi = history[-1]["oi"] if history else 0
    prev_price = history[-1]["price"] if history else 0

    if stats is not None:
        stats["future_quotes"] = stats.get("future_quotes", 0) + 1
        if oi > 0:
            stats["future_oi_quotes"] = stats.get("future_oi_quotes", 0) + 1

    if prev_oi > 0:
        tick_lots = int(abs(oi - prev_oi) / lot_size)
        if stats is not None:
            stats["max_future_tick_lots"] = max(
                stats.get("max_future_tick_lots", 0),
                tick_lots,
            )
        if tick_lots >= threshold and key not in active_watches:
            active_watches[key] = {
                "start_oi": prev_oi,
                "start_price": prev_price,
                "end_time": now + timedelta(seconds=15),
                "symbol": symbol,
                "name": name,
                "lot_size": lot_size,
                "expiry_text": get_future_expiry_text(symbol) if is_mcx_underlying(name) else "",
            }

    if key in active_watches:
        watch = active_watches[key]
        if now >= watch["end_time"]:
            oi_chg = oi - watch["start_oi"]
            p_chg = ltp - watch["start_price"]
            final_lot_size = _normalize_lot_size(watch.get("lot_size")) or lot_size
            final_lots = int(abs(oi_chg) / final_lot_size)
            if final_lots >= threshold:
                strength = get_strength_label(final_lots, watch["name"])
                action = classify_action(watch["symbol"], oi_chg, p_chg)
                p_icon = "▲" if p_chg >= 0 else "▼"
                expiry_line = (
                    f"EXPIRY: {watch['expiry_text']}\\n"
                    if watch.get("expiry_text")
                    else ""
                )
                alert_text = (
                    f"{strength}\\n🚨 {action}\\nSymbol: {watch['symbol']}\\n"
                    f"{expiry_line}"
                    f"━━━━━━━━━━━━━━━\\n"
                    f"LOTS: {final_lots}\\nPRICE: {ltp:.2f} ({p_icon})\\nFUTURE PRICE: {ltp:.2f}\\n"
                    f"━━━━━━━━━━━━━━━\\n"
                    f"EXISTING OI: {watch['start_oi']:,}\\nOI CHANGE  : {oi_chg:+,d}\\nNEW OI     : {oi:,}\\n"
                    f"TIME: {now.strftime('%H:%M:%S')}"
                )
                alert_key = f"FUT:{name}:{watch['symbol']}:{watch['start_oi']}:{watch['start_price']}"
                if not _burst_alert_recent(alert_key):
                    alerts_list.append(alert_text)
            del active_watches[key]

    history.append({"time": now, "oi": oi, "price": ltp})
    if len(history) > 20:
        history.pop(0)


def process_option_logic(kite, name, underlying_data, option_quotes, alerts_list, stats=None):
    if not is_burst_underlying(name):
        return

    opt_df, u_ltp = underlying_data
    if opt_df.empty:
        return

    now = datetime.now(IST)
    u_ltp = _normalize_burst_price(name, u_ltp)
    if DEBUG_BURST_STRIKES:
        try:
            strikes = sorted({float(row["strike"]) for _, row in opt_df.iterrows()})
            print(
                f"[BURST RUNTIME DEBUG] {name} future_ltp={u_ltp:.2f} "
                f"option_rows={len(opt_df)} selected_strikes={strikes[:5]}{'...' if len(strikes) > 5 else ''} "
                f"count={len(strikes)}"
            )
        except Exception as e:
            print(f"[BURST RUNTIME DEBUG] {name} strike debug failed: {e}")

    for _, row in opt_df.iterrows():
        t_str = str(int(row["instrument_token"]))
        if t_str not in option_quotes:
            continue

        lot_size = _get_row_lot_size(row)
        if not lot_size:
            _log_missing_lot_size_once(
                f"option:{t_str}",
                row.get("tradingsymbol", t_str),
            )
            continue
        
        q = option_quotes[t_str]
        curr_oi = q.get("oi", 0)
        volume = q.get("volume", 0)
        ltp = q.get("last_price", 0)
        ltp = float(ltp or 0)
        threshold = get_option_burst_threshold_for_price(name, ltp)
        t_int = int(row["instrument_token"])
        option_type = str(row.get("instrument_type", "") or "").upper()
        if option_type not in {"CE", "PE"}:
            tradingsymbol = str(row.get("tradingsymbol", "") or "").upper()
            if tradingsymbol.endswith("CE"):
                option_type = "CE"
            elif tradingsymbol.endswith("PE"):
                option_type = "PE"

        if direction_engine:
            try:
                direction_engine.process_tick(
                    symbol=row["tradingsymbol"],
                    ltp=ltp,
                    volume=volume,
                    instrument_data={
                        "instrument_type": option_type,
                        "strike": float(row["strike"]),
                        "expiry": row["expiry"],
                        "u_ltp": u_ltp
                    }
                )
            except Exception as e:
                print(f"Error in IV Engine (Option): {e}")

        if stats is not None:
            stats["option_quotes"] = stats.get("option_quotes", 0) + 1
            if curr_oi > 0:
                stats["option_oi_quotes"] = stats.get("option_oi_quotes", 0) + 1

        if t_int not in day_open_oi_store:
            day_open_oi_store[t_int] = curr_oi

        if t_int not in option_history:
            option_history[t_int] = []
        history = option_history[t_int]
        prev_oi = history[-1]["oi"] if history else 0
        prev_price = history[-1]["price"] if history else 0

        if prev_oi > 0:
            tick_lots = int(abs(curr_oi - prev_oi) / lot_size)
            if stats is not None:
                stats["max_option_tick_lots"] = max(
                    stats.get("max_option_tick_lots", 0),
                    tick_lots,
                )
            if tick_lots >= threshold and t_int not in active_watches:
                expiry_text = (
                    row["expiry"].strftime("%d-%m-%Y")
                    if pd.notna(row.get("expiry"))
                    else "NA"
                )
                active_watches[t_int] = {
                    "start_oi": prev_oi,
                    "start_price": prev_price,
                    "end_time": now + timedelta(seconds=15),
                    "symbol": row["tradingsymbol"],
                    "underlying": name,
                    "lot_size": lot_size,
                    "expiry_text": expiry_text,
                }

        if t_int in active_watches:
            watch = active_watches[t_int]
            if now >= watch["end_time"]:
                oi_chg = curr_oi - watch["start_oi"]
                p_chg = ltp - watch["start_price"]
                final_lot_size = _normalize_lot_size(watch.get("lot_size")) or lot_size
                final_lots = int(abs(oi_chg) / final_lot_size)
                final_threshold = get_option_burst_threshold_for_price(watch["underlying"], ltp)
                if final_lots >= final_threshold:
                    strength = get_strength_label(final_lots, watch["underlying"])
                    action = classify_action(watch["symbol"], oi_chg, p_chg)
                    p_icon = "▲" if p_chg >= 0 else "▼"
                    alert_text = (
                        f"{strength}\\n🚨 {action}\\nSymbol: {watch['symbol']}\\n"
                        f"EXPIRY: {watch.get('expiry_text', 'NA')}\\n"
                        f"━━━━━━━━━━━━━━━\\n"
                        f"LOTS: {final_lots}\\nPRICE: {ltp:.2f} ({p_icon})\\nFUTURE PRICE: {u_ltp:.2f}\\n"
                        f"━━━━━━━━━━━━━━━\\n"
                        f"EXISTING OI: {watch['start_oi']:,}\\nOI CHANGE  : {oi_chg:+,d}\\nNEW OI     : {curr_oi:,}\\n"
                        f"TIME: {now.strftime('%H:%M:%S')}"
                    )
                    alert_key = f"OPT:{name}:{t_int}:{watch['start_oi']}:{watch['start_price']}"
                    if not _burst_alert_recent(alert_key):
                        alerts_list.append(alert_text)
                del active_watches[t_int]

        history.append({"time": now, "oi": curr_oi, "price": ltp})
        if len(history) > 20:
            history.pop(0)


def _map_tracked_futures_by_name'''

new_content, count = pattern.subn(replacement, content)
if count > 0:
    with open('heatmap_engine.py', 'w', encoding='utf-8') as f:
        f.write(new_content)
    print("Replaced successfully")
else:
    print("Pattern not found!")
