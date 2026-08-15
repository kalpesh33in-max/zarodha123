import re

with open('heatmap_engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

# We need to replace the final_threshold check in process_option_logic

pattern = re.compile(r'final_threshold = get_option_burst_threshold_for_price\(watch\["underlying"\], ltp\)\s+if final_lots >= final_threshold:\s+strength = get_strength_label\(final_lots, watch\["underlying"\]\)\s+action = classify_action\(watch\["symbol"\], oi_chg, p_chg\)', re.MULTILINE)

replacement = '''action = classify_action(watch["symbol"], oi_chg, p_chg)
                if "WRITER" in action:
                    final_threshold = 100
                else:
                    final_threshold = 500
                    
                if final_lots >= final_threshold:
                    strength = get_strength_label(final_lots, watch["underlying"])'''

new_content, count = pattern.subn(replacement, content)
if count > 0:
    with open('heatmap_engine.py', 'w', encoding='utf-8') as f:
        f.write(new_content)
    print(f"Replaced {count} times in option logic.")
else:
    print("Pattern not found in option logic!")

