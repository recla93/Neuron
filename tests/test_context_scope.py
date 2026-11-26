"""A scoped call (Gray Matter's `_scope`) sees and switches its own context only.

2026-11-26: one session switched the shared pointer file and another session's
facts went to the wrong graph. A scope must never touch the file or the global."""
from neuron.registry import GraphRegistry


def _pointer(tmp_path) -> str:
    p = tmp_path / "_active_context.txt"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def test_scope_changes_active_for_the_call_only(tmp_path):
    r = GraphRegistry(str(tmp_path))
    r.switch("backend")
    tok = r.scope("graymatter")
    try:
        assert r.active == "graymatter"
        r.get().facts  # the scoped graph is the one served
    finally:
        r.unscope(tok)
    assert r.active == "backend"
    assert _pointer(tmp_path) == "backend"


def test_switch_inside_a_scope_never_writes_the_pointer(tmp_path):
    r = GraphRegistry(str(tmp_path))
    r.switch("backend")
    tok = r.scope("graymatter")
    try:
        assert r.switch("qwen") == "qwen"
        assert r.active == "qwen"
    finally:
        r.unscope(tok)
    assert r.active == "backend"
    assert _pointer(tmp_path) == "backend"


def test_server_pops_scope_and_restores(tmp_path, monkeypatch):
    import asyncio
    from neuron import server as S
    monkeypatch.setattr(S, "_g", GraphRegistry(str(tmp_path)))
    S._g.switch("backend")
    seen = {}

    async def handler(arguments, ctx, g):
        seen["active"], seen["args"] = S._g.active, dict(arguments)
        return []
    monkeypatch.setitem(S._HANDLERS, "probe_scope", handler)
    asyncio.run(S._call_tool_impl("probe_scope", {"_scope": "graymatter", "x": 1}))
    assert seen == {"active": "graymatter", "args": {"x": 1}}
    assert S._g.active == "backend"


def test_unscoped_switch_still_persists(tmp_path):
    r = GraphRegistry(str(tmp_path))
    r.switch("llm")
    assert _pointer(tmp_path) == "llm"
    assert GraphRegistry(str(tmp_path)).active == "llm"


def test_a_project_session_never_auto_switches_on_domain(tmp_path, monkeypatch):
    """Two turns signalling `frontend` used to move the context; inside a project
    session the second turn's facts would have gone to the `frontend` graph."""
    import asyncio
    import pytest
    pytest.importorskip("mcp")
    from neuron import server as S
    monkeypatch.setattr(S, "_g", GraphRegistry(str(tmp_path)))
    monkeypatch.setattr(S, "TURSO_ENGINE", False)
    monkeypatch.setattr(S, "AUTO_SWITCH", True)        # even when switched on
    S._g.switch("default")
    for i in range(3):
        asyncio.run(S.call_tool("store_turn", {
            "_scope": "graymatter", "domain": "frontend", "keywords": ["navbar"],
            "facts": [{"kind": "decision", "text": f"navbar decision {i}", "concepts": ["navbar"]}]}))
    assert len(S._g.get("graymatter").facts) == 3
    assert not S._g.get("frontend").facts
    assert S._g.active == "default"
