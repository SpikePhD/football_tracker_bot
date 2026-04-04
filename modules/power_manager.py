# modules/power_manager.py

import os
import platform
import atexit
import logging

logger = logging.getLogger(__name__)

def disable_sleep():
    os_name = platform.system()
    if os_name == "Windows":
        # Consider adding error checking for os.system calls if permissions might be an issue
        # For example, check the return code. 0 usually means success.
        # ret_code = os.system("powercfg -change -standby-timeout-ac 0")
        # if ret_code != 0:
        #     logger.warning(f"Failed to execute powercfg to disable sleep. Return code: {ret_code}. Admin rights might be needed.")
        # else:
        #     logger.info("🔌 [Windows] Sleep disabled (AC)")
        os.system("powercfg -change -standby-timeout-ac 0")
        logger.info("🔌 [Windows] Sleep disabled (AC)")
    elif os_name == "Linux":
        # ret_code = os.system("systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target")
        # if ret_code != 0:
        #     logger.warning(f"Failed to execute systemctl mask. Return code: {ret_code}. Root/sudo rights might be needed.")
        # else:
        #     logger.info("🔌 [Linux] Sleep services masked")
        os.system("systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target")
        logger.info("🔌 [Linux] Sleep services masked")
    else:
        logger.info(f"🔌 No sleep management for OS {os_name}")

def restore_sleep():
    os_name = platform.system()
    if os_name == "Windows":
        os.system("powercfg -change -standby-timeout-ac 15")
        logger.info("🔌 [Windows] Sleep restored to 15m (AC)")
    elif os_name == "Linux":
        os.system("systemctl unmask sleep.target suspend.target hibernate.target hybrid-sleep.target")
        logger.info("🔌 [Linux] Sleep services unmasked")
    else:
        logger.info(f"🔌 No restore needed for OS {os_name}")

def setup_power_management():
    logger.info("⚙️ Setting up power management...")
    disable_sleep()
    atexit.register(restore_sleep)
    logger.info("👍 Power management setup complete. Sleep restore registered on exit.")