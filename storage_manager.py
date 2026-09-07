import os
import sys
import json
import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

MAX_DAYS = {
    "BANKNIFTY": 7,
    "CRUDEOILM": 3
}

def parse_date_key(dt_str):
    """Parse 'DD-MM-YYYY' string into a comparable datetime.date."""
    try:
        parts = dt_str.replace(".json", "").split("-")
        return datetime.date(int(parts[2]), int(parts[1]), int(parts[0]))
    except Exception:
        return datetime.date.min

def prune_storage(symbol):
    """
    Enforces the rolling storage limit:
    - BANKNIFTY: Keeps exactly the latest 7 trading days.
    - CRUDEOILM: Keeps exactly the latest 3 trading days.
    Any older files are permanently deleted to keep storage clean and capped.
    """
    sym_dir = DATA_DIR / symbol
    if not sym_dir.exists():
        return []
        
    limit = MAX_DAYS.get(symbol, 7)
    json_files = list(sym_dir.glob("*.json"))
    
    # Sort files chronologically by date in filename
    sorted_files = sorted(json_files, key=lambda f: parse_date_key(f.name))
    
    deleted_files = []
    if len(sorted_files) > limit:
        files_to_remove = sorted_files[:len(sorted_files) - limit]
        for f in files_to_remove:
            try:
                f.unlink()
                deleted_files.append(f.name)
                print(f"[StorageManager] FIFO Wipe: Deleted {f.name} from {symbol} storage (Capped at {limit} days).")
            except Exception as e:
                print(f"[StorageManager] Error deleting {f.name}: {e}")
                
    return deleted_files

def load_symbol_data(symbol):
    """Load all active rolling days for the given symbol."""
    sym_dir = DATA_DIR / symbol
    if not sym_dir.exists():
        return {}
        
    prune_storage(symbol)
    json_files = list(sym_dir.glob("*.json"))
    sorted_files = sorted(json_files, key=lambda f: parse_date_key(f.name))
    
    result = {}
    for f in sorted_files:
        date_key = f.stem
        try:
            with open(f, "r", encoding="utf-8") as f_in:
                result[date_key] = json.load(f_in)
        except Exception as e:
            print(f"[StorageManager] Error loading {f}: {e}")
            
    return result

def load_all_data():
    """Load all symbols with their respective rolling windows."""
    return {
        "BANKNIFTY": load_symbol_data("BANKNIFTY"),
        "CRUDEOILM": load_symbol_data("CRUDEOILM")
    }

def save_candle(symbol, date_str, candle_obj, strikes=None):
    """
    Append or update a 1-minute candle into today's rolling file.
    If a new day file is created, automatically triggers FIFO pruning.
    """
    sym_dir = DATA_DIR / symbol
    sym_dir.mkdir(parents=True, exist_ok=True)
    
    day_file = sym_dir / f"{date_str}.json"
    is_new_day = not day_file.exists()
    
    if day_file.exists():
        try:
            with open(day_file, "r", encoding="utf-8") as f:
                day_data = json.load(f)
        except Exception:
            day_data = {"timeline": [], "strikes": strikes or [], "base_oi": {}}
    else:
        day_data = {"timeline": [], "strikes": strikes or [], "base_oi": {}}
        
    if strikes:
        day_data["strikes"] = strikes
        
    # Check if candle already exists for this minute, update or append
    timeline = day_data.get("timeline", [])
    tm = candle_obj.get("time")
    
    replaced = False
    for i, pt in enumerate(timeline):
        if pt.get("time") == tm:
            timeline[i] = candle_obj
            replaced = True
            break
            
    if not replaced:
        timeline.append(candle_obj)
        
    day_data["timeline"] = timeline
    
    with open(day_file, "w", encoding="utf-8") as f:
        json.dump(day_data, f)
        
    if is_new_day:
        prune_storage(symbol)

if __name__ == "__main__":
    print("Testing Storage Manager...")
    all_data = load_all_data()
    for sym, days in all_data.items():
        print(f"Symbol: {sym} -> {len(days)} rolling days: {list(days.keys())}")
