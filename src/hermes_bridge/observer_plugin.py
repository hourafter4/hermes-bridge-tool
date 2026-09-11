"""Hermes filesystem plugin: private turn observations on the existing API listener.

This file is copied verbatim as ``plugins/hermes-bridge/__init__.py``. It has
no dependency on the locally installed bridge package. Hook turn IDs are not
Runs API IDs. Missing finalization events never establish completion.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hmac
import os
from pathlib import Path
import sqlite3
import time

MAX_TURNS = 2000
MAX_IDENTIFIER = 1024
PLUGIN_VERSION = "0.1.0"


def _identifier(value):
    if (not isinstance(value, str) or not value or len(value) > MAX_IDENTIFIER
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        return None
    return value


def _home():
    # Resolve for every callback/request: Hermes scopes this helper per profile.
    from hermes_constants import get_hermes_home
    return Path(get_hermes_home())


class TurnStore:
    """Small cross-process store containing identifiers and outcomes only."""

    def __init__(self, home=None):
        self.home = Path(home) if home is not None else None

    @property
    def directory(self):
        return (self.home if self.home is not None else _home()) / "plugin-data" / "hermes-bridge-observer"

    @contextmanager
    def _connect(self):
        directory = self.directory
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink():
            raise OSError("Observer directory must not be a symbolic link")
        directory.chmod(0o700)
        path = directory / "turns.sqlite3"
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        connection = sqlite3.connect(path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("""CREATE TABLE IF NOT EXISTS turns (
                turn_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                initial_session_id TEXT NOT NULL, task_id TEXT, platform TEXT,
                started_at REAL, finished_at REAL, status TEXT NOT NULL,
                observed_at REAL NOT NULL)""")
            yield connection
            connection.commit()
        finally:
            connection.close()

    def record(self, session_id, turn_id, *, status, task_id=None, platform=None):
        session_id, turn_id = _identifier(session_id), _identifier(turn_id)
        if session_id is None or turn_id is None:
            return
        task_id, platform = _identifier(task_id), _identifier(platform)
        now = time.time()
        terminal = status != "started"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""INSERT INTO turns (
                turn_id, session_id, initial_session_id, task_id, platform,
                started_at, finished_at, status, observed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(turn_id) DO UPDATE SET
                    session_id=CASE WHEN excluded.finished_at IS NOT NULL
                        THEN excluded.session_id ELSE turns.session_id END,
                    initial_session_id=CASE WHEN turns.started_at IS NULL AND excluded.started_at IS NOT NULL
                        THEN excluded.initial_session_id ELSE turns.initial_session_id END,
                    task_id=COALESCE(turns.task_id, excluded.task_id),
                    platform=COALESCE(turns.platform, excluded.platform),
                    started_at=COALESCE(turns.started_at, excluded.started_at),
                    finished_at=COALESCE(turns.finished_at, excluded.finished_at),
                    status=CASE WHEN turns.finished_at IS NOT NULL
                        THEN turns.status ELSE excluded.status END,
                    observed_at=MAX(turns.observed_at, excluded.observed_at)
                """, (turn_id, session_id, session_id, task_id, platform,
                       None if terminal else now, now if terminal else None, status, now))
            # Retention is deliberately bounded, including unfinished observations.
            # A pruned/missing turn returns unknown, never an inferred completion.
            connection.execute("""DELETE FROM turns WHERE turn_id IN (
                SELECT turn_id FROM turns ORDER BY observed_at DESC, turn_id DESC
                LIMIT -1 OFFSET ?)""", (MAX_TURNS,))

    @staticmethod
    def _project(row):
        payload = {key: row[key] for key in (
            "session_id", "initial_session_id", "turn_id", "task_id", "platform", "started_at", "finished_at", "status")
            if row[key] is not None}
        payload["terminal"] = row["finished_at"] is not None
        payload["liveness"] = "unknown"
        return payload

    def list(self, session_id, limit=10):
        with self._connect() as connection:
            rows = connection.execute("""SELECT * FROM turns
                WHERE session_id=? OR initial_session_id=?
                ORDER BY COALESCE(started_at, finished_at) DESC, turn_id DESC LIMIT ?""",
                (session_id, session_id, limit)).fetchall()
        return {"object": "list", "data": [self._project(row) for row in rows]}

    def get(self, session_id, turn_id):
        with self._connect() as connection:
            row = connection.execute("""SELECT * FROM turns WHERE turn_id=?
                AND (session_id=? OR initial_session_id=?)""",
                (turn_id, session_id, session_id)).fetchone()
        if row is not None:
            return self._project(row)
        return {"session_id": session_id, "turn_id": turn_id,
                "status": "unknown", "terminal": False, "liveness": "unknown"}


