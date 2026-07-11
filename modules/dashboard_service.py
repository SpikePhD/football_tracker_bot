"""Authenticated aiohttp administration dashboard application."""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from pathlib import Path

from aiohttp import web

from modules.configuration import (
    ConfigurationError,
    configuration_snapshot,
    load_effective_config,
    replace_secret,
    save_complete_config,
    validate_config,
)
from modules.dashboard_audit import AuditLog
from modules.dashboard_auth import AuthenticationError, LoginLimiter, SessionStore, UserStore
from modules.dashboard_health import read_bot_health
from modules.dashboard_logs import read_logs
from modules.dashboard_process import ProcessController
from modules.runtime_settings import get_runtime_settings, set_morning_schedule, set_runtime_mode

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "dashboard_static"
SESSION_COOKIE = "mvb_dashboard_session"


def _client_ip(request: web.Request) -> str:
    peer = request.transport.get_extra_info("peername") if request.transport else None
    return str(peer[0]) if peer else "unknown"


def _https(request: web.Request) -> bool:
    return request.secure or request.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip().lower() == "https"


def _changed_paths(before, after, prefix="") -> list[str]:
    if isinstance(before, dict) and isinstance(after, dict):
        paths = []
        for key in sorted(set(before) | set(after)):
            scope = f"{prefix}.{key}" if prefix else key
            if key not in before or key not in after:
                paths.append(scope)
            else:
                paths.extend(_changed_paths(before[key], after[key], scope))
        return paths
    return [] if before == after else [prefix]


def _set_path(target: dict, path: str, value) -> None:
    parts = path.split(".")
    current = target
    for part in parts[:-1]:
        current = current[part]
    current[parts[-1]] = deepcopy(value)


def _dashboard_safe_config(config: dict) -> dict:
    """Represent Discord snowflakes as strings to avoid browser number rounding."""
    value = deepcopy(config)
    channel_id = (value.get("discord") or {}).get("channel_id")
    if channel_id is not None:
        value["discord"]["channel_id"] = str(channel_id)
    for owner in (value.get("administration") or {}).get("owner_users", []):
        if isinstance(owner, dict) and owner.get("id") is not None:
            owner["id"] = str(owner["id"])
    return value


