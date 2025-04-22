# modules/verbose_logger.py

from datetime import datetime

def _ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def log_info(msg):    print(f"[INFO  {_ts()}] {msg}")
def log_warning(msg): print(f"[WARN  {_ts()}] ⚠️ {msg}")
def log_error(msg):   print(f"[ERROR {_ts()}] ❌ {msg}")
def log_success(msg): print(f"[OK    {_ts()}] ✅ {msg}")
def log_debug(msg):   print(f"[DEBUG {_ts()}] 🐛 {msg}")
