"""Unified database connection layer for Neuron.

Three tiers, in order of preference:

1. **Remote Turso (cloud)** — when ``TURSO_DATABASE_URL`` and ``TURSO_AUTH_TOKEN``
   are set, connects to a real Turso cloud database over libsql-client (HTTP).
   This is what makes memory survive across machines/sessions, not just on
   one local file. ``vector_distance_cos()`` runs server-side on Turso itself.
2. **Local pyturso engine** — embedded libSQL-compatible engine, native
   ``vector_distance_cos()``, but persists to a local file only (no cloud
   sync). Used when pyturso is installed but no Turso cloud credentials are
   configured.
3. **Stdlib sqlite3** — last-resort fallback, no native vector search (the
   code falls back to a Python-side cosine similarity loop in that case).

Every call site in the codebase should go through ``connect()`` here instead
of importing sqlite3/turso directly, so the three tiers stay interchangeable.
"""

from __future__ import annotations

import os
import sqlite3 as _sqlite3
from typing import Any, Sequence

TURSO_DATABASE_URL = os.environ.get("TURSO_DATABASE_URL", "").strip()
TURSO_AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "").strip()
REMOTE_TURSO = bool(TURSO_DATABASE_URL and TURSO_AUTH_TOKEN)

try:
    import turso as _local_turso
    LOCAL_TURSO_ENGINE = True
except ImportError:
    _local_turso = None
    LOCAL_TURSO_ENGINE = False

if REMOTE_TURSO:
    import libsql_client
else:
    libsql_client = None  # type: ignore[assignment]

# Native SQL vector_distance_cos() is available whenever we're talking to an
# actual Turso/libSQL engine, local or remote — not with plain sqlite3.
VECTOR_SQL_SUPPORTED = REMOTE_TURSO or LOCAL_TURSO_ENGINE

ENGINE_NAME = "Turso (cloud)" if REMOTE_TURSO else ("Turso (local)" if LOCAL_TURSO_ENGINE else "SQLite")

# Session-level PRAGMAs are meaningless against a remote HTTP database — the
# server manages its own journaling/sync. Introspective PRAGMAs like
# table_info still need to reach the server, so only no-op these specific ones.
_REMOTE_NOOP_PRAGMAS = ("journal_mode", "synchronous", "foreign_keys")


class _RemoteCursor:
    """Thin sqlite3-cursor-like wrapper around a libsql_client ResultSet."""

    def __init__(self, conn: "RemoteTursoConnection") -> None:
        self._conn = conn
        self._result: Any = None

    def _is_noop_pragma(self, sql: str) -> bool:
        s = sql.strip().lower()
        if not s.startswith("pragma"):
            return False
        return any(p in s for p in _REMOTE_NOOP_PRAGMAS) and "table_info" not in s

    def execute(self, sql: str, params: Sequence[Any] = ()) -> "_RemoteCursor":
        if self._is_noop_pragma(sql):
            self._result = None
            return self
        self._result = self._conn._client.execute(sql, list(params) if params else None)
        return self

    def executemany(self, sql: str, seq_of_params: Sequence[Sequence[Any]]) -> "_RemoteCursor":
        stmts = [libsql_client.Statement(sql, list(p)) for p in seq_of_params]
        if stmts:
            self._conn._client.batch(stmts)
        return self

    def fetchall(self) -> list[tuple]:
        if self._result is None:
            return []
        return [tuple(row.astuple()) for row in self._result.rows]

    def fetchone(self) -> tuple | None:
        rows = self.fetchall()
        return rows[0] if rows else None

    def __iter__(self):
        return iter(self.fetchall())


class RemoteTursoConnection:
    """sqlite3-compatible facade over a remote Turso (libSQL) cloud database."""

    def __init__(self, url: str, auth_token: str) -> None:
        self._client = libsql_client.create_client_sync(url=url, auth_token=auth_token)

    def execute(self, sql: str, params: Sequence[Any] = ()) -> _RemoteCursor:
        return _RemoteCursor(self).execute(sql, params)

    def executemany(self, sql: str, seq_of_params: Sequence[Sequence[Any]]) -> _RemoteCursor:
        return _RemoteCursor(self).executemany(sql, seq_of_params)

    def executescript(self, script: str) -> None:
        stmts = [s.strip() for s in script.split(";") if s.strip()]
        for s in stmts:
            self.execute(s)

    def commit(self) -> None:
        pass  # libsql-client commits per statement/batch — nothing to flush

    def close(self) -> None:
        self._client.close()


def connect(path: str):
    """Open a connection to the main graph store, preferring real Turso cloud."""
    if REMOTE_TURSO:
        return RemoteTursoConnection(TURSO_DATABASE_URL, TURSO_AUTH_TOKEN)
    if LOCAL_TURSO_ENGINE:
        return _local_turso.connect(path)
    return _sqlite3.connect(path)


def connect_local(path: str):
    """Connect to a specific local file via the local Turso engine (or sqlite3).

    Use this for code that must address a particular seed/graph file by path
    (e.g. per-context vector search) — those operations are inherently
    file-scoped and stay local even when a remote Turso cloud database is
    configured for the main graph store via ``connect()``.
    """
    if LOCAL_TURSO_ENGINE:
        return _local_turso.connect(path)
    return _sqlite3.connect(path)
