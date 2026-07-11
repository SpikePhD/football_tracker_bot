"""Password hashing, administrator persistence, sessions, and login throttling."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from modules.storage import BOT_MEMORY_DIR, save_text_path

USERS_PATH = BOT_MEMORY_DIR / "dashboard_users.json"
USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1


class AuthenticationError(ValueError):
    pass


def _hash_password(password: str, salt: bytes | None = None) -> dict:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return {
        "algorithm": "scrypt",
        "salt": base64.b64encode(salt).decode(),
        "hash": base64.b64encode(digest).decode(),
        "n": SCRYPT_N,
        "r": SCRYPT_R,
        "p": SCRYPT_P,
    }


def _verify_password(password: str, record: dict) -> bool:
    try:
        salt = base64.b64decode(record["salt"])
        expected = base64.b64decode(record["hash"])
        actual = hashlib.scrypt(
            password.encode(), salt=salt, n=int(record["n"]), r=int(record["r"]), p=int(record["p"])
        )
        return hmac.compare_digest(actual, expected)
    except (KeyError, TypeError, ValueError):
        return False


class UserStore:
    def __init__(self, path: Path = USERS_PATH):
        self.path = path
        self._lock = threading.RLock()
        self._ensure_bootstrap()

    def _read(self) -> dict:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": 1, "users": []}
        if not isinstance(value, dict) or not isinstance(value.get("users"), list):
            raise AuthenticationError("Dashboard user store is invalid.")
        return value

    def _write(self, value: dict) -> None:
        save_text_path(self.path, json.dumps(value, indent=2) + "\n", mode=0o600)

    def _ensure_bootstrap(self) -> None:
        with self._lock:
            data = self._read()
            if data["users"]:
                return
            data["users"].append({
                "username": "admin",
                "password": _hash_password("admin"),
                "active": True,
                "bootstrap_password": True,
                "created_at": int(time.time()),
            })
            self._write(data)

    @staticmethod
    def _public(user: dict) -> dict:
        return {
            "username": user["username"],
            "active": bool(user.get("active", True)),
            "bootstrap_password": bool(user.get("bootstrap_password", False)),
            "created_at": user.get("created_at"),
        }

    def list_users(self) -> list[dict]:
        with self._lock:
            return [self._public(user) for user in self._read()["users"]]

    def authenticate(self, username: str, password: str) -> dict | None:
        key = str(username or "").lower()
        with self._lock:
            for user in self._read()["users"]:
                if user["username"].lower() == key and user.get("active", True):
                    return self._public(user) if _verify_password(password, user["password"]) else None
        # Keep missing-user timing closer to normal verification.
        _verify_password(password or "", _hash_password("invalid"))
        return None

    def add_user(self, username: str, password: str) -> dict:
        username = str(username or "").strip()
        if not USERNAME_RE.fullmatch(username):
            raise AuthenticationError("Username must be 3-32 letters, numbers, dots, underscores, or hyphens.")
        if not isinstance(password, str) or len(password) < 10:
            raise AuthenticationError("Password must contain at least 10 characters.")
        with self._lock:
            data = self._read()
            if any(user["username"].lower() == username.lower() for user in data["users"]):
                raise AuthenticationError("Username already exists.")
            user = {
                "username": username,
                "password": _hash_password(password),
                "active": True,
                "bootstrap_password": False,
                "created_at": int(time.time()),
            }
            data["users"].append(user)
            self._write(data)
            return self._public(user)

    def change_password(self, username: str, password: str) -> dict:
        if not isinstance(password, str) or len(password) < 10:
            raise AuthenticationError("Password must contain at least 10 characters.")
        with self._lock:
            data = self._read()
            for user in data["users"]:
                if user["username"].lower() == username.lower():
                    user["password"] = _hash_password(password)
                    user["bootstrap_password"] = False
                    self._write(data)
                    return self._public(user)
        raise AuthenticationError("Administrator not found.")

    def set_active(self, username: str, active: bool) -> dict:
        with self._lock:
            data = self._read()
            target = next((u for u in data["users"] if u["username"].lower() == username.lower()), None)
            if target is None:
                raise AuthenticationError("Administrator not found.")
            active_count = sum(bool(u.get("active", True)) for u in data["users"])
            if not active and target.get("active", True) and active_count <= 1:
                raise AuthenticationError("The last active administrator cannot be disabled.")
            target["active"] = bool(active)
            self._write(data)
            return self._public(target)

    def delete_user(self, username: str) -> None:
        with self._lock:
            data = self._read()
            target = next((u for u in data["users"] if u["username"].lower() == username.lower()), None)
            if target is None:
                raise AuthenticationError("Administrator not found.")
            if target.get("active", True) and sum(bool(u.get("active", True)) for u in data["users"]) <= 1:
                raise AuthenticationError("The last active administrator cannot be deleted.")
            data["users"].remove(target)
            self._write(data)


@dataclass
class Session:
    username: str
    csrf: str
    created: float
    last_seen: float


class SessionStore:
    IDLE_SECONDS = 12 * 60 * 60
    ABSOLUTE_SECONDS = 7 * 24 * 60 * 60

    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, username: str, now: float | None = None) -> tuple[str, Session]:
        now = now or time.time()
        token = secrets.token_urlsafe(32)
        session = Session(username=username, csrf=secrets.token_urlsafe(24), created=now, last_seen=now)
        with self._lock:
            self._sessions[token] = session
        return token, session

    def get(self, token: str | None, now: float | None = None) -> Session | None:
        if not token:
            return None
        now = now or time.time()
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return None
            if now - session.last_seen > self.IDLE_SECONDS or now - session.created > self.ABSOLUTE_SECONDS:
                self._sessions.pop(token, None)
                return None
            session.last_seen = now
            return session

    def delete(self, token: str | None) -> None:
        if token:
            with self._lock:
                self._sessions.pop(token, None)

    def delete_user(self, username: str) -> None:
        with self._lock:
            self._sessions = {k: v for k, v in self._sessions.items() if v.username.lower() != username.lower()}


class LoginLimiter:
    def __init__(self):
        self.failures: dict[str, list[float]] = {}
        self.locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _keys(username: str, ip: str) -> tuple[str, str]:
        return f"user:{username.lower()}", f"ip:{ip}"

    def retry_after(self, username: str, ip: str, now: float | None = None) -> int:
        now = now or time.time()
        with self._lock:
            until = max((self.locked_until.get(k, 0) for k in self._keys(username, ip)), default=0)
        return max(0, int(until - now + 0.999))

    def fail(self, username: str, ip: str, now: float | None = None) -> None:
        now = now or time.time()
        with self._lock:
            for key in self._keys(username, ip):
                recent = [stamp for stamp in self.failures.get(key, []) if now - stamp <= 300]
                recent.append(now)
                self.failures[key] = recent
                if len(recent) >= 5:
                    self.locked_until[key] = now + 900

    def success(self, username: str, ip: str) -> None:
        with self._lock:
            for key in self._keys(username, ip):
                self.failures.pop(key, None)
                self.locked_until.pop(key, None)
