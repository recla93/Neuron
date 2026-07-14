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
    sys.modules["turso"] = None  # force sqlite3 / Python-fallback tier

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
    typ.Tool                  = type("Tool", (), {"__init__": lambda s, **kw: None})
    typ.TextContent           = type("TC", (), {"__init__": lambda s, **kw: s.__dict__.update(kw)})
    typ.ServerCapabilities    = type("SC", (), {})
    typ.ToolsCapability       = type("TsCap", (), {})
    typ.Resource              = type("Resource", (), {"__init__": lambda s, **kw: s.__dict__.update(kw)})
    hlp.ReadResourceContents  = type("ReadResourceContents", (), {"__init__": lambda s, **kw: s.__dict__.update(kw)})
