"""The keywords of one turn are linked to each other.

Reported 2026-10-24 on a fresh install: 4 turns, 14 nodes, 0 links. Links came
only from what the model remembered to write, so a query that landed on `focus`
never reached `primeng`, stored in the same turn and carrying the fact.
"""

import asyncio

from neuron import server as S
from neuron.models import Graph, Link, Node
from neuron.registry import GraphRegistry


def _graph(*kws: str) -> Graph:
    g = Graph()
    for kw in kws:
        g.add_node(Node(keyword=kw, turn=1, topic="t", domain="general",
                        sentiment="neutral"))
    return g


def test_every_pair_once_and_an_existing_link_wins():
    g = _graph("primeng", "dropdown", "safari", "focus")
    g.add_link(Link(source="safari", target="primeng", link_type="cause-effect",
                    weight="strong", rationale="", created_turn=1,
                    last_active_turn=1))
    added = g.link_cooccurring(["primeng", "dropdown", "safari", "focus", "Focus",
                                "assente"], turn=1)

    assert added == 5, "6 coppie, meno quella gia' collegata (in un verso o nell'altro)"
    pairs = {frozenset((lk.source, lk.target)) for lk in g.links}
    assert len(pairs) == len(g.links) == 6, "una coppia, un link"
    new = [lk for lk in g.links if lk.rationale == "same turn"]
    assert all((lk.weight, lk.link_type) == ("medium", "deepening") for lk in new)
    assert g.link_cooccurring(["primeng", "focus"], turn=2) == 0


def test_a_same_turn_link_survives_idle_turns():
    """Tangential would expire after a few idle turns — the bug solved once,
    and asked about months later, is exactly the case this exists for."""
    g = _graph("primeng", "focus")
    g.link_cooccurring(["primeng", "focus"], turn=1)
    for _ in range(50):
        g.turn_count += 1
        g.increment_inactivity(set())
    g.prune_tangential()
    assert len(g.links) == 1


def test_a_query_on_one_keyword_reaches_the_fact_on_another(tmp_path, monkeypatch):
    """The reported case, end to end through store_turn and pre_turn."""
    monkeypatch.setattr(S, "_g", GraphRegistry(graphs_dir=str(tmp_path)))
    call = lambda tool, args: asyncio.run(S.call_tool(tool, args))[0].text

    out = call("store_turn", {
        "topic": "bug p-dropdown in modale Safari",
        "keywords": ["primeng", "dropdown", "safari", "focus"],
        "episode": "Su Safari il p-dropdown in una p-dialog perde il focus: appendTo body."})
    assert "+6 same-turn" in out, out

    recall = call("pre_turn", {"topic": "focus", "keywords": ["focus"]})
    assert "primeng" in recall, f"da focus non si arriva a primeng: {recall}"
    assert "appendTo body" in recall, f"il fatto su primeng non emerge: {recall}"