def _normalize_dashboard_config(config: dict) -> dict:
    value = deepcopy(config)
    try:
        value["discord"]["channel_id"] = int(value["discord"]["channel_id"])
        for owner in value["administration"]["owner_users"]:
            owner["id"] = int(owner["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigurationError("Discord channel and owner IDs must be numeric IDs.") from exc
    return value


def _dashboard_snapshot() -> dict:
    snapshot = configuration_snapshot()
    for key in ("config", "defaults", "overrides"):
        snapshot[key] = _dashboard_safe_config(snapshot[key])
    return snapshot


def _json_error(message: str, status: int = 400, **extra) -> web.Response:
    return web.json_response({"ok": False, "error": str(message), **extra}, status=status)


@web.middleware
async def error_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except (ConfigurationError, AuthenticationError, ValueError) as exc:
        return _json_error(str(exc))
    except json.JSONDecodeError:
        return _json_error("Request body must be valid JSON.")
    except Exception:
        logger.exception("Dashboard request failed: method=%s path=%s", request.method, request.path)
        return _json_error("The dashboard could not complete this request.", 500)


@web.middleware
async def security_headers_middleware(request: web.Request, handler):
    response = await handler(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'"
    response.headers["Cache-Control"] = "no-store" if request.path.startswith("/api/") else "no-cache"
    return response


def create_dashboard_app(
    *,
    user_store: UserStore | None = None,
    sessions: SessionStore | None = None,
    limiter: LoginLimiter | None = None,
    audit: AuditLog | None = None,
    controller: ProcessController | None = None,
) -> web.Application:
    users = user_store or UserStore()
    sessions = sessions or SessionStore()
    limiter = limiter or LoginLimiter()
    audit = audit or AuditLog()
    controller = controller or ProcessController()
    app = web.Application(middlewares=[security_headers_middleware, error_middleware], client_max_size=2 * 1024 * 1024)
    routes = web.RouteTableDef()

    def session_for(request: web.Request):
        return sessions.get(request.cookies.get(SESSION_COOKIE))

    def require_session(request: web.Request, *, csrf: bool = False):
        session = session_for(request)
        if session is None:
            raise web.HTTPUnauthorized(text=json.dumps({"ok": False, "error": "Authentication required."}), content_type="application/json")
        if csrf and request.headers.get("X-CSRF-Token") != session.csrf:
            raise web.HTTPForbidden(text=json.dumps({"ok": False, "error": "Invalid CSRF token."}), content_type="application/json")
        return session

    def record(request: web.Request, session, action: str, paths=None, result="success"):
        audit.record(username=session.username, ip=_client_ip(request), action=action, paths=paths, result=result)

    @routes.get("/")
    async def index(_request):
        return web.FileResponse(STATIC_DIR / "index.html")

    @routes.get("/api/session")
    async def get_session(request):
        session = session_for(request)
        if session is None:
            return web.json_response({"authenticated": False, "https": _https(request)})
        current = next((u for u in users.list_users() if u["username"].lower() == session.username.lower()), None)
        if current is None or not current["active"]:
            sessions.delete(request.cookies.get(SESSION_COOKIE))
            return web.json_response({"authenticated": False, "https": _https(request)})
        return web.json_response({
            "authenticated": True,
            "username": session.username,
            "csrf": session.csrf,
            "default_password": current["bootstrap_password"],
            "https": _https(request),
        })

    @routes.post("/api/login")
    async def login(request):
        body = await request.json()
        username = str(body.get("username") or "")
        password = str(body.get("password") or "")
        ip = _client_ip(request)
        retry = limiter.retry_after(username, ip)
        if retry:
            audit.record(username=username or "unknown", ip=ip, action="login", result="rate_limited")
            return _json_error("Too many failed attempts. Try again later.", 429, retry_after=retry)
        user = users.authenticate(username, password)
        if user is None:
            limiter.fail(username, ip)
            audit.record(username=username or "unknown", ip=ip, action="login", result="denied")
            return _json_error("Invalid username or password.", 401)
        limiter.success(username, ip)
        token, session = sessions.create(user["username"])
        response = web.json_response({"ok": True, "username": user["username"], "csrf": session.csrf, "default_password": user["bootstrap_password"]})
        response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="Strict", secure=_https(request), max_age=7 * 24 * 60 * 60, path="/")
        audit.record(username=user["username"], ip=ip, action="login", result="success")
        return response

    @routes.post("/api/logout")
    async def logout(request):
        session = require_session(request, csrf=True)
        record(request, session, "logout")
        sessions.delete(request.cookies.get(SESSION_COOKIE))
        response = web.json_response({"ok": True})
        response.del_cookie(SESSION_COOKIE, path="/")
        return response

    @routes.get("/api/config")
    async def get_config(request):
        require_session(request)
        return web.json_response(_dashboard_snapshot())

    @routes.post("/api/config/validate")
    async def validate_draft(request):
        require_session(request, csrf=True)
        body = await request.json()
        validate_config(_normalize_dashboard_config(body.get("config")))
        return web.json_response({"ok": True})

    @routes.put("/api/config")
    async def save_config(request):
        session = require_session(request, csrf=True)
        body = await request.json()
        before = load_effective_config()
        try:
            result = save_complete_config(_normalize_dashboard_config(body.get("config")), expected_revision=body.get("revision"))
        except ConfigurationError as exc:
            if "changed since" in str(exc):
                record(request, session, "configuration.save", result="revision_conflict")
                return _json_error(str(exc), 409, current=_dashboard_snapshot())
            raise
        paths = _changed_paths(before, result["config"])
        record(request, session, "configuration.save", paths)
        restart = None
        if body.get("restart"):
            restart = await controller.restart_bot()
            record(request, session, "service.restart_bot", result="success" if restart.get("ok") else "failed")
        return web.json_response({"ok": True, "changed_paths": paths, "revision": result["revision"], "restart": restart})

    @routes.post("/api/config/reset")
    async def reset_config(request):
        session = require_session(request, csrf=True)
        body = await request.json()
        snapshot = _dashboard_snapshot()
        if body.get("revision") != snapshot["revision"]:
            return _json_error("Configuration changed since it was loaded.", 409, current=snapshot)
        draft = deepcopy(snapshot["config"])
        paths = body.get("paths") or []
        if not isinstance(paths, list) or any(path not in snapshot["sources"] for path in paths):
            raise ConfigurationError("Reset paths must name known configuration fields.")
        for path in paths:
            value = snapshot["defaults"]
            for part in path.split("."):
                value = value[part]
            _set_path(draft, path, value)
        result = save_complete_config(_normalize_dashboard_config(draft), expected_revision=snapshot["revision"])
        record(request, session, "configuration.reset", paths)
        return web.json_response({"ok": True, "snapshot": _dashboard_snapshot(), "revision": result["revision"]})

    @routes.put("/api/secrets/{name}")
    async def secret_replace(request):
        session = require_session(request, csrf=True)
        body = await request.json()
        name = request.match_info["name"]
        replace_secret(name, body.get("value"))
        record(request, session, "secret.replace", [f"secrets.{name}"])
        from modules.configuration import secret_status
        return web.json_response({"ok": True, "status": secret_status()[name]})

    @routes.get("/api/runtime")
    async def runtime_get(request):
        require_session(request)
        return web.json_response(get_runtime_settings())

    @routes.put("/api/runtime/mode")
    async def runtime_mode(request):
        session = require_session(request, csrf=True)
        body = await request.json()
        result = set_runtime_mode(body.get("mode"))
        record(request, session, "runtime.mode", ["runtime.mode"])
        return web.json_response({"ok": True, **result})

    @routes.put("/api/runtime/morning")
    async def runtime_morning(request):
        session = require_session(request, csrf=True)
        body = await request.json()
        result = set_morning_schedule(enabled=body.get("enabled"), hour=body.get("hour"), minute=body.get("minute"), timezone=body.get("timezone"))
        record(request, session, "runtime.morning", ["runtime.morning"])
        return web.json_response({"ok": True, "morning": result})

    @routes.get("/api/status")
    async def status(request):
        require_session(request)
        return web.json_response({"health": read_bot_health(), "services": await controller.status()})

    @routes.get("/api/logs")
    async def logs(request):
        require_session(request)
        config = load_effective_config()
        result = read_logs(Path(config["log"]["file_path"]), mode=request.query.get("mode", "recent"), module=request.query.get("module"), limit=int(request.query.get("limit", "300")))
        return web.json_response(result)

    @routes.get("/api/audit")
    async def audit_history(request):
        require_session(request)
        return web.json_response({"entries": audit.recent(int(request.query.get("limit", "200")))})

    @routes.get("/api/admins")
    async def admins_get(request):
        require_session(request)
        return web.json_response({"users": users.list_users()})

    @routes.post("/api/admins")
    async def admins_add(request):
        session = require_session(request, csrf=True)
        body = await request.json()
        user = users.add_user(body.get("username"), body.get("password"))
        record(request, session, "administrator.add", [f"dashboard_users.{user['username']}"])
        return web.json_response({"ok": True, "user": user}, status=201)

    @routes.put("/api/admins/{username}/password")
    async def admins_password(request):
        session = require_session(request, csrf=True)
        body = await request.json()
        username = request.match_info["username"]
        if username.lower() == session.username.lower() and body.get("current_password") is not None:
            if users.authenticate(username, str(body.get("current_password"))) is None:
                raise AuthenticationError("Current password is incorrect.")
        user = users.change_password(username, body.get("password"))
        sessions.delete_user(username)
        record(request, session, "administrator.password", [f"dashboard_users.{username}.password"])
        return web.json_response({"ok": True, "user": user, "reauthenticate": username.lower() == session.username.lower()})

    @routes.put("/api/admins/{username}/active")
    async def admins_active(request):
        session = require_session(request, csrf=True)
        body = await request.json()
        username = request.match_info["username"]
        user = users.set_active(username, body.get("active"))
        if not user["active"]:
            sessions.delete_user(username)
        record(request, session, "administrator.active", [f"dashboard_users.{username}.active"])
        return web.json_response({"ok": True, "user": user})

    @routes.delete("/api/admins/{username}")
    async def admins_delete(request):
        session = require_session(request, csrf=True)
        username = request.match_info["username"]
        users.delete_user(username)
        sessions.delete_user(username)
        record(request, session, "administrator.delete", [f"dashboard_users.{username}"])
        return web.json_response({"ok": True})

    @routes.post("/api/operations/restart")
    async def restart(request):
        session = require_session(request, csrf=True)
        result = await controller.restart_bot()
        record(request, session, "service.restart_bot", result="success" if result.get("ok") else "failed")
        return web.json_response(result, status=200 if result.get("ok") else 503)

    @routes.post("/api/operations/update")
    async def update(request):
        session = require_session(request, csrf=True)
        result = await controller.start_update()
        record(request, session, "service.update", result="started" if result.get("ok") else "failed")
        return web.json_response(result, status=202 if result.get("ok") else 503)

    app.add_routes(routes)
    app.router.add_static("/static", STATIC_DIR, append_version=True)
    return app
