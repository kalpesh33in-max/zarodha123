import json

files = [
    r"C:\Users\kalpe\Downloads\logs.1781875529930.json",
    r"C:\Users\kalpe\Downloads\logs.1781850858753.json"
]

keywords = ["matrix", "token", "mcx", "crudeoil", "error", "exception", "failed"]

for fpath in files:
    print(f"\n==================== SEARCHING IN {fpath} ====================")
    try:
        with open(fpath, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        matches = []
        for entry in data:
            msg = entry.get("message", "")
            msg_lower = msg.lower()
            if any(kw in msg_lower for kw in keywords):
                matches.append((entry.get("timestamp"), entry.get("severity", "INFO"), msg))
        
        print(f"Found {len(matches)} matching entries.")
        # Print some matches, especially errors or matrix related
        matrix_errors = [m for m in matches if "matrix" in m[2].lower() or "token" in m[2].lower() or "error" in m[2].lower() or "failed" in m[2].lower()]
        mcx_matches = [m for m in matches if "mcx" in m[2].lower() or "crude" in m[2].lower()]
        
        print(f"\n--- Matrix/Token/Error/Failure Matches (showing up to 50): ---")
        for ts, sev, msg in matrix_errors[:50]:
            print(f"[{ts}] [{sev}] {msg}")
            
        print(f"\n--- MCX Matches (showing up to 50): ---")
        for ts, sev, msg in mcx_matches[:50]:
            print(f"[{ts}] [{sev}] {msg}")
            
    except Exception as e:
        print(f"Error processing {fpath}: {e}")
