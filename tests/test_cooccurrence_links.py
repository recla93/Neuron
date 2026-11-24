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
    assert all((lk.weight, lk.link_type) == ("tangential", "deepening") for lk in new)
    assert g.link_cooccurring(["primeng", "focus"], turn=2) == 0


def test_a_same_turn_pair_named_once_expires():
    """2026-11-19: once is a coincidence. 2131 of 2233 live links were same-turn,
    1453 never repeated, and they filled the recall budget the facts needed.
    It expires while both ends keep another link (here, through `dialog`)."""
    g = _graph("primeng", "focus", "dialog")
    g.link_cooccurring(["primeng", "focus"], turn=1)
    g.add_link(Link(source="primeng", target="dialog", link_type="deepening",
                    weight="strong", rationale="r", created_turn=1, last_active_turn=1))
    g.add_link(Link(source="focus", target="dialog", link_type="deepening",
                    weight="strong", rationale="r", created_turn=1, last_active_turn=1))
    for _ in range(50):
        g.turn_count += 1
        g.increment_inactivity(set())
    g.prune_tangential()
    assert {frozenset((lk.source, lk.target)) for lk in g.links} == {
        frozenset(("primeng", "dialog")), frozenset(("focus", "dialog"))}


def test_the_last_link_of_a_node_never_expires():
    """2026-11-24: expiry left 344 of 1000 live concepts with no link at all. A
    pair named once with nothing else keeps its link; and of two expired links
    on a leaf, the leaf keeps one."""
    g = _graph("primeng", "focus")
    g.link_cooccurring(["primeng", "focus"], turn=1)
    h = _graph("hub", "x", "y")
    h.link_cooccurring(["hub", "x"], turn=1)
    h.link_cooccurring(["hub", "y"], turn=1)
    h.link_cooccurring(["x", "y"], turn=1)
    for gr in (g, h):
        for _ in range(50):
            gr.turn_count += 1
            gr.increment_inactivity(set())
        gr.prune_tangential()
    assert len(g.links) == 1, "the pair's only link stays"
    linked = {e for lk in h.links for e in (lk.source, lk.target)}
    assert linked == {"hub", "x", "y"}, "no node of the triangle is left without a link"
    assert len(h.links) == 2, "the triangle loses exactly one expired link"


def test_a_same_turn_pair_that_comes_back_stays():
    g = _graph("primeng", "focus")
    g.link_cooccurring(["primeng", "focus"], turn=1)
    for turn in (1, 3, 5):                      # the cooldown counts one every 2 turns
        g.reinforce_coactivation(["primeng", "focus"], turn=turn)
    assert g.links[0].weight == "medium"
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


def test_an_isolated_concept_is_linked_to_a_concept_of_its_fact():
    """2026-11-24: 345 of 1005 live concepts had no link; 197 shared a fact. The
    partner is the concept sharing the most facts; a concept with no fact stays
    alone (the nearest vector was rejected: `sed -> java` at cos 0.90)."""
    g = _graph("profiling", "slow test suite", "pytest", "lonely")
    g.add_fact("fix", "profile one file to find a slow suite", ["profiling", "slow test suite"], turn=1)
    g.add_fact("fix", "cProfile on pytest", ["profiling", "slow test suite", "pytest"], turn=1)
    assert g.link_isolated_by_facts(turn=2) == 2
    pairs = {frozenset((lk.source, lk.target)) for lk in g.links}
    assert frozenset(("profiling", "slow test suite")) in pairs, "two shared facts win"
    assert any("pytest" in p for p in pairs), "pytest shares one fact: linked too"
    assert not any("lonely" in p for p in pairs), "no fact, no anchor"
    assert all((lk.weight, lk.rationale) == ("tangential", "same fact") for lk in g.links)
    assert g.link_isolated_by_facts(turn=3) == 0, "idempotent"


def test_eviction_takes_the_concept_with_no_fact_and_no_link_first(monkeypatch):
    """At equal salience the stable sort evicted the oldest, linked or not."""
    import neuron.models as M
    monkeypatch.setattr(M, "MAX_NODES", 3)
    g = _graph("old linked", "old with fact", "young alone")
    g.nodes[0].turn, g.nodes[1].turn, g.nodes[2].turn = 1, 2, 9
    g.add_link(Link(source="old linked", target="old with fact", link_type="deepening",
                    weight="strong", rationale="r", created_turn=1, last_active_turn=1))
    g.add_fact("fact", "a fact", ["old with fact"], turn=2)
    g.add_node(Node(keyword="newcomer", turn=10, topic="t", domain="d", sentiment="neutral"))
    assert {n.keyword for n in g.nodes} == {"old linked", "old with fact", "newcomer"}