def _auth_status(request, adapter):
    """Use the gateway's configured, request-profile-scoped key, never an env fallback."""
    resolver = getattr(adapter, "_expected_api_key", None)
    if not callable(resolver):
        return 403
    try:
        expected = resolver()
    except Exception:
        return 403
    if not isinstance(expected, str) or not expected:
        return 403
    header = request.headers.get("Authorization", "")
    if not isinstance(header, str) or not header.startswith("Bearer "):
        return 401
    supplied = header[7:]
    return None if hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")) else 401


def _wire_routes(native, adapter, store):
    from aiohttp import web

    # Current Hermes passes the app directly. A mapping wrapper is accepted only
    # when it explicitly contains an aiohttp-style app; never inspect globals.
    app = native if hasattr(native, "router") else native.get("app") if isinstance(native, dict) else None
    if app is None or not hasattr(app, "router"):
        raise RuntimeError("Hermes API plugin route registration is unavailable")

    async def handle(request):
        denied = _auth_status(request, adapter)
        if denied:
            return web.json_response({"error": "Observer authorization failed"}, status=denied)
        session_id = _identifier(request.query.get("session_id"))
        turn_id = request.match_info.get("turn_id")
        if session_id is None or (turn_id is not None and _identifier(turn_id) is None):
            return web.json_response({"error": "Valid session_id and turn_id are required"}, status=400)
        try:
            limit = int(request.query.get("limit", "10"))
            if not 1 <= limit <= 100:
                raise ValueError
        except (TypeError, ValueError):
            return web.json_response({"error": "limit must be an integer from 1 to 100"}, status=400)
        try:
            if turn_id is None:
                payload = await asyncio.to_thread(store.list, session_id, limit)
            else:
                payload = await asyncio.to_thread(store.get, session_id, turn_id)
            return web.json_response(payload)
        except (OSError, sqlite3.Error, RuntimeError):
            # Database exceptions may contain filesystem paths; do not reflect them.
            return web.json_response({"error": "Observer state is unavailable"}, status=503)

    app.router.add_get("/hermes-bridge/v1/turns", handle)
    app.router.add_get("/hermes-bridge/v1/turns/{turn_id}", handle)


def register(ctx):
    """Public Hermes native plugin entry point; registration performs no IO."""
    if not callable(getattr(ctx, "register_platform_handler", None)):
        raise RuntimeError("Hermes must support native API plugin handlers")
    store = TurnStore()

    def on_start(session_id=None, turn_id=None, task_id=None, platform=None, **kwargs):
        store.record(session_id, turn_id, status="started", task_id=task_id, platform=platform)

    def on_end(session_id=None, turn_id=None, task_id=None, platform=None,
               completed=None, failed=None, interrupted=None, **kwargs):
        # Reduced CLI exit hooks are not correlated terminal events. Older hooks
        # missing explicit outcomes must remain unknown as well.
        if not all(isinstance(value, bool) for value in (completed, failed, interrupted)):
            return
        status = ("interrupted" if interrupted else "failed" if failed
                  else "completed" if completed else "incomplete")
        store.record(session_id, turn_id, status=status, task_id=task_id, platform=platform)

    ctx.register_hook("pre_llm_call", on_start)
    ctx.register_hook("on_session_end", on_end)
    ctx.register_platform_handler("api_server", lambda native, adapter: _wire_routes(native, adapter, store))
