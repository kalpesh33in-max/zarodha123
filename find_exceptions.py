import json
import re

files = [
    r"C:\Users\kalpe\Downloads\logs.1781875529930.json",
    r"C:\Users\kalpe\Downloads\logs.1781850858753.json"
]

print("Scanning for exceptions and traceback lines...")
for fpath in files:
    print(f"\n==================== EXCEPTIONS IN {fpath} ====================")
    try:
        with open(fpath, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        errors = set()
        for entry in data:
            msg = entry.get("message", "")
            severity = entry.get("severity", "").upper()
            if severity in ("ERROR", "WARNING") or "error" in msg.lower() or "exception" in msg.lower() or "traceback" in msg.lower() or "failed" in msg.lower() or "cannot" in msg.lower():
                if "Failed to send Matrix message: 429" not in msg:
                    errors.add((severity, msg))
        
        print(f"Found {len(errors)} unique non-rate-limit error/warning/exception entries:")
        for sev, err in sorted(list(errors))[:100]:
            print(f"[{sev}] {err}")
            
    except Exception as e:
        print(f"Error: {e}")
