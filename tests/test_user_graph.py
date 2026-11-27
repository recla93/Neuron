"""The user graph (2026-11-26): habits and facts about the user live in ONE graph,
read from every context. They lived in whatever context was active, so a session
in another project lost the user's rules."""
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


def test_habit_goes_to_user_graph_and_project_fact_stays(S):
    _call(S, "store_turn", {"facts": [
        {"kind": "habit", "text": "Ask before any real usage test.", "concepts": ["usage test"]},
        {"kind": "decision", "text": "Project contexts come from the git remote.", "concepts": ["context"]}]})
    user, proj = S._g.get("user"), S._g.get("graymatter")
    assert [f["kind"] for f in user.facts.values()] == ["habit"]
    assert [f["kind"] for f in proj.facts.values()] == ["decision"]


def test_profile_and_ids_reach_the_user_graph_from_any_context(S):
    _call(S, "store_turn", {"facts": [{"kind": "user", "text": "The user works on Windows 11.",
                                       "concepts": ["windows"]}]})
    fid = next(iter(S._g.get("user").facts))
    S._g.switch("mocoder")                         # another project, empty
    g = S._g.get()
    assert [f["id"] for f in S._profile_facts(g, 1000)] == [fid]
    assert S._fact_graph(fid, g) is S._g.get("user")
    assert "Windows 11" in (S._expand_facts(f"#{fid}", g) or "")
    out = _call(S, "confirm", {"facts": [fid]})
    assert fid in out and S._g.get("user").facts[fid]["confirmed"] == 1


def test_user_graph_itself_is_not_doubled(S):
    _call(S, "store_turn", {"facts": [{"kind": "habit", "text": "Reply in Italian.", "concepts": ["language"]}]})
    u = S._g.get("user")
    assert S._user_graph(u) is None
    assert len(S._profile_facts(u, 1000)) == 1


def test_recall_cache_keeps_every_graph_in_use(monkeypatch, tmp_path):
    """Recalling the project graph then the user graph pruned the cache to the
    last one's texts: the project's facts were skipped on every message."""
    from neuron import search as se
    from neuron.models import Graph

    class Fake:
        def embed(self, texts, batch_size=0):
            return [[1.0, 0.0] for _ in texts]
    fake = Fake()
    monkeypatch.setattr(se, "_get_embedder", lambda: fake)
    monkeypatch.setattr(se, "_persistable", lambda emb: False)
    monkeypatch.setattr(se, "_RECALL_INLINE_MAX", 10_000)
    monkeypatch.setattr(se, "_RECALL_LIVE", {})
    eid = id(fake)
    monkeypatch.setattr(se, "_RECALL_VECS", {(eid, f"stale {i}"): [1.0, 0.0] for i in range(3000)})
    proj, user = Graph(), Graph()
    p = proj.add_fact("decision", "Contexts come from the git remote.", ["context"], 1)["id"]
    u = user.add_fact("habit", "Ask before a real usage test.", ["usage test"], 1)["id"]
    se._ensure_recall_vecs(proj)
    se._ensure_recall_vecs(user)
    assert (eid, se._fact_text(proj.facts[p])) in se._RECALL_VECS
    assert (eid, se._fact_text(user.facts[u])) in se._RECALL_VECS
    assert (eid, "stale 1") not in se._RECALL_VECS


def test_user_hits_join_without_dropping_the_newer_version(S, monkeypatch):
    """_with_newer puts a fact's newer version beside it, past the k slots;
    merging the user's facts by re-sorting dropped it."""
    proj, user = S._g.get(), S._g.get("user")
    user.add_fact("habit", "placeholder", ["x"], 1)
    f = lambda i: {"id": i, "kind": "fact", "concepts": ["x"], "text": i}   # noqa: E731
    lists = {id(proj): [(f("a"), 0.80, "x"), (f("a_new"), 0.50, "x"), (f("b"), 0.70, "x"), (f("c"), 0.60, "x")]}
    monkeypatch.setattr(S._search, "_search_facts",
                        lambda q, gr, *a, **k: lists.get(id(gr), [(f("u_weak"), 0.55, "x")]))
    assert [h[0]["id"] for h in S._fact_hits("q", proj, 3, 0.4, 0.15, newer=True)] == ["a", "a_new", "b", "c"]
    lists[id(user)] = [(f("u_strong"), 0.75, "x")]
    assert [h[0]["id"] for h in S._fact_hits("q", proj, 3, 0.4, 0.15, newer=True)] == ["a", "u_strong", "a_new", "b"]


def test_the_inbox_reads_every_graph_with_its_label(S, monkeypatch):
    moc = S._g.get("mocoder")
    moc.add_fact("decision", "mocoder 1.0 waits for the Java path run.", ["release"], 1)
    S._g.save("mocoder")
    S._g.switch("inbox")
    inbox = S._g.get()
    f = lambda i: {"id": i, "kind": "fact", "concepts": ["x"], "text": i}   # noqa: E731
    lists = {id(moc): [(f("m1"), 0.70, "release")]}
    monkeypatch.setattr(S._search, "_search_facts", lambda q, gr, *a, **k: lists.get(id(gr), []))
    assert [(h[0]["id"], h[2]) for h in S._fact_hits("q", inbox, 3, 0.4, 0.15)] == [("m1", "mocoder: release")]
    S._g.switch("graymatter")                       # a project reads its own and the user's only
    assert S._fact_hits("q", S._g.get(), 3, 0.4, 0.15) == []


def test_a_confirm_from_the_inbox_saves_the_graph_it_touched(S, tmp_path):
    from neuron.registry import GraphRegistry
    fid = S._g.get("mocoder").add_fact("decision", "mocoder license is MIT.", ["license"], 1)["id"]
    S._g.save("mocoder")
    S._g.switch("inbox")
    assert fid in _call(S, "confirm", {"facts": [fid]})
    assert GraphRegistry(S._g._graphs_dir).get("mocoder").facts[fid]["confirmed"] == 1


def test_the_session_start_lists_the_contexts(S):
    S._g.get("mocoder").add_fact("decision", "mocoder license is MIT.", ["license"], 1)
    S._g.save("mocoder")
    out = _call(S, "pre_turn", {"topic": "where were we", "profile": True})
    line = next(l for l in out.splitlines() if l.startswith("contexts: "))
    assert "mocoder 1" in line and "context=" in line and "user" not in line.split(" — ")[0], line
    assert "contexts: " not in _call(S, "pre_turn", {"topic": "next message"})
