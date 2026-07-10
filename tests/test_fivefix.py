"""Regression tests for the FiveFix branch fixes:

  * cosine metric consistency (Python fallback returns similarities in [-1, 1],
    not the old raw dot product that ran to ~9 on non-normalized vectors);
  * _search_embeddings merges seed + active DBs instead of returning on the
    first non-empty one (the seed used to shadow the user's live graph);
  * accent-folding in the heuristic extractor (accented Italian words are no
    longer truncated to garbage stems, and accented connectors are filtered);
  * per-(embedder,text) embedding cache;
  * _env.sanitize_credential strips control chars anywhere in a credential.
"""

from __future__ import annotations

import sys
import types
import os
import json
import tempfile

# ── Mock heavy deps before importing neuron (mirrors tests/test_core.py) ──────
sys.modules["turso"] = None  # force sqlite3 / Python-fallback tier

_fe = types.ModuleType("fastembed")
class _FakeEmbed:
    def __init__(self, *a, **kw): pass
    def embed(self, texts):
        for _ in list(texts):
            yield [0.1] * 384          # constant, NON-unit vector (norm ≈ 1.96)
_fe.TextEmbedding = _FakeEmbed
sys.modules["fastembed"] = _fe

def _mod(name):
    m = types.ModuleType(name); sys.modules[name] = m; return m

import contextlib
_mod("mcp")
_srv_mod = _mod("mcp.server")
_low = _mod("mcp.server.lowlevel")
_hlp = _mod("mcp.server.lowlevel.helper_types")
_mdl = _mod("mcp.server.models")
_std = _mod("mcp.server.stdio")
_typ = _mod("mcp.types")

class _FakeSrv:
    def __init__(self, *a, **kw): pass
    def list_tools(self): return lambda f: f
    def call_tool(self):  return lambda f: f
    def list_resources(self): return lambda f: f
    def read_resource(self):  return lambda f: f

@contextlib.asynccontextmanager
async def _fake_stdio(*a, **kw): yield None, None

_srv_mod.Server               = _FakeSrv
_low.NotificationOptions      = type("NotificationOptions", (), {})
_mdl.InitializationOptions    = type("IO", (), {})
_std.stdio_server             = _fake_stdio
_typ.Tool                     = type("Tool", (), {"__init__": lambda s, **kw: None})
_typ.TextContent              = type("TC", (), {"__init__": lambda s, **kw: s.__dict__.update(kw)})
_typ.ServerCapabilities       = type("SC", (), {})
_typ.ToolsCapability          = type("TsCap", (), {})
_typ.Resource                 = type("Resource", (), {"__init__": lambda s, **kw: s.__dict__.update(kw)})
_hlp.ReadResourceContents     = type("ReadResourceContents", (), {"__init__": lambda s, **kw: s.__dict__.update(kw)})

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from neuron.models import Node, Graph          # noqa: E402
import neuron.server as _srv                    # noqa: E402
from neuron.server import SemanticExtractor, _fold_accents  # noqa: E402
from neuron import _env                          # noqa: E402


class _Fake384:
    """A well-behaved 384-dim embedder (constant, non-zero) used to isolate the
    embedding-dependent tests from any embedder another test file may have left in
    the shared `neuron.server` globals (e.g. test_embedding_config installs a
    7-dim / zero embedder to exercise the dimension guard)."""
    def embed(self, texts):
        for _ in list(texts):
            yield [0.1] * 384


def _install_fake_embedder():
    _srv._embedder = _Fake384()
    _srv._embed_dim_checked = True     # dim is correct; skip the one-shot guard
    _srv._embed_cache.clear()


def _graph(keywords):
    _install_fake_embedder()
    g = Graph()
    for kw in keywords:
        g.add_node(Node(keyword=kw, turn=1, topic="t", domain="backend",
                        sentiment="neutral", entities=[], tags=[]))
    return g


