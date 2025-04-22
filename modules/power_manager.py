# modules/power_manager.py

import os, platform, atexit
from modules.verbose_logger import log_info

def disable_sleep():
    os_name = platform.system()
    if os_name == "Windows":
        os.system("powercfg -change -standby-timeout-ac 0")
        log_info("🔌 [Windows] Sleep disabled (AC)")
    elif os_name == "Linux":
        os.system("systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target")
        log_info("🔌 [Linux] Sleep services masked")
    else:
        log_info(f"🔌 No sleep management for OS {os_name}")

def restore_sleep():
    os_name = platform.system()
    if os_name == "Windows":
        os.system("powercfg -change -standby-timeout-ac 15")
        log_info("🔌 [Windows] Sleep restored to 15m")
    elif os_name == "Linux":
        os.system("systemctl unmask sleep.target suspend.target hibernate.target hybrid-sleep.target")
        log_info("🔌 [Linux] Sleep services unmasked")
    else:
        log_info(f"🔌 No restore needed for OS {os_name}")

def setup_power_management():
    disable_sleep()
    atexit.register(restore_sleep)
