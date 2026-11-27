"""A project's recall brings ONE fact of another project when it is very close
(2026-11-27): measured on 282 real messages, judged blind, 19 of 21 were useful
at cosine >= 0.60. It adds after the project's own facts, carries its project in
the label, never reaches the flash, and stays out of the inbox (which reads every
graph already)."""
import asyncio

import pytest


@pytest.fixture
def S(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    from neuron import server as S
    from neuron.registry import GraphRegistry
    monkeypatch.setattr(S, "_g", GraphRegistry(graphs_dir=str(tmp_path)))
    monkeypatch.setattr(S, "TURSO_ENGINE", False)
    monkeypatch.setattr(S, "CROSS_FACT_SIM", 0.60)
    S._g.switch("graymatter")
    return S


def _f(i):
    return {"id": i, "kind": "decision", "concepts": ["risk"], "text": i}


@pytest.fixture
def scores(S, monkeypatch):
    """Per-graph hits; the floor (4th argument) applies as in _search_facts."""
    lists = {}
    monkeypatch.setattr(S._search, "_search_facts", lambda q, gr, k, floor, *a, **kw:
                        [h for h in lists.get(id(gr), []) if h[1] >= floor][:k])
    return lists


def _two_projects(S):
    arb = S._g.get("arbor")
    arb.add_fact("decision", "ARBOR risk is computed over fixed questions.", ["risk"], 1)
    S._g.save("arbor")
    return S._g.get(), arb


def test_the_recall_adds_the_closest_fact_of_another_project_by_its_score(S, scores):
    proj, arb = _two_projects(S)
    scores[id(proj)] = [(_f("own"), 0.70, "risk"), (_f("own2"), 0.62, "risk")]
    scores[id(arb)] = [(_f("a1"), 0.65, "risk")]
    hits = S._fact_hits("q", proj, 3, 0.4, 0.15, cross=True)
    # every own fact stays; the other project's goes where its score puts it,
    # not last, where the hook's trim cut it
    assert [(h[0]["id"], h[2]) for h in hits] == [("own", "risk"), ("a1", "arbor: risk"), ("own2", "risk")]


def test_a_far_fact_of_another_project_stays_out(S, scores):
    proj, arb = _two_projects(S)
    scores[id(arb)] = [(_f("a1"), 0.58, "risk")]           # under 0.60
    assert S._fact_hits("q", proj, 3, 0.4, 0.15, cross=True) == []


def test_the_flash_never_gets_another_projects_fact(S, scores):
    proj, arb = _two_projects(S)
    scores[id(arb)] = [(_f("a1"), 0.90, "risk")]
    assert S._fact_hits("q", proj, 3, 0.4, 0.15) == []    # cross is the recall's alone


def test_zero_turns_it_off(S, scores, monkeypatch):
    proj, arb = _two_projects(S)
    scores[id(arb)] = [(_f("a1"), 0.90, "risk")]
    monkeypatch.setattr(S, "CROSS_FACT_SIM", 0.0)
    assert S._fact_hits("q", proj, 3, 0.4, 0.15, cross=True) == []


def test_the_inbox_does_not_get_it_twice(S, scores):
    _proj, arb = _two_projects(S)
    S._g.switch("inbox")
    scores[id(arb)] = [(_f("a1"), 0.90, "risk")]
    ids = [h[0]["id"] for h in S._fact_hits("q", S._g.get(), 3, 0.4, 0.15, cross=True)]
    assert ids == ["a1"]                                   # once, from the inbox's own reading


def test_a_fact_brought_from_another_project_can_be_confirmed(S):
    _proj, arb = _two_projects(S)
    fid = next(iter(arb.facts))
    out = asyncio.run(S.call_tool("confirm", {"facts": [fid]}))[0].text
    assert fid in out
    from neuron.registry import GraphRegistry
    assert GraphRegistry(S._g._graphs_dir).get("arbor").facts[fid]["confirmed"] == 1
