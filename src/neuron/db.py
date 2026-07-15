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

__all__ = [
    "connect", "connect_local", "RemoteTursoConnection",
    "REMOTE_TURSO", "ENGINE_NAME", "VECTOR_SQL_SUPPORTED",
    "TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN",
]


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
            "  (or use the Neuron Control Center -> Turso -> Connect).",
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


def _with_retry(fn, *, attempts: int = 4, base_delay: float = 0.4,
                on_retry=None):
    """Run ``fn`` with exponential backoff on transient remote failures (P5).

    Only ever wraps atomic units — client creation and a single ``batch()`` (which
    is all-or-nothing) — so a retry can never double-apply a partially-written
    save. Re-raises the last error if every attempt fails.

    ``on_retry`` (T76): called between attempts — used to RECREATE a dead client
    before retrying. Without it, a dropped WebSocket/HTTP session made every
    retry fail on the same corpse: the connection object never healed, so after
    an idle disconnect nothing was ever written again.
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
            if on_retry is not None:
                try:
                    on_retry()
                except Exception:
                    pass  # reconnect itself failing → next attempt raises anyway
    raise last  # pragma: no cover (loop always returns or raises above)


def _url_candidates(url: str) -> list[str]:
    """Connection URLs to try, in order (T76).

    WebSocket schemes (``libsql://``/``wss://``/``ws://``) keep a long-lived
    socket that some endpoints/proxies silently drop after idle; the
    ``https://`` (Hrana-over-HTTP) form is stateless per request. Try the
    user's URL first, then fall back to its HTTP twin.
    """
    out = [url]
    for prefix in ("libsql://", "wss://", "ws://"):
        if url.startswith(prefix):
            out.append("https://" + url[len(prefix):])
            break
    return out


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
        self._auth_token = auth_token
        self._urls = _url_candidates(url)
        self._url_idx = 0
        self._client = self._create_client()
        self._tx: "list | None" = None   # buffered Statements while a tx is open

    # -- connection lifecycle (T76) ------------------------------------------
    def _create_client(self):
        """Create the libsql client, falling back across URL transports.

        A ``libsql://`` (WebSocket) endpoint that rejects/drops the sync client
        is retried on its ``https://`` twin; whichever works becomes the
        preferred transport for the rest of this connection's life.
        """
        last: Exception | None = None
        for i in range(self._url_idx, len(self._urls)):
            try:
                client = _with_retry(
                    lambda u=self._urls[i]: libsql_client.create_client_sync(
                        url=u, auth_token=self._auth_token),
                    attempts=2)
                self._url_idx = i
                return client
            except Exception as e:
                last = e
        raise last  # every transport failed

    def _reconnect(self) -> None:
        """Drop the (presumed dead) client and build a fresh one.

        Called between retry attempts: after an idle disconnect the old client
        object never recovers, so retrying on it is pointless — this is what
        used to make the store silently stop persisting turns.
        """
        try:
            self._client.close()
        except Exception:
            pass
        self._client = self._create_client()

    def ping(self) -> bool:
        """Cheap health check (``SELECT 1``) with one reconnect attempt."""
        for _ in range(2):
            try:
                self._client.execute("SELECT 1")
                return True
            except Exception:
                try:
                    self._reconnect()
                except Exception:
                    return False
        return False

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
            _with_retry(lambda: self._client.batch(stmts),
                        on_retry=self._reconnect)

    # -- statement execution ------------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] = ()) -> _RemoteCursor:
        if self._tx is not None and _is_write_sql(sql):
            self._tx.append(libsql_client.Statement(sql, list(params) if params else None))
            return _RemoteCursor(self, buffered=True)
        return _with_retry(lambda: _RemoteCursor(self).execute(sql, params),
                           on_retry=self._reconnect)

    def executemany(self, sql: str, seq_of_params: Sequence[Sequence[Any]]) -> _RemoteCursor:
        stmts = [libsql_client.Statement(sql, list(p)) for p in seq_of_params]
        if self._tx is not None:
            self._tx.extend(stmts)          # join the open transaction
            return _RemoteCursor(self, buffered=True)
        if stmts:
            _with_retry(lambda: self._client.batch(stmts),   # own atomic batch
                        on_retry=self._reconnect)
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
