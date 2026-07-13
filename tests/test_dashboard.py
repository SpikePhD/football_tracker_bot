import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("API_KEY", "test-api-key")


class FakeController:
    supported = False

    async def status(self):
        return {"supported": False, "bot": "unsupported", "dashboard": "running"}

    async def restart_bot(self):
        return {"ok": False, "supported": False, "message": "unsupported"}

    async def start_update(self):
        return {"ok": False, "supported": False, "message": "unsupported"}


class DashboardAuthTests(unittest.TestCase):
    def test_bootstrap_scrypt_password_and_multi_admin_guards(self):
        from modules.dashboard_auth import AuthenticationError, UserStore

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "users.json"
            store = UserStore(path)
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn('"password": "admin"', raw)
            self.assertIn('"algorithm": "scrypt"', raw)
            self.assertTrue(store.authenticate("ADMIN", "admin")["bootstrap_password"])
            with self.assertRaises(AuthenticationError):
                store.set_active("admin", False)
            store.add_user("second.admin", "long-password")
            store.set_active("admin", False)
            self.assertFalse(store.list_users()[0]["active"])

    def test_session_idle_and_absolute_expiry(self):
        from modules.dashboard_auth import SessionStore

        store = SessionStore()
        token, _ = store.create("admin", now=100)
        self.assertIsNotNone(store.get(token, now=101))
        self.assertIsNone(store.get(token, now=100 + SessionStore.ABSOLUTE_SECONDS + 1))

    def test_rate_limiter_locks_user_and_ip(self):
        from modules.dashboard_auth import LoginLimiter

        limiter = LoginLimiter()
        for _ in range(5):
            limiter.fail("admin", "127.0.0.1", now=100)
        self.assertGreater(limiter.retry_after("admin", "127.0.0.1", now=101), 800)


class DashboardConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.base = json.loads(Path("config.example.json").read_text(encoding="utf-8-sig"))

    def test_complete_draft_uses_minimal_overrides_and_revision_conflicts(self):
        from modules.configuration import ConfigurationError, configuration_revision, save_complete_config

        with tempfile.TemporaryDirectory() as tmp:
            default = Path(tmp) / "config.json"
            local = Path(tmp) / "config.local.json"
            default.write_text(json.dumps(self.base), encoding="utf-8")
            draft = deepcopy(self.base)
            draft["bot"]["name"] = "Dashboard Bot"
            with patch.dict("os.environ", {"CHANNEL_ID": ""}):
                result = save_complete_config(
                    draft,
                    expected_revision=configuration_revision(self.base),
                    default_path=default,
                    local_path=local,
                )
            self.assertEqual(json.loads(local.read_text())["bot"], {"name": "Dashboard Bot"})
            self.assertEqual(result["overrides"], {"bot": {"name": "Dashboard Bot"}})
            with patch.dict("os.environ", {"CHANNEL_ID": ""}):
                with self.assertRaisesRegex(ConfigurationError, "changed since"):
                    save_complete_config(self.base, expected_revision="stale", default_path=default, local_path=local)

    def test_dashboard_transport_preserves_large_discord_ids(self):
        from modules.dashboard_service import _dashboard_safe_config, _normalize_dashboard_config

        value = deepcopy(self.base)
        value["discord"]["channel_id"] = 123456789012345678
        value["administration"]["owner_users"] = [{"id": 987654321098765432, "label": "Owner"}]
        safe = _dashboard_safe_config(value)
        self.assertEqual(safe["discord"]["channel_id"], "123456789012345678")
        self.assertEqual(safe["administration"]["owner_users"][0]["id"], "987654321098765432")
        self.assertEqual(_normalize_dashboard_config(safe), value)

    def test_dashboard_accepts_numeric_owner_shorthand(self):
        from modules.dashboard_service import _normalize_dashboard_config

        value = deepcopy(self.base)
        value["administration"]["owner_users"] = [212898043475787776]
        normalized = _normalize_dashboard_config(value)
        self.assertEqual(normalized["administration"]["owner_users"], [{
            "id": 212898043475787776,
            "label": "Discord Owner 1",
        }])


class DashboardHealthPublicationTests(unittest.TestCase):
    def test_scheduler_health_snapshot_includes_runtime_mode(self):
        from modules import scheduler

        with patch.object(scheduler, "get_mode", return_value="normal"), \
             patch.object(scheduler.api_provider, "get_status", return_value={"active_provider": "espn"}), \
             patch.object(scheduler.api_provider, "get_tennis_status", return_value={"requests": {"total": 4}}), \
             patch("cogs.version.get_version_info", return_value={"sha": "abc123"}), \
             patch("modules.dashboard_health.write_bot_health") as write:
            scheduler.write_dashboard_health_snapshot()

        self.assertEqual(write.call_args.kwargs["mode"], "normal")
        self.assertEqual(write.call_args.kwargs["commit"]["sha"], "abc123")
        self.assertEqual(write.call_args.kwargs["tennis_provider"]["requests"]["total"], 4)


class DashboardApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from modules.dashboard_audit import AuditLog
        from modules.dashboard_auth import UserStore
        from modules.dashboard_service import create_dashboard_app

        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        app = create_dashboard_app(
            user_store=UserStore(root / "users.json"),
            audit=AuditLog(root / "audit.jsonl"),
            controller=FakeController(),
        )
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def _login(self):
        response = await self.client.post("/api/login", json={"username": "admin", "password": "admin"})
        self.assertEqual(response.status, 200)
        return await response.json()

    async def test_unauthenticated_and_csrf_protection(self):
        self.assertEqual((await self.client.get("/api/admins")).status, 401)
        login = await self._login()
        self.assertEqual((await self.client.post("/api/admins", json={})).status, 403)
        response = await self.client.post(
            "/api/admins",
            json={"username": "operator.one", "password": "long-password"},
            headers={"X-CSRF-Token": login["csrf"]},
        )
        self.assertEqual(response.status, 201)

    async def test_default_warning_and_cookie_flags(self):
        response = await self.client.post("/api/login", json={"username": "admin", "password": "admin"})
        cookie_header = response.headers["Set-Cookie"]
        response = await self.client.get("/api/session")
        data = await response.json()
        self.assertTrue(data["default_password"])
        self.assertIn("HttpOnly", cookie_header)
        self.assertIn("SameSite=Strict", cookie_header)


if __name__ == "__main__":
    unittest.main()
