import re

with open('heatmap_engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix expiry_line in process_future_burst
pattern1 = r'''expiry_line = \(
\s*f"EXPIRY: \{watch\['expiry_text'\]\}
"
\s*if watch.get\("expiry_text"\)
\s*else ""
\s*\)'''
repl1 = '''expiry_line = (
                    f"EXPIRY: {watch['expiry_text']}\\n"
                    if watch.get("expiry_text")
                    else ""
                )'''

# Fix alert_text in process_future_burst
pattern2 = r'''alert_text = \(
\s*f"\{strength\}
🚨 \{action\}
Symbol: \{watch\['symbol'\]\}
"
\s*f"\{expiry_line\}"
\s*f"━━━━━━━━━━━━━━━
"
\s*f"LOTS: \{final_lots\}
PRICE: \{ltp:\.2f\} \(\{p_icon\}\)
FUTURE PRICE: \{ltp:\.2f\}
"
\s*f"━━━━━━━━━━━━━━━
"
\s*f"EXISTING OI: \{watch\['start_oi'\]:,\}
OI CHANGE  : \{oi_chg:\+,d\}
NEW OI     : \{oi:,\}
"
\s*f"TIME: \{now\.strftime\('%H:%M:%S'\)\}"
\s*\)'''
repl2 = '''alert_text = (
                    f"{strength}\\n🚨 {action}\\nSymbol: {watch['symbol']}\\n"
                    f"{expiry_line}"
                    f"━━━━━━━━━━━━━━━\\n"
                    f"LOTS: {final_lots}\\nPRICE: {ltp:.2f} ({p_icon})\\nFUTURE PRICE: {ltp:.2f}\\n"
                    f"━━━━━━━━━━━━━━━\\n"
                    f"EXISTING OI: {watch['start_oi']:,}\\nOI CHANGE  : {oi_chg:+,d}\\nNEW OI     : {oi:,}\\n"
                    f"TIME: {now.strftime('%H:%M:%S')}"
                )'''

# Fix alert_text in process_option_logic
pattern3 = r'''alert_text = \(
\s*f"\{strength\}
🚨 \{action\}
Symbol: \{watch\['symbol'\]\}
"
\s*f"EXPIRY: \{watch\.get\('expiry_text', 'NA'\)\}
"
\s*f"━━━━━━━━━━━━━━━
"
\s*f"LOTS: \{final_lots\}
PRICE: \{ltp:\.2f\} \(\{p_icon\}\)
FUTURE PRICE: \{u_ltp:\.2f\}
"
\s*f"━━━━━━━━━━━━━━━
"
\s*f"EXISTING OI: \{watch\['start_oi'\]:,\}
OI CHANGE  : \{oi_chg:\+,d\}
NEW OI     : \{curr_oi:,\}
"
\s*f"TIME: \{now\.strftime\('%H:%M:%S'\)\}"
\s*\)'''
repl3 = '''alert_text = (
                        f"{strength}\\n🚨 {action}\\nSymbol: {watch['symbol']}\\n"
                        f"EXPIRY: {watch.get('expiry_text', 'NA')}\\n"
                        f"━━━━━━━━━━━━━━━\\n"
                        f"LOTS: {final_lots}\\nPRICE: {ltp:.2f} ({p_icon})\\nFUTURE PRICE: {u_ltp:.2f}\\n"
                        f"━━━━━━━━━━━━━━━\\n"
                        f"EXISTING OI: {watch['start_oi']:,}\\nOI CHANGE  : {oi_chg:+,d}\\nNEW OI     : {curr_oi:,}\\n"
                        f"TIME: {now.strftime('%H:%M:%S')}"
                    )'''

content = re.sub(pattern1, repl1, content, flags=re.MULTILINE)
content = re.sub(pattern2, repl2, content, flags=re.MULTILINE)
content = re.sub(pattern3, repl3, content, flags=re.MULTILINE)

with open('heatmap_engine.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("Fixed syntax errors!")