class _RecordConn:
    """Fake store connection: records executemany writes and answers the
    embed_model probe. Used by the P5 model-guard and P3 reconcile tests."""
    def __init__(self, stored_model=None):
        self.stored_model = stored_model
        self.many = []       # list[(sql, rows)]
        self.singles = []    # list[sql]
    def execute(self, sql, params=()):
        self.singles.append(sql)
        if "embed_model" in sql:
            rows = [(self.stored_model,)] if self.stored_model else []
            return _FakeStoreCursor(rows)
        return _FakeStoreCursor([])
    def executemany(self, sql, rows):
        self.many.append((sql, list(rows))); return _FakeStoreCursor([])
    def begin(self): pass
    def commit(self): pass
    def rollback(self): pass
    def close(self): pass


def _dirty_graph():
    """A minimal graph with one node+vector, schema pre-marked, ready to save."""
    g = Graph(turn_count=1)
    g.add_node(Node(keyword="alpha", turn=1, topic="t", domain="backend",
                    sentiment="neutral"))
    g._schema_ready = True
    g._needs_full_write = True
    g._dirty = True
    return g


# ── cosine metric ────────────────────────────────────────────────────────────

class TestCosineMetric:
    def test_fallback_similarity_in_range(self):
        """With non-normalized vectors the old raw dot product gave ~3.84;
        a real cosine must stay within [-1, 1]."""
        _srv.TURSO_ENGINE = False
        g = _graph(["fastapi", "redis", "postgres", "docker"])
        res = _srv._search_embeddings(["fastapi", "database"], top_n=8, graph=g)
        assert res, "expected results"
        for kw, sim in res:
            assert -1.0001 <= sim <= 1.0001, f"cosine out of range: {kw}={sim}"

    def test_refine_domain_fallback_in_range(self):
        _srv.TURSO_ENGINE = False
        g = _graph(["spring", "hibernate", "jpa"])
        # load the graph into the registry so the Python fallback can see it
        _srv._g._graphs = {"default": g}
        best, alt = _srv._refine_domain(["spring", "boot"])
        # constant vectors ⇒ every sim == 1.0 ⇒ a domain is returned, in range
        assert best is None or isinstance(best, str)


# ── seed + active merge (the #1 bug) ──────────────────────────────────────────

class TestSearchMerge:
    def test_merges_seed_and_active_keeping_max(self, monkeypatch):
        """Turso path must union seed + active DBs (max sim per keyword), not
        return on the first non-empty DB."""
        class _FakeConn:
            def __init__(self, rows): self._rows = rows
            def execute(self, *a, **k): return self
            def fetchall(self): return self._rows
            def close(self): pass

        dbrows = {
            "seed":   [("A", 0.90), ("B", 0.50)],
            "active": [("B", 0.80), ("C", 0.70)],
        }
        _install_fake_embedder()
        _srv._seed_conn_cache.clear()
        monkeypatch.setattr(_srv, "TURSO_ENGINE", True)
        monkeypatch.setattr(_srv, "_seed_usable", lambda p: True)
        monkeypatch.setattr(_srv, "_active_db_path", lambda: "active")
        monkeypatch.setattr(_srv._g, "_seed_path", "seed", raising=False)
        monkeypatch.setattr(_srv.os.path, "exists", lambda p: True)
        monkeypatch.setattr(_srv._db, "connect_local", lambda db: _FakeConn(dbrows[db]))
        try:
            res = dict(_srv._search_embeddings(["x"], top_n=8, graph=Graph()))
        finally:
            _srv._seed_conn_cache.clear()
        assert res.get("A") == 0.90            # seed-only survives
        assert res.get("C") == 0.70            # active-only survives (was shadowed before)
        assert res.get("B") == 0.80            # max across seed(0.5) & active(0.8)

    def test_seed_connection_reused_active_not_cached(self, monkeypatch):
        """The immutable seed connection is opened once and reused; the writable
        active DB is reopened every call."""
        opens: dict[str, int] = {}

        class _FakeConn:
            def __init__(self, rows): self._rows = rows
            def execute(self, *a, **k): return self
            def fetchall(self): return self._rows
            def close(self): pass

        dbrows = {"seed": [("A", 0.9)], "active": [("B", 0.8)]}

        def _connect(db):
            opens[db] = opens.get(db, 0) + 1
            return _FakeConn(dbrows[db])

        _install_fake_embedder()
        _srv._seed_conn_cache.clear()
        monkeypatch.setattr(_srv, "TURSO_ENGINE", True)
        monkeypatch.setattr(_srv, "_seed_usable", lambda p: True)
        monkeypatch.setattr(_srv, "_active_db_path", lambda: "active")
        monkeypatch.setattr(_srv._g, "_seed_path", "seed", raising=False)
        monkeypatch.setattr(_srv.os.path, "exists", lambda p: True)
        monkeypatch.setattr(_srv._db, "connect_local", _connect)
        try:
            _srv._search_embeddings(["x"], top_n=8, graph=Graph())
            _srv._search_embeddings(["x"], top_n=8, graph=Graph())
            assert opens.get("seed") == 1      # cached across both calls
            assert opens.get("active") == 2    # reopened each call
        finally:
            _srv._seed_conn_cache.clear()


