"""Shared lightweight mocks for the heavy runtime deps (fastembed / mcp / turso).

Two test modules — test_core.py and test_fivefix.py — import `neuron` at module
level and want it to load WITHOUT the real embedding model or MCP stack. The
injection has to happen before that import, so it can't be a fixture: call
`install_mock_deps()` at the very top of the module, then import neuron.

Other test files use `pytest.importorskip("fastembed"/"mcp")` because they need
the REAL deps — they must NOT call this.
"""
from __future__ import annotations

import contextlib
import sys
import types


class FakeEmbed:
    """384-dim constant embedder (non-unit vector, norm ~1.96)."""
    def __init__(self, *a, **kw): pass
    def embed(self, texts):
        texts = list(texts) if not isinstance(texts, list) else texts
        for _ in texts:
            yield [0.1] * 384


class FakeSrv:
    def __init__(self, *a, **kw): pass
    def list_tools(self): return lambda f: f
    def call_tool(self):  return lambda f: f
    def list_resources(self): return lambda f: f
    def read_resource(self):  return lambda f: f


@contextlib.asynccontextmanager
async def _fake_stdio(*a, **kw):
    yield None, None


def install_mock_deps() -> None:
    """Inject fake fastembed/mcp modules and force the sqlite3 DB tier.

    Idempotent-enough for a test session: overwrites sys.modules entries.
    """
    sys.modules["turso"] = None  # force sqlite3 / Python-fallback tier for THIS import

    _fe = types.ModuleType("fastembed")
    _fe.TextEmbedding = FakeEmbed
    sys.modules["fastembed"] = _fe

    def _mod(name):
        m = types.ModuleType(name)
        sys.modules[name] = m
        return m

    _mod("mcp")
    srv = _mod("mcp.server")
    low = _mod("mcp.server.lowlevel")
    hlp = _mod("mcp.server.lowlevel.helper_types")
    mdl = _mod("mcp.server.models")
    std = _mod("mcp.server.stdio")
    typ = _mod("mcp.types")

    srv.Server                = FakeSrv
    low.NotificationOptions   = type("NotificationOptions", (), {})
    mdl.InitializationOptions = type("IO", (), {})
    std.stdio_server          = _fake_stdio
    typ.Tool                  = type("Tool", (), {"__init__": lambda s, **kw: s.__dict__.update(kw)})
    typ.TextContent           = type("TC", (), {"__init__": lambda s, **kw: s.__dict__.update(kw)})
    typ.ServerCapabilities    = type("SC", (), {})
    typ.ToolsCapability       = type("TsCap", (), {})
    typ.Resource              = type("Resource", (), {"__init__": lambda s, **kw: s.__dict__.update(kw)})
    hlp.ReadResourceContents  = type("ReadResourceContents", (), {"__init__": lambda s, **kw: s.__dict__.update(kw)})


def unpoison_turso() -> None:
    """Undo the ``sys.modules["turso"] = None`` sentinel from install_mock_deps().

    Call this right after the ``neuron`` imports that needed the fake-missing
    ``turso`` are done. ``sys.modules[name] = None`` is a process-global CPython
    import-cache entry, not a per-test-file thing: it makes every subsequent
    ``import turso`` anywhere in this interpreter raise ImportError immediately,
    for the rest of the process. In a single combined test run across neuron +
    gray_matter + neurag (one pytest process, one collection phase), pytest
    IMPORTS every test file up front before running any fixture — so this file
    poisons ``turso`` during collection, and neurag's db.py (imported when
    neurag's own test files are collected right after) computes its
    module-level TURSO_AVAILABLE=False from that poisoned entry and never
    recovers, since it's only computed once at import time. neurag's conftest.py
    tries to purge the sentinel, but as an autouse fixture it only runs at
    first TEST EXECUTION — after collection (and the poisoning) already
    happened. Popping here, right after the imports that need it, keeps the
    fake-missing window scoped to just this module's own import instead of
    leaking into every test file collected afterward in the same process.
    """
    sys.modules.pop("turso", None)
