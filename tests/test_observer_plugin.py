import multiprocessing
from pathlib import Path
import sqlite3
import stat
import tempfile
import types
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from hermes_bridge import observer_plugin as observer


def _record_process(home, index):
    store = observer.TurnStore(home)
    for sequence in range(5):
        identifier = f"turn-{index}-{sequence}"
        store.record("cli-session", identifier, status="started")
        store.record("cli-session", identifier, status="completed")


class PluginContext:
    def __init__(self):
        self.hooks = {}
        self.factories = {}

    def register_hook(self, name, callback):
        self.hooks[name] = callback

    def register_platform_handler(self, name, factory):
        self.factories[name] = factory


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.store = observer.TurnStore(self.home)
        self.scope = patch.object(observer, "_home", return_value=self.home)
        self.scope.start()
        self.addCleanup(self.scope.stop)
        self.context = PluginContext()
        observer.register(self.context)

    def test_hooks_record_sanitized_correlated_outcomes(self):
        start = self.context.hooks["pre_llm_call"]
        end = self.context.hooks["on_session_end"]
        start(session_id="cli-session", turn_id="opaque:turn/one", task_id="task",
              platform="cli", user_message="PRIVATE PROMPT", conversation_history=["PRIVATE HISTORY"],
              model="PRIVATE MODEL", api_key="PRIVATE KEY")
        result = self.store.get("cli-session", "opaque:turn/one")
        self.assertEqual(result["status"], "started")
        self.assertFalse(result["terminal"])
        self.assertEqual(result["liveness"], "unknown")
        end(session_id="cli-session", turn_id="opaque:turn/one", completed=True,
            failed=False, interrupted=False, assistant_response="PRIVATE ANSWER")
        result = self.store.get("cli-session", "opaque:turn/one")
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["terminal"])
        self.assertEqual(result["task_id"], "task")
        self.assertIn("started_at", result)
        self.assertIn("finished_at", result)
        encoded = (self.store.directory / "turns.sqlite3").read_bytes()
        self.assertNotIn(b"PRIVATE", encoded)
        self.assertEqual(stat.S_IMODE(self.store.directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.store.directory / "turns.sqlite3").stat().st_mode), 0o600)

    def test_reduced_exit_and_missing_outcomes_do_not_finish_turn(self):
        start, end = (self.context.hooks[key] for key in ("pre_llm_call", "on_session_end"))
        start(session_id="session", turn_id="turn")
        end(session_id="session", completed=False, interrupted=True)
        end(session_id="session", turn_id="turn", completed=True)
        self.assertEqual(self.store.get("session", "turn")["status"], "started")
        self.assertEqual(self.store.get("session", "absent"), {
            "session_id": "session", "turn_id": "absent", "status": "unknown",
            "terminal": False, "liveness": "unknown"})

    def test_each_explicit_outcome_and_duplicate_start(self):
        end = self.context.hooks["on_session_end"]
        for status, flags in (("completed", (True, False, False)), ("failed", (False, True, False)),
                              ("interrupted", (False, False, True)), ("incomplete", (False, False, False))):
            with self.subTest(status=status):
                end(session_id="session", turn_id=status, completed=flags[0], failed=flags[1], interrupted=flags[2])
                self.store.record("session", status, status="started")
                result = self.store.get("session", status)
                self.assertEqual(result["status"], status)
                self.assertTrue(result["terminal"])

    def test_compaction_keeps_original_session_lookup(self):
        self.store.record("original", "turn", status="started")
        self.store.record("compacted", "turn", status="completed")
        for session in ("original", "compacted"):
            self.assertEqual(self.store.get(session, "turn")["status"], "completed")
            self.assertEqual(len(self.store.list(session)["data"]), 1)
        self.assertEqual(self.store.get("unrelated", "turn")["status"], "unknown")

    def test_profiles_are_resolved_for_each_observation(self):
        store = observer.TurnStore()
        store.record("session", "default-turn", status="started")
        with patch.object(observer, "_home", return_value=self.home / "another-profile"):
            self.assertEqual(store.list("session")["data"], [])
            store.record("session", "profile-turn", status="started")
        self.assertEqual([row["turn_id"] for row in store.list("session")["data"]], ["default-turn"])

    def test_retention_is_bounded_and_pruned_state_unknown(self):
        with patch.object(observer, "MAX_TURNS", 3):
            for number in range(5):
                self.store.record("session", str(number), status="started")
        self.assertEqual(len(self.store.list("session")["data"]), 3)
        self.assertEqual(self.store.get("session", "0")["status"], "unknown")

    def test_concurrent_processes_preserve_all_turns(self):
        context = multiprocessing.get_context("spawn")
        processes = [context.Process(target=_record_process, args=(str(self.home), index)) for index in range(4)]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=15)
            if process.is_alive():
                process.terminate()
                process.join()
            self.assertEqual(process.exitcode, 0)
        rows = self.store.list("cli-session", 100)["data"]
        self.assertEqual(len(rows), 20)
        self.assertTrue(all(row["status"] == "completed" for row in rows))


class ObserverHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = observer.TurnStore(self.temporary.name)
        self.key = "configured-gateway-key"
        self.adapter = types.SimpleNamespace(_expected_api_key=lambda: self.key)
        app = web.Application()
        observer._wire_routes(app, self.adapter, self.store)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def get(self, path, key="configured-gateway-key"):
        headers = {"Authorization": "Bearer " + key} if key is not None else {}
        return await self.client.get(path, headers=headers)

    async def test_list_specific_and_missing_turn_over_real_http(self):
        self.store.record("cli", "turn:one", status="completed")
        response = await self.get("/hermes-bridge/v1/turns?session_id=cli&limit=1")
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["data"][0]["turn_id"], "turn:one")
        response = await self.get("/hermes-bridge/v1/turns/turn%3Aone?session_id=cli")
        self.assertEqual((await response.json())["status"], "completed")
        response = await self.get("/hermes-bridge/v1/turns/missing?session_id=cli")
        self.assertEqual(response.status, 200)
        self.assertFalse((await response.json())["terminal"])

    async def test_missing_wrong_and_unconfigured_auth_fail_closed(self):
        for supplied in (None, "wrong", "üwrong", "configured-gateway-key "):
            response = await self.get("/hermes-bridge/v1/turns?session_id=cli", supplied)
            self.assertEqual(response.status, 401)
        self.key = ""
        response = await self.get("/hermes-bridge/v1/turns?session_id=cli")
        self.assertEqual(response.status, 403)
        del self.adapter._expected_api_key
        response = await self.get("/hermes-bridge/v1/turns?session_id=cli")
        self.assertEqual(response.status, 403)
        self.assertFalse(self.store.directory.exists())

    async def test_configured_key_resolved_for_every_request(self):
        self.key = "profile-specific-key"
        with patch.dict("os.environ", {"API_SERVER_KEY": "configured-gateway-key"}):
            response = await self.get("/hermes-bridge/v1/turns?session_id=cli")
            self.assertEqual(response.status, 401)
            response = await self.get("/hermes-bridge/v1/turns?session_id=cli", self.key)
            self.assertEqual(response.status, 200)

    async def test_bad_parameters_and_store_failure_do_not_leak(self):
        for query in ("", "?session_id=cli&limit=0", "?session_id=cli&limit=abc", "?session_id=cli&limit=101"):
            response = await self.get("/hermes-bridge/v1/turns" + query)
            self.assertEqual(response.status, 400)
        with patch.object(self.store, "list", side_effect=sqlite3.OperationalError("PRIVATE FILE AND KEY")):
            response = await self.get("/hermes-bridge/v1/turns?session_id=cli")
            self.assertEqual(response.status, 503)
            self.assertNotIn("PRIVATE", await response.text())


if __name__ == "__main__":
    unittest.main()
