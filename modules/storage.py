# modules/storage.py
# Thin read/write wrapper for bot_memory/ (Pi-owned runtime state).
# All files are JSON. Paths are relative to the project root.

import json
import logging
import pathlib

logger = logging.getLogger(__name__)

BOT_MEMORY_DIR = pathlib.Path(__file__).resolve().parent.parent / "bot_memory"


def _path(filename: str) -> pathlib.Path:
    return BOT_MEMORY_DIR / filename


def load(filename: str, default: dict) -> dict:
    """Load a JSON file from bot_memory/. Returns default if missing or corrupt."""
    p = _path(filename)
    try:
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning(f"storage: {filename} not found, using defaults.")
        return default
    except json.JSONDecodeError as e:
        logger.error(f"storage: {filename} is corrupt ({e}), using defaults.")
        return default


def save(filename: str, data: dict) -> None:
    """Write data as JSON to bot_memory/filename. Creates the directory if needed."""
    p = _path(filename)
    try:
        BOT_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError as e:
        logger.error(f"storage: Failed to write {filename}: {e}")
