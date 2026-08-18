"""When the context moves, the two halves of the session stay reachable.

The auto-switch relocated the active graph and left nothing behind, so one
continuous stretch of work ended up split across two graphs with no link
between them — observed 2026-08-17: half a session in `default`, half in `ai`,
neither holding the whole thing, and no way to get from one to the other.

`CrossLink` and its whole supporting cast (persistence, lookup by either end,
parent-prefix inheritance) already existed in the registry and were called by
nobody. These tests pin the caller.
"""

import tempfile

import pytest

from neuron.models import Node
from neuron.registry import GraphRegistry


@pytest.fixture
def reg():
    return GraphRegistry(graphs_dir=tempfile.mkdtemp(prefix="trail_"))


def _node(kw: str, salience: int = 5) -> Node:
    return Node(keyword=kw, turn=1, topic="t", domain="general",
                sentiment="neutral", salience=salience)


def test_the_same_passage_is_recorded_once(reg):
    """`_cross_links` is a flat list, loaded whole at startup, appended to on
    every switch. The same passage recurs across sessions, so without dedup
    months of use grow an unbounded list of identical rows — all re-read on
    every start."""
    for _ in range(5):
        reg.add_cross_link("default", "vault lock", "ai", "benchmark",
                           rationale="ripetuto")
    assert len(reg._cross_links) == 1, (
        f"lo stesso passaggio registrato {len(reg._cross_links)} volte")


def test_dedup_is_per_endpoint_pair_not_per_context(reg):
    """Two different concepts moving between the same two contexts are two
    different facts. Deduplicating on the contexts alone would collapse them."""
    reg.add_cross_link("default", "vault lock", "ai", "benchmark")
    reg.add_cross_link("default", "handshake", "ai", "relevance filter")
    assert len(reg._cross_links) == 2


def test_a_trail_is_reachable_from_both_ends(reg):
    """The point of recording it: whichever half of the split session you are
    in, you can get to the other."""
    reg.add_cross_link("default", "vault lock", "ai", "benchmark")
    assert reg.get_cross_links("default"), "irraggiungibile dal contesto di partenza"
    assert reg.get_cross_links("ai"), "irraggiungibile dal contesto di arrivo"


def test_a_trail_survives_a_restart(reg):
    """Il checkpoint del worker e il suo shutdown handler chiamano `save_all`,
    che scrive anche il trail. Resta vero, ma non e' piu' l'unica strada: vedi
    il test sotto."""
    reg.add_cross_link("default", "vault lock", "ai", "benchmark",
                       rationale="il soggetto si e' spostato")
    reg.save_all()

    restarted = GraphRegistry(graphs_dir=reg._graphs_dir)
    trails = restarted.get_cross_links("ai")
    assert [(t.source_keyword, t.target_keyword) for t in trails] \
        == [("vault lock", "benchmark")]
    assert trails[0].rationale == "il soggetto si e' spostato"


def test_a_trail_survives_a_dirty_death(reg):
    """Senza nessun checkpoint: la riga deve essere gia' sul disco.

    `store_turn` fa `save(ctx)` a ogni turno, ma `save(ctx)` non tocca il trail.
    Fino a un `save_all()` la riga viveva solo in memoria, quindi uno switch
    seguito da una morte sporca del processo lasciava i turni salvati e il
    collegamento fra le due meta' della sessione perso -- lo stesso guasto per
    cui il trail esiste, un giro piu' in la'.
    """
    reg.add_cross_link("default", "vault lock", "ai", "benchmark",
                       rationale="il soggetto si e' spostato")
    # niente save_all(): e' esattamente cio' che una morte sporca non concede

    riavviato = GraphRegistry(graphs_dir=reg._graphs_dir)
    trails = riavviato.get_cross_links("ai")
    assert [(t.source_keyword, t.target_keyword) for t in trails]         == [("vault lock", "benchmark")], "il trail non e' arrivato al disco"


def test_the_switch_records_the_concept_it_left_behind(reg, monkeypatch):
    """The wiring itself: on a switch, the outgoing context's most salient node
    is linked to the incoming turn's first keyword.

    Source is the most salient node — what you were actually working on — not
    the first one added, which would be whatever the session happened to open
    with.
    """
    import neuron.server as srv

    reg.get("default").add_node(_node("pranzo", salience=1))
    reg.get("default").add_node(_node("vault lock", salience=9))
    monkeypatch.setattr(srv, "_g", reg)

    srv._switch_leaves_a_trail("default", "ai", ["benchmark", "salience"])

    trails = reg.get_cross_links("ai")
    assert len(trails) == 1, trails
    t = trails[0]
    assert (t.source_context, t.source_keyword) == ("default", "vault lock"), (
        "deve partire dal concetto piu' saliente, non dal primo inserito")
    assert (t.target_context, t.target_keyword) == ("ai", "benchmark")


def test_a_switch_out_of_seed_only_content_records_nothing(reg, monkeypatch):
    """A context is never really empty: `get()` warm-starts it from the seed.

    So the naive "has nodes?" check let through a trail whose source was
    whatever the seed shipped — the first draft of this wiring recorded "you
    moved from docs/TOOLS to benchmark" about work nobody had done. A trail out
    of a context the user has never written to is noise with a timestamp.
    """
    import neuron.server as srv
    monkeypatch.setattr(srv, "_g", reg)
    reg.get("default")                       # warm-start only, nothing of ours
    assert reg.get("default").nodes, "premessa: il seed ha popolato il contesto"
    assert all((n.turn or 0) == 0 for n in reg.get("default").nodes), \
        "premessa: le righe del seed stanno al turno 0"

    srv._switch_leaves_a_trail("default", "ai", ["benchmark"])
    assert reg._cross_links == [], (
        f"traccia spuria da contenuto solo-seed: {reg._cross_links}")


def test_the_hysteresis_rule_exists_once():
    """`auto` and `store_turn` must share one implementation of the switch.

    `auto` carried an inline copy of `_signal_domain_switch` — same rules,
    separately maintained — and it had already diverged twice: the copy never
    called `_save_domain_signal()`, so a signal counted there was forgotten
    between turns; and when the switch learned to leave a cross-context trail,
    only the shared version got it. Two copies of one rule drift in silence,
    because nothing fails when they do. This is what fails.
    """
    import pathlib

    import neuron.server as srv

    src = pathlib.Path(srv.__file__).read_text(encoding="utf-8")
    counter_bumps = src.count('_domain_signal["count"] += 1')
    assert counter_bumps == 1, (
        f"la regola dell'isteresi e' implementata {counter_bumps} volte: "
        "una copia tornera' a divergere")
    assert "_signal_domain_switch(" in src


def test_recording_a_trail_never_breaks_the_switch(reg, monkeypatch):
    """Best-effort by construction: a switch that works is worth more than a
    trail that is written."""
    import neuron.server as srv

    class Exploding:
        active = "default"
        def get(self, *a, **k):
            raise RuntimeError("boom")

    monkeypatch.setattr(srv, "_g", Exploding())
    srv._switch_leaves_a_trail("default", "ai", ["benchmark"])   # must not raise
