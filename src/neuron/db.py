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

**Caution:** ``TURSO_DATABASE_URL`` / ``TURSO_AUTH_TOKEN`` are read from
``os.environ`` **at import time** (module level). Setting them after ``db`` is
imported has no effect. Set them before importing any ``neuron`` module, or use
``neuron._env.load_dotenv_once()`` which runs first from ``neuron/__init__.py``.
"""

from __future__ import annotations

import os
import sqlite3 as _sqlite3
import time as _time
from typing import Any, Sequence

from neuron._env import sanitize_credential


TURSO_DATABASE_URL = sanitize_credential(os.environ.get("TURSO_DATABASE_URL", ""))
TURSO_AUTH_TOKEN = sanitize_credential(os.environ.get("TURSO_AUTH_TOKEN", ""))
REMOTE_TURSO = bool(TURSO_DATABASE_URL and TURSO_AUTH_TOKEN)

try:
    import turso as _local_turso
    LOCAL_TURSO_ENGINE = True
except ImportError:
    _local_turso = None
    LOCAL_TURSO_ENGINE = False

if REMOTE_TURSO:
    try:
        import libsql_client
    except ImportError:
        # Cloud creds are set but the 'cloud' extra (libsql-client) isn't
        # installed. Don't crash the whole server on import — degrade to the
        # local engine and tell the user how to enable cloud. This is exactly
        # the case that killed the bridge on a fresh install.
        import sys as _sys
        print(
            "neuron: TURSO_DATABASE_URL/TOKEN are set but the 'cloud' extra is "
            "not installed, so the cloud tier is unavailable. Falling back to the "
            "local engine. To enable cloud, install libsql-client:\n"
            "        pip install \"neuron[cloud]\"\n"
            "  (or use Configuration.bat -> Bridge & Cloud Turso -> Connect).",
            file=_sys.stderr,
        )
        libsql_client = None  # type: ignore[assignment]
        REMOTE_TURSO = False
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

# Statements that MODIFY the store. Used to decide, inside an open transaction,
# whether to buffer a statement (writes) or run it immediately (reads must return
# their rows right away — e.g. reconcile's "which rows exist" SELECT).
_WRITE_PREFIXES = ("insert", "update", "delete", "replace", "create", "alter", "drop")


def _is_write_sql(sql: str) -> bool:
    head = sql.lstrip()
    if not head:
        return False
    return head.split(None, 1)[0].lower() in _WRITE_PREFIXES


def _with_retry(fn, *, attempts: int = 4, base_delay: float = 0.4):
    """Run ``fn`` with exponential backoff on transient remote failures (P5).

    Only ever wraps atomic units — client creation and a single ``batch()`` (which
    is all-or-nothing) — so a retry can never double-apply a partially-written
    save. Re-raises the last error if every attempt fails.
    """
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:  # transient network / server errors
            last = e
            if i == attempts - 1:
                raise
            _time.sleep(base_delay * (2 ** i))
    raise last  # pragma: no cover (loop always returns or raises above)


class _RemoteCursor:
    """Thin sqlite3-cursor-like wrapper around a libsql_client ResultSet.

    ``buffered=True`` marks a write that was appended to an open transaction's
    buffer rather than executed — it has no rows, so fetch* return empty.
    """

    def __init__(self, conn: "RemoteTursoConnection", buffered: bool = False) -> None:
        self._conn = conn
        self._result: Any = None
        self._buffered = buffered

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
    """sqlite3-compatible facade over a remote Turso (libSQL) cloud database.

    Transactions (P2): ``begin()`` opens a buffer; subsequent WRITE statements are
    collected instead of executed, while reads still run immediately (so mid-save
    SELECTs see committed state). ``commit()`` flushes the whole buffer as ONE
    ``batch()`` — a single all-or-nothing transaction, so a concurrent reader never
    observes a half-applied save. Without an open transaction, behaviour is the
    per-statement autocommit as before.
    """

    def __init__(self, url: str, auth_token: str) -> None:
        self._client = _with_retry(
            lambda: libsql_client.create_client_sync(url=url, auth_token=auth_token))
        self._tx: "list | None" = None   # buffered Statements while a tx is open

    # -- transaction control ------------------------------------------------
    def begin(self) -> None:
        self._tx = []

    def rollback(self) -> None:
        self._tx = None   # nothing was sent yet; just drop the buffer

    def commit(self) -> None:
        if self._tx is None:
            return        # no open tx (autocommit path) — nothing to flush
        stmts, self._tx = self._tx, None
        if stmts:
            _with_retry(lambda: self._client.batch(stmts))

    # -- statement execution ------------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] = ()) -> _RemoteCursor:
        if self._tx is not None and _is_write_sql(sql):
            self._tx.append(libsql_client.Statement(sql, list(params) if params else None))
            return _RemoteCursor(self, buffered=True)
        return _RemoteCursor(self).execute(sql, params)

    def executemany(self, sql: str, seq_of_params: Sequence[Sequence[Any]]) -> _RemoteCursor:
        stmts = [libsql_client.Statement(sql, list(p)) for p in seq_of_params]
        if self._tx is not None:
            self._tx.extend(stmts)          # join the open transaction
            return _RemoteCursor(self, buffered=True)
        if stmts:
            _with_retry(lambda: self._client.batch(stmts))   # own atomic batch
        return _RemoteCursor(self)

    def executescript(self, script: str) -> None:
        stmts = [s.strip() for s in script.split(";") if s.strip()]
        for s in stmts:
            self.execute(s)

    def close(self) -> None:
        self._client.close()


def _ensure_parent_dir(path: str) -> None:
    """Make sure the file's parent directory exists before we open it.

    turso.connect() raises ``IoError: open: NotFound`` when the directory of the
    target file does not exist yet (unlike sqlite3.connect, which still needs the
    dir but fails with a different message). This bit brand-new contexts: the
    first save of a never-before-written context wrote graph_<ctx>.db into a dir
    that hadn't been created, so store_turn/auto crashed. Creating the parent dir
    here fixes it for BOTH engines. Skips special paths like ':memory:'.
    """
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass


def connect(path: str):
    """Open a connection to the main graph store, preferring real Turso cloud."""
    if REMOTE_TURSO:
        return RemoteTursoConnection(TURSO_DATABASE_URL, TURSO_AUTH_TOKEN)
    _ensure_parent_dir(path)
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
    _ensure_parent_dir(path)
    if LOCAL_TURSO_ENGINE:
        return _local_turso.connect(path)
    return _sqlite3.connect(path)
