"""retire (2026-11-28): a fact leaves the memory with no replacement, in
whatever context it lives, and can come back. supersedes needed a new fact in
its place: for junk, a tombstone that was noise itself."""
import asyncio
import json

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


def _retire(S, facts, restore=False):
    return json.loads(asyncio.run(S.call_tool("retire", {"facts": facts, "restore": restore}))[0].text)


def _fresh(S):
    from neuron.registry import GraphRegistry
    return GraphRegistry(S._g._graphs_dir)


def test_a_fact_of_another_context_is_retired_and_stays_retired(S):
    fid = S._g.get("default").add_fact("fact", "xxxxxxxx", ["junk"], 1)["id"]
    S._g.save("default")
    out = _retire(S, [f"#{fid}"])
    assert out["retired"] == [f"default:{fid}"]
    g = _fresh(S).get("default")
    assert g.facts[fid]["status"] == "retired"          # saved, not only in memory
    assert fid not in [e["id"] for e in g.episodes.get("junk", [])]


def test_restore_brings_it_back(S):
    g = S._g.get()
    fid = g.add_fact("fact", "the GPU is a GTX 1080", ["vram"], 1)["id"]
    S._g.save("graymatter")
    _retire(S, [fid])
    out = _retire(S, [fid], restore=True)
    assert out["restored"] == [f"graymatter:{fid}"]
    assert _fresh(S).get("graymatter").facts[fid]["status"] == "active"
    assert fid in [e["id"] for e in S._g.get().episodes["vram"]]


def test_twice_or_unknown_changes_nothing(S):
    fid = S._g.get().add_fact("fact", "a fact", ["x"], 1)["id"]
    _retire(S, [fid])
    out = _retire(S, [fid, "deadbeef"])
    assert out["retired"] == [] and out["unknown"] == ["deadbeef"]
    assert out["unchanged"] == [f"graymatter:{fid} (retired)"]


def test_a_retired_fact_leaves_the_recall(S, monkeypatch):
    g = S._g.get()
    fid = g.add_fact("fact", "release prepared, commits 955b701", ["release"], 1)["id"]
    seen = []
    monkeypatch.setattr(S._search, "_ensure_recall_vecs", lambda graph: 0)

    def spy(q, graph, *a, **k):
        seen.extend(f["id"] for f in graph.facts.values() if f["status"] == "active")
        return []
    monkeypatch.setattr(S._search, "_search_facts", spy)
    S._fact_hits("release", g, 3, 0.4, 0.15)
    assert fid in seen
    seen.clear()
    _retire(S, [fid])
    S._fact_hits("release", g, 3, 0.4, 0.15)
    assert fid not in seen


def test_retire_is_not_announced_to_the_model(S):
    names = [t.name for t in asyncio.run(S.list_tools())]
    assert "retire" not in names                       # the user's call, not the loop's


def test_a_concept_with_no_fact_goes_to_the_graveyard_with_its_links(S):
    from neuron.models import Link, Node
    g = S._g.get("default")
    for kw in ("ada", "pranzo", "turso"):
        g.add_node(Node(keyword=kw, turn=1, domain="general", sentiment="neutral", topic="pranzo con Ada"))
    g.add_link(Link("ada", "pranzo", "instance-of", "strong", "", 1, 1))
    g.add_link(Link("turso", "ada", "analogy", "strong", "", 1, 1))
    S._g.save("default")
    out = json.loads(asyncio.run(S.call_tool("retire", {"concepts": ["ada", "pranzo"]}))[0].text)
    assert sorted(out["concepts_retired"]) == ["default:ada", "default:pranzo"]
    fresh = _fresh(S).get("default")
    assert fresh.get_node("ada") is None and fresh.get_node("turso") is not None
    assert not [lk for lk in fresh.links if "ada" in (lk.source, lk.target)]
    grave = fresh.load_graveyard(fresh_path(S, "default"), "default")
    assert {e["keyword"] for e in grave} >= {"ada", "pranzo"}


def test_a_concept_with_an_active_fact_stays(S):
    from neuron.models import Node
    g = S._g.get()
    g.add_node(Node(keyword="pranzo", turn=1, domain="general", sentiment="neutral", topic="lunch"))
    g.add_fact("fact", "lunch is at noon", ["pranzo"], 1)
    S._g.save("graymatter")
    out = json.loads(asyncio.run(S.call_tool("retire", {"concepts": ["pranzo"]}))[0].text)
    assert out["unchanged"] == ["graymatter:pranzo (has active facts)"]
    assert S._g.get().get_node("pranzo") is not None


def fresh_path(S, ctx):
    import os
    return os.path.join(S._g._graphs_dir, f"graph_{ctx}.db")
