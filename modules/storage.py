# modules/storage.py
# Thin read/write wrapper for bot_memory/ (Pi-owned runtime state).
# All files are JSON. Paths are relative to the project root.

import json
import logging
import os
import pathlib
import threading
import uuid
from copy import deepcopy

logger = logging.getLogger(__name__)

BOT_MEMORY_DIR = pathlib.Path(__file__).resolve().parent.parent / "bot_memory"
_storage_lock = threading.RLock()


def _path(filename: str) -> pathlib.Path:
    return BOT_MEMORY_DIR / filename


def load(filename: str, default: dict) -> dict:
    """Load a JSON file from bot_memory/. Returns default if missing or corrupt."""
    p = _path(filename)
    with _storage_lock:
        try:
            with p.open("r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            logger.warning("storage: %s not found, using defaults.", filename)
            return deepcopy(default)
        except json.JSONDecodeError as e:
            logger.error("storage: %s is corrupt (%s), using defaults.", filename, e)
            return deepcopy(default)


def save_json_path(path: pathlib.Path, data: dict, *, ensure_ascii: bool = True) -> None:
    """Atomically replace a JSON file and raise if durable persistence fails."""
    with _storage_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.parent / f"{path.name}.{uuid.uuid4().hex}.tmp"
        try:
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=ensure_ascii)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            finally:
                logger.exception("storage: Failed to write %s", path)
                raise


def save(filename: str, data: dict) -> None:
    """Atomically write bot_memory/filename, raising on persistence failure."""
    save_json_path(_path(filename), data)
