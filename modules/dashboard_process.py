"""Portable systemd process controller with exact, bounded commands."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from dotenv import dotenv_values

from utils.redaction import redact_text

REPO_DIR = Path(__file__).resolve().parent.parent
OUTPUT_LIMIT = 6000


class ProcessController:
    def __init__(self, deploy_path: Path | None = None):
        values = dotenv_values(deploy_path or REPO_DIR / ".env.deploy")
        self.bot_service = str(values.get("SERVICE_NAME") or "marco_van_botten")
        self.dashboard_service = str(values.get("DASHBOARD_SERVICE_NAME") or "marco_van_botten_dashboard")
        self.update_service = str(values.get("UPDATE_SERVICE_NAME") or "marco_van_botten_update")
        self.supported = os.name != "nt" and shutil.which("systemctl") is not None and shutil.which("sudo") is not None

    async def _run(self, *args: str, timeout: int = 30) -> dict:
        if not self.supported:
            return {"ok": False, "supported": False, "message": "No supported systemd process controller is configured."}
        try:
            process = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
            )
            output, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
            clean = redact_text(output.decode("utf-8", errors="replace"))[-OUTPUT_LIMIT:]
            return {"ok": process.returncode == 0, "supported": True, "exit_code": process.returncode, "output": clean}
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            return {"ok": False, "supported": True, "message": "Process-control command timed out."}

    async def status(self) -> dict:
        if not self.supported:
            return {"supported": False, "bot": "unsupported", "dashboard": "running"}
        result = {}
        for label, service in (("bot", self.bot_service), ("dashboard", self.dashboard_service)):
            check = await self._run("sudo", "systemctl", "is-active", service)
            result[label] = "active" if check["ok"] else "inactive"
        return {"supported": True, **result}

    async def restart_bot(self) -> dict:
        return await self._run("sudo", "systemctl", "restart", self.bot_service)

    async def start_update(self) -> dict:
        return await self._run("sudo", "systemctl", "start", "--no-block", self.update_service, timeout=20)
