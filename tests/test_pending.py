"""Pendings (2026-11-27): what is left to do lives in one `pending` graph, is
shown at each session start of its project before "where we stopped", and a
fact of the project closes it with supersedes=<its id>."""
import asyncio

import pytest


@pytest.fixture
def S(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    from neuron import server as S
    from neuron.registry import GraphRegistry
    monkeypatch.setattr(S, "_g", GraphRegistry(graphs_dir=str(tmp_path)))
    monkeypatch.setattr(S, "TURSO_ENGINE", False)
    S._g.switch("graymatter")
    return S


def _call(S, tool, args):
    return asyncio.run(S.call_tool(tool, args))[0].text


def _start(S):
    return _call(S, "pre_turn", {"topic": "where were we", "profile": True})


def test_pending_lives_apart_shows_first_and_closes(S, tmp_path):
    from neuron.registry import GraphRegistry
    _call(S, "store_turn", {"facts": [
        {"kind": "pending", "text": "Rebuild the GM wheel before pushing.", "concepts": ["wheel"]}]})
    pg = S._g.get("pending")
    (pid, p), = pg.facts.items()
    assert p["kind"] == "pending" and "graymatter" in p["concepts"]
    assert not S._g.get("graymatter").facts                       # not in the project graph
    _call(S, "store_turn", {"facts": [
        {"kind": "habit", "text": "Reply in Italian.", "concepts": ["language"]}]})
    out = _start(S)
    assert f"pending: Rebuild the GM wheel before pushing. #{pid}" in out
    assert out.index("pending: ") < out.index("profile: ")       # first: a hook budget cuts the tail
    assert "contexts: " in out and "pending " not in out.split("contexts: ")[1].split(" — ")[0]

    S._g.switch("mocoder")                                        # another project: not shown
    assert "pending:" not in _start(S)
    S._g.switch("inbox")                                          # the inbox: every project's
    assert f"#{pid}" in _start(S)

    S._g.switch("graymatter")
    rep = _call(S, "store_turn", {"facts": [
        {"kind": "progress", "text": "GM wheel rebuilt into both vendors.", "concepts": ["wheel"],
         "supersedes": pid}]})
    assert f"closes pending #{pid}" in rep
    assert [f["kind"] for f in S._g.get("graymatter").facts.values()] == ["progress"]
    assert GraphRegistry(S._g._graphs_dir).get("pending").facts[pid]["status"] == "superseded"
    assert "pending:" not in _start(S)


def test_pendings_keep_the_plan_order_blockers_first_and_drift_shows(S):
    _call(S, "store_turn", {"facts": [
        {"kind": "pending", "text": "Step one of the plan.", "concepts": ["plan"]},
        {"kind": "pending", "text": "Step two of the plan.", "concepts": ["plan"]},
        {"kind": "pending", "text": "Rebuild the wheel before any push.", "concepts": ["wheel", "blocker"]}]})
    pg = S._g.get("pending")
    old = next(f for f in pg.facts.values() if f["text"].startswith("Step one"))
    old["created_at"] = "2026-01-01T09:00:00"                    # opened long ago, never touched
    texts = [f["text"] for f in S._open_pending("graymatter")]
    assert texts == ["Rebuild the wheel before any push.", "Step one of the plan.", "Step two of the plan."]
    line = _start(S).split("pending: ", 1)[1]
    assert line.startswith("[blocker] Rebuild the wheel")
    assert f"#{old['id']} (01/01, stale " in line


def test_pendings_past_the_cap_are_counted_not_hidden(S, monkeypatch):
    monkeypatch.setattr(S, "PENDING_MAX", 2)
    for i in range(3):
        _call(S, "store_turn", {"facts": [{"kind": "pending", "text": f"Task number {i} left open.",
                                           "concepts": ["task"]}]})
    line = _start(S).split("pending: ", 1)[1]
    assert "Task number 0" in line and "Task number 2" not in line.split(" | +")[0]
    assert "+1 more pending" in line