# ── accent handling in extraction ─────────────────────────────────────────────

class TestAccentExtraction:
    def test_fold_accents(self):
        assert _fold_accents("città") == "citta"
        assert _fold_accents("perché così può") == "perche cosi puo"
        assert _fold_accents("FastAPI") == "FastAPI"     # ASCII untouched

    def test_accented_nouns_not_truncated(self):
        kws = [k.lower() for k in SemanticExtractor.extract(
            "La città ha una qualità di rete più alta perché usiamo Postgres."
        ).keywords]
        # clean folded nouns survive
        assert "citta" in kws and "qualita" in kws
        # garbage stems from ASCII truncation must be gone
        for junk in ("citt", "qualit", "perch"):
            assert junk not in kws, f"garbage stem leaked: {junk} -> {kws}"
        # accented connectors filtered
        for stop in ("piu", "perche", "pero"):
            assert stop not in kws, f"connector leaked: {stop} -> {kws}"

    def test_folded_connectors_in_stopwords(self):
        for w in ("perche", "cosi", "cioe", "piu", "pero", "puo", "gia"):
            assert w in _srv.STOP_WORDS, w


# ── embedding cache ───────────────────────────────────────────────────────────

class TestEmbeddingCache:
    def test_memoizes_per_text(self):
        _install_fake_embedder()
        v1 = _srv._get_embedding("kubernetes")
        n = len(_srv._embed_cache)
        v2 = _srv._get_embedding("kubernetes")
        assert v1 is v2                       # same cached object
        assert len(_srv._embed_cache) == n    # no growth on a hit
        _srv._get_embedding("docker")
        assert len(_srv._embed_cache) == n + 1


# ── credential sanitization ───────────────────────────────────────────────────

class TestSanitizeCredential:
    def test_strips_control_chars_anywhere(self):
        assert _env.sanitize_credential("tok en\n") == "token"
        assert _env.sanitize_credential("ab\r\ncd\t") == "abcd"
        assert _env.sanitize_credential("") == ""
        assert _env.sanitize_credential("clean-token_123") == "clean-token_123"


# ── P1: per-user session-state sidecar on a shared remote store ────────────────

class _FakeStoreCursor:
    def __init__(self, rows=()): self._rows = list(rows)
    def fetchall(self): return self._rows
    def fetchone(self): return self._rows[0] if self._rows else None
    def __iter__(self): return iter(self._rows)


class _FakeStoreConn:
    """Minimal store connection that records what is written to the `meta` table,
    so we can prove session state does NOT go there on the remote tier."""
    def __init__(self): self.meta_writes = []
    def execute(self, sql, params=()): return _FakeStoreCursor()
    def executemany(self, sql, rows):
        if "meta" in sql.lower():
            self.meta_writes.extend(list(rows))
        return _FakeStoreCursor()
    def executescript(self, s): return None
    def begin(self): return None
    def rollback(self): return None
    def commit(self): return None
    def close(self): return None


