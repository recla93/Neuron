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


def test_context_names_are_checked_by_words():
    from neuron.registry import similar_contexts
    have = ["spring", "graymatter", "studio/bash", "llm", "qwen"]
    assert similar_contexts("springboot", have) == ["spring"]
    assert similar_contexts("Spring Boot", have) == ["spring"]
    assert similar_contexts("gray-matter", have) == ["graymatter"]
    assert similar_contexts("bash", have) == ["studio/bash"]
    assert similar_contexts("reboot", have) == [] and similar_contexts("react", have) == []


def test_an_unknown_context_goes_to_the_inbox_unless_created(tmp_path, monkeypatch):
    import asyncio
    import pytest
    pytest.importorskip("mcp")
    from neuron import server as S
    monkeypatch.setattr(S, "_g", GraphRegistry(str(tmp_path)))
    monkeypatch.setattr(S, "TURSO_ENGINE", False)
    S._g.get("spring").add_fact("fact", "Constructor injection is preferred.", ["injection"], 1)
    S._g.save("spring")
    call = lambda a: asyncio.run(S.call_tool("store_turn", a))[0].text   # noqa: E731
    fact = lambda t: [{"kind": "fact", "text": t, "concepts": ["bean"]}]  # noqa: E731
    out = call({"context": "springboot", "facts": fact("Beans are singletons by default.")})
    assert "saved in 'inbox'" in out and "'spring'" in out, out
    assert [f["text"] for f in S._g.get("inbox").facts.values()] == ["Beans are singletons by default."]
    out = call({"context": "Spring", "facts": fact("@Lazy delays a bean.")})
    assert "saved in 'spring'" in out and len(S._g.get("spring").facts) == 2
    out = call({"context": "django", "new_context": True, "facts": fact("Django models are classes.")})
    assert "new context 'django' created" in out and len(S._g.get("django").facts) == 1


def test_a_scoped_turn_is_saved_in_its_own_graph(tmp_path, monkeypatch):
    """registry.save() saved the shared pointer's graph: a scoped session's
    facts lived in memory only and were lost on restart (live 2026-11-27)."""
    import asyncio
    import pytest
    pytest.importorskip("mcp")
    from neuron import server as S
    monkeypatch.setattr(S, "_g", GraphRegistry(str(tmp_path)))
    monkeypatch.setattr(S, "TURSO_ENGINE", False)
    S._g.switch("graymatter")
    asyncio.run(S.call_tool("store_turn", {"_scope": "inbox", "facts": [
        {"kind": "fact", "text": "An inbox fact must reach the disk.", "concepts": ["inbox"]}]}))
    fresh = GraphRegistry(str(tmp_path))
    assert [f["text"] for f in fresh.get("inbox").facts.values()] == ["An inbox fact must reach the disk."]
    assert not fresh.get("graymatter").facts
