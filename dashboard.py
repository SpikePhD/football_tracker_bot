"""Marco Van Botten configuration dashboard entry point."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from aiohttp import web
from dotenv import dotenv_values

from modules.dashboard_service import create_dashboard_app


def deployment_settings(path: Path = Path(".env.deploy")) -> tuple[str, int]:
    values = dotenv_values(path) if path.exists() else {}
    host = str(values.get("DASHBOARD_HOST") or os.getenv("DASHBOARD_HOST") or "0.0.0.0")
    try:
        port = int(values.get("DASHBOARD_PORT") or os.getenv("DASHBOARD_PORT") or 8765)
    except ValueError as exc:
        raise RuntimeError("DASHBOARD_PORT must be a number.") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("DASHBOARD_PORT must be between 1 and 65535.")
    return host, port


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)-8s] [%(name)s] %(message)s")
    host, port = deployment_settings()
    web.run_app(create_dashboard_app(), host=host, port=port, access_log=logging.getLogger("dashboard.access"))