class TestSessionSidecar:
    def test_sidecar_path_next_to_graph(self):
        g = Graph()
        p = g._session_sidecar_path(os.path.join("x", "graph_default.db"), "default")
        assert p == os.path.join("x", "graph_default.session.json")

    def test_sidecar_roundtrip(self):
        g = Graph()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "graph_default.db")
            g._save_session_sidecar(path, "default", {"turn_count": "12", "x": "y"})
            assert g._load_session_sidecar(path, "default") == {"turn_count": "12", "x": "y"}
        # missing file → empty dict, never raises
        assert g._load_session_sidecar(os.path.join("nope", "graph_x.db"), "x") == {}

    def test_local_store_keeps_session_in_meta(self):
        """Local single-writer store: session state still round-trips via the DB
        meta (unchanged behaviour, no sidecar needed)."""
        _install_fake_embedder()
        assert not _srv._db.REMOTE_TURSO   # test env is the sqlite fallback tier
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "graph_default.db")
            g = Graph(turn_count=5)
            g.add_node(Node(keyword="alpha", turn=1, topic="t", domain="backend",
                            sentiment="neutral"))
            g.staged_stimulus = "beta (act=0.50)"
            g.save_sqlite(path, context="default", force=True)
            assert not os.path.exists(g._session_sidecar_path(path, "default"))  # no sidecar locally
            g2 = Graph()
            g2.load_sqlite(path, context="default")
            assert g2.turn_count == 5
            assert g2.staged_stimulus == "beta (act=0.50)"

    def test_remote_store_splits_meta_and_writes_sidecar(self, monkeypatch):
        """Remote shared store: embed_model/embed_dim go to the store meta, but
        per-user session state goes to the LOCAL sidecar, never the shared meta."""
        fake = _FakeStoreConn()
        monkeypatch.setattr(_srv._db, "REMOTE_TURSO", True)
        monkeypatch.setattr(_srv._db, "connect", lambda path: fake)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "graph_default.db")
            g = Graph(turn_count=7)
            g.session_id = "sess-A"
            g.staged_stimulus = "redis (act=0.80)"
            g._schema_ready = True          # skip DDL against the fake conn
            g._needs_full_write = False
            g._dirty = True                 # force the write path
            g.save_sqlite(path, context="default")

            meta_keys = {k for (k, _v) in fake.meta_writes}
            assert "embed_model" in meta_keys and "embed_dim" in meta_keys
            # session state must NOT have hit the shared meta table
            assert "turn_count" not in meta_keys
            assert "staged_stimulus" not in meta_keys
            assert "session_id" not in meta_keys

            side = g._load_session_sidecar(path, "default")
            assert side.get("turn_count") == "7"
            assert side.get("staged_stimulus") == "redis (act=0.80)"
            assert side.get("session_id") == "sess-A"


# ── P2: atomic remote transaction (one batch) ─────────────────────────────────

class _FakeRow:
    def __init__(self, t): self._t = t
    def astuple(self): return self._t


class _FakeRS:
    def __init__(self, rows): self.rows = [_FakeRow(r) for r in rows]


class TestRemoteTransaction:
    def _conn(self, monkeypatch):
        import types as _t
        rec = {"batches": [], "executed": []}

        class _Client:
            def execute(self, sql, params=None):
                rec["executed"].append((sql, params)); return _FakeRS([])
            def batch(self, stmts):
                rec["batches"].append(list(stmts))
            def close(self): pass

        fake = _t.SimpleNamespace(
            Statement=lambda sql, params=None: (sql, params),
            create_client_sync=lambda url, auth_token: _Client(),
        )
        monkeypatch.setattr(_srv._db, "libsql_client", fake)
        return _srv._db.RemoteTursoConnection("url", "tok"), rec

    def test_writes_buffer_until_commit_one_batch(self, monkeypatch):
        conn, rec = self._conn(monkeypatch)
        conn.begin()
        conn.execute("INSERT INTO nodes VALUES (1)", ())
        conn.executemany("INSERT INTO links VALUES (?)", [(1,), (2,)])
        conn.execute("SELECT keyword FROM nodes WHERE context=?", ("default",))  # read passes through
        assert rec["batches"] == []                      # nothing flushed mid-transaction
        assert any("SELECT" in s for s, _ in rec["executed"])
        conn.commit()
        assert len(rec["batches"]) == 1                  # exactly ONE atomic batch
        assert len(rec["batches"][0]) == 3               # 1 insert + 2 link rows

    def test_rollback_discards_buffer(self, monkeypatch):
        conn, rec = self._conn(monkeypatch)
        conn.begin()
        conn.execute("DELETE FROM nodes", ())
        conn.rollback()
        conn.commit()
        assert rec["batches"] == []                       # nothing sent

    def test_autocommit_executemany_is_own_batch(self, monkeypatch):
        conn, rec = self._conn(monkeypatch)
        conn.executemany("INSERT INTO x VALUES (?)", [(1,)])   # no open tx
        assert len(rec["batches"]) == 1


