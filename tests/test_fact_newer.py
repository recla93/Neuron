"""The latest word on a subject (search._with_newer) and seen facts left out
before the top-k cut. Fixed vectors: the numbers of the live case, 2026-11-10."""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from neuron import search as se  # noqa: E402
from neuron.models import Graph  # noqa: E402

Q = (1.0, 0.0, 0.0)
OLD = (0.9, math.sqrt(1 - 0.81), 0.0)                 # the question 0.90
_y = (0.66 - 0.9 * 0.53) / OLD[1]
NEW = (0.53, _y, math.sqrt(1 - 0.53 ** 2 - _y ** 2))  # the question 0.53, the old fact 0.66 (the live pair)


def _embed(vectors):
    def embed(text):
        for key, v in vectors.items():
            if key in text:
                return v
        return Q
    return embed


def _graph():
    g = Graph()
    old = g.add_fact("fact", "OLDTXT memory lives in the MSIX container", ["msix", "memoria"], 72)
    new = g.add_fact("fix", "NEWTXT memory now lives in the user folder", ["msix", "suite"], 74)
    return g, old["id"], new["id"]


def _ids(hits):
    return [h[0]["id"] for h in hits]


def test_without_the_rule_the_update_is_out_of_the_window():
    g, old, _new = _graph()
    hits = se._search_facts("where?", g, 3, 0.40, 0.20, 0.15, embed=_embed({"OLDTXT": OLD, "NEWTXT": NEW}))
    assert _ids(hits) == [old]


def test_the_later_fact_on_the_same_subject_comes_first():
    g, old, new = _graph()
    hits = se._search_facts("where?", g, 3, 0.40, 0.20, 0.15,
                            embed=_embed({"OLDTXT": OLD, "NEWTXT": NEW}), newer=True)
    assert _ids(hits) == [new, old]


def test_no_shared_concept_no_update():
    g = Graph()
    old = g.add_fact("fact", "OLDTXT memory lives in the MSIX container", ["msix"], 72)
    g.add_fact("fix", "NEWTXT memory now lives in the user folder", ["venv"], 74)
    hits = se._search_facts("where?", g, 3, 0.40, 0.20, 0.15,
                            embed=_embed({"OLDTXT": OLD, "NEWTXT": NEW}), newer=True)
    assert _ids(hits) == [old["id"]]


def test_an_earlier_fact_never_rides_in_as_an_update():
    g = Graph()
    g.add_fact("fact", "NEWTXT memory now lives in the user folder", ["msix"], 70)   # older this time
    old = g.add_fact("fact", "OLDTXT memory lives in the MSIX container", ["msix"], 72)
    hits = se._search_facts("where?", g, 3, 0.40, 0.20, 0.15,
                            embed=_embed({"OLDTXT": OLD, "NEWTXT": NEW}), newer=True)
    assert _ids(hits) == [old["id"]]


def test_seen_facts_leave_before_the_cut_and_weak_ones_stay_out():
    g = Graph()
    ids = [g.add_fact("fact", f"F{i}TXT a fact", ["x"], i)["id"] for i in range(5)]
    sims = [0.95, 0.93, 0.92, 0.90, 0.60]   # the last one is out of the 0.15 window
    vecs = {f"F{i}TXT": (s, math.sqrt(1 - s * s), 0.0) for i, s in enumerate(sims)}
    hits = se._search_facts("q", g, 3, 0.40, 0.0, 0.15, embed=_embed(vecs), exclude=set(ids[:3]))
    assert _ids(hits) == [ids[3]]


def test_the_update_goes_before_every_earlier_fact_on_its_subject():
    g = Graph()
    a = g.add_fact("fact", "ATXT the first diagnosis", ["msix"], 71)["id"]
    b = g.add_fact("fact", "BTXT the second diagnosis", ["msix"], 72)["id"]
    n = g.add_fact("fix", "NEWTXT the move", ["msix"], 74)["id"]
    # A is the best hit but too far from the update to match it (0.5 < 0.6): the
    # update matches B, and still goes before A
    off = math.hypot(NEW[1], NEW[2])                      # A leans away from the update
    r = math.sqrt(1 - 0.95 ** 2)
    vecs = {"ATXT": (0.95, -r * NEW[1] / off, -r * NEW[2] / off), "BTXT": OLD, "NEWTXT": NEW}
    assert sum(x * y for x, y in zip(vecs["ATXT"], NEW)) < se.NEWER_MIN_SIM
    hits = se._search_facts("where?", g, 3, 0.40, 0.20, 0.15, embed=_embed(vecs), newer=True)
    assert _ids(hits)[0] == n and set(_ids(hits)) == {a, b, n}


def test_the_fact_the_message_quotes_beats_a_slightly_closer_one():
    """bench v2 m16: "serve un install.ps1 -Force" left the fact about
    `install.ps1 -Force` 6th. Equal vectors, so only the wording decides; a word
    every fact has ("memory") weighs little, and lex=0 keeps the old order."""
    g = Graph()
    a = g.add_fact("fact", "ATXT memory needs install.ps1 -Force after a code change", ["venv"], 1)["id"]
    b = g.add_fact("fact", "BTXT memory is copied into the venv by the wheel", ["venv"], 2)["id"]
    c = g.add_fact("fact", "CTXT memory of the daemon survives a restart", ["daemon"], 3)["id"]
    vecs = {"ATXT": (0.60, 0.80, 0.0), "BTXT": (0.62, math.sqrt(1 - 0.62 ** 2), 0.0), "CTXT": (0.61, math.sqrt(1 - 0.61 ** 2), 0.0)}
    q = "memory: do we still need an install.ps1 -Force?"
    assert _ids(se._search_facts(q, g, 1, 0.40, 0.0, 0.15, embed=_embed(vecs))) == [b]
    assert _ids(se._search_facts(q, g, 1, 0.40, 0.0, 0.15, embed=_embed(vecs), lex=0.15)) == [a]
    facts = [g.facts[a], g.facts[b], g.facts[c]]
    assert se._lexical("memory", facts)[a] < 0.2 and se._lexical(q, facts)[a] == 1.0


def test_a_facts_own_use_rate_only_adds_and_only_when_asked():
    """ADR-021: a fact used in half of its showings passes a slightly closer one
    when the caller is in the experiment's arm (use > 0). Never shown, shown
    once, shown and never used: no bonus and no penalty — unused is not useless."""
    g = Graph()
    a = g.add_fact("fact", "ATXT first", ["x"], 1)["id"]
    b = g.add_fact("fact", "BTXT second", ["x"], 2)["id"]
    c = g.add_fact("fact", "CTXT third", ["x"], 3)["id"]
    vecs = {"ATXT": (0.60, 0.80, 0.0), "BTXT": (0.62, math.sqrt(1 - 0.62 ** 2), 0.0), "CTXT": (0.61, math.sqrt(1 - 0.61 ** 2), 0.0)}
    g.facts[a].update(shown=4, confirmed=2)
    g.facts[c].update(shown=6, confirmed=0)
    assert _ids(se._search_facts("q", g, 3, 0.40, 0.0, 0.15, embed=_embed(vecs))) == [b, c, a]
    assert _ids(se._search_facts("q", g, 3, 0.40, 0.0, 0.15, embed=_embed(vecs), use=0.10)) == [a, b, c]
    g.facts[a].update(shown=1, confirmed=1)
    assert se._use_rate(g.facts[a]) == 0.0 and se._use_rate(g.facts[c]) == 0.0