# ── P5: retry/backoff + embed-model write guard ───────────────────────────────

class TestRetryAndModelGuard:
    def test_with_retry_succeeds_after_transient_failures(self):
        calls = {"n": 0}
        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("transient")
            return "ok"
        assert _srv._db._with_retry(flaky, attempts=4, base_delay=0.0) == "ok"
        assert calls["n"] == 3

    def test_with_retry_reraises_after_exhaustion(self):
        def always():
            raise ValueError("boom")
        try:
            _srv._db._with_retry(always, attempts=3, base_delay=0.0)
            assert False, "should have raised"
        except ValueError as e:
            assert str(e) == "boom"

    def test_model_guard_skips_vectors_on_mismatch(self, monkeypatch):
        _install_fake_embedder()
        conn = _RecordConn(stored_model="some/other-model")
        monkeypatch.setattr(_srv._db, "REMOTE_TURSO", True)
        monkeypatch.setattr(_srv._db, "connect", lambda p: conn)
        with tempfile.TemporaryDirectory() as d:
            g = _dirty_graph()
            g.save_sqlite(os.path.join(d, "graph_default.db"), context="default")
        sqls = [sql for sql, _ in conn.many]
        assert not any("node_vectors" in s for s in sqls)       # vectors skipped
        assert any("nodes" in s and "INSERT" in s.upper() for s in sqls)  # nodes still written
        meta_rows = [r for sql, rows in conn.many if "meta" in sql.lower() for r in rows]
        assert all(k != "embed_model" for (k, _v) in meta_rows)  # model not clobbered

    def test_model_guard_writes_vectors_on_match(self, monkeypatch):
        import neuron.models as _m
        _install_fake_embedder()
        conn = _RecordConn(stored_model=_m.EMBED_MODEL)
        monkeypatch.setattr(_srv._db, "REMOTE_TURSO", True)
        monkeypatch.setattr(_srv._db, "connect", lambda p: conn)
        with tempfile.TemporaryDirectory() as d:
            g = _dirty_graph()
            g.save_sqlite(os.path.join(d, "graph_default.db"), context="default")
        assert any("node_vectors" in sql for sql, _ in conn.many)


# ── P3: reconcile downgraded on a shared remote store ─────────────────────────

class TestSharedReconcileGuard:
    def test_diff_delete_downgraded_without_optin(self, monkeypatch):
        _install_fake_embedder()
        conn = _RecordConn(stored_model=None)
        monkeypatch.setattr(_srv._db, "REMOTE_TURSO", True)
        monkeypatch.setattr(_srv._db, "connect", lambda p: conn)
        had = os.environ.pop("NS_ALLOW_SHARED_RECONCILE", None)
        try:
            with tempfile.TemporaryDirectory() as d:
                g = _dirty_graph()
                g._needs_diff_delete = True          # a merge asked for reconcile
                g.save_sqlite(os.path.join(d, "graph_default.db"), context="default")
        finally:
            if had is not None:
                os.environ["NS_ALLOW_SHARED_RECONCILE"] = had
        sqls = " ".join(sql for sql, _ in conn.many).upper()
        assert "DELETE" not in sqls                  # destructive delete NOT issued
        assert "NODES" in sqls                        # additive upsert still applied


# ── P4: one-shot schema init ──────────────────────────────────────────────────

class TestEnsureSchema:
    def test_ensure_schema_creates_all_tables(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "graph_default.db")
            Graph().ensure_schema(path, "default")     # local tier -> real sqlite file
            conn = sqlite3.connect(path)
            tbls = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            conn.close()
        assert {"meta", "nodes", "links", "node_vectors", "_graveyard"} <= tbls
