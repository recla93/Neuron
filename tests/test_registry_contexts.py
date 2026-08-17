"""Contexts must survive a restart, and must be listable before being touched.

Both bugs here were found on a live install on 2026-08-17, and both were silent
— which is why they had lasted: `_active_context.txt` said "ai", no
`graph_ai.db` existed, the running server reported "default", and nothing
anywhere reported the disagreement.
"""

import pathlib
import tempfile

import pytest

from neuron.models import Node
from neuron.registry import GraphRegistry


@pytest.fixture
def graphs_dir():
    return tempfile.mkdtemp(prefix="ctxtest_")


def _node(kw: str) -> Node:
    return Node(keyword=kw, turn=1, topic="t", domain="general",
                sentiment="neutral", salience=5)


def test_active_context_survives_a_restart_before_anything_is_stored(graphs_dir):
    """Switch to a fresh context, restart, and you are still in it.

    An empty context has no file: `save_sqlite` writes nothing when there is
    nothing to write. Restoring the pointer used to be gated on that file
    existing, so switching and restarting before the first turn dropped you back
    into "default" while the pointer kept naming the context you chose — and the
    two never reconciled. The pointer is a name; `get()` materialises the graph
    on demand, here as everywhere else.
    """
    GraphRegistry(graphs_dir=graphs_dir).switch("ai")
    assert not (pathlib.Path(graphs_dir) / "graph_ai.db").exists(), (
        "premessa del test: un contesto vuoto non ha ancora un file")

    restarted = GraphRegistry(graphs_dir=graphs_dir)
    assert restarted.active == "ai", (
        "il contesto scelto e' stato perso al riavvio, e il puntatore su disco "
        "resta a indicarlo: i due non tornano piu' d'accordo")


def test_list_contexts_reports_what_is_on_disk_not_what_this_process_touched(graphs_dir):
    """A context you built yesterday must be listed today, before you touch it.

    `list_contexts` iterated the in-memory dict while calling itself "all
    available contexts": a freshly started server answered with an empty list,
    and a live install reported one context while four had files on disk. What
    is not listed cannot be switched back to, so the contexts were effectively
    unreachable.
    """
    first = GraphRegistry(graphs_dir=graphs_dir)
    for ctx in ("veicoli", "arredamento"):
        first.get(ctx).add_node(_node(f"nodo-{ctx}"))
        first.save(ctx)

    fresh = GraphRegistry(graphs_dir=graphs_dir)          # nothing touched yet
    listed = {c["context"]: c["nodes"] for c in fresh.list_contexts()}
    assert listed == {"veicoli": 1, "arredamento": 1}, (
        f"il disco ha due contesti, l'elenco ne riporta {listed}")


def test_save_all_persists_a_brand_new_context(graphs_dir):
    """The checkpoint must cover the contexts most likely to be lost.

    `save_all` is what BOTH durability nets call: the worker's periodic
    checkpoint and its shutdown handler — the one written precisely so a dirty
    kill on Windows still keeps data. It used to skip `ctx in _seed_loaded`
    under the heading "never writes to seed", but it only ever writes to the
    context's own file, and `get()` marks EVERY newly created context as
    seed-loaded because it warm-starts them. So a fresh context was invisible to
    both nets and survived only if an explicit per-turn `save(ctx)` happened to
    fire.

    Note what is NOT called here: only `save_all`, never `save(ctx)`.
    """
    reg = GraphRegistry(graphs_dir=graphs_dir)
    reg.switch("ai")
    assert "ai" in reg._seed_loaded, "premessa: un contesto nuovo e' seed-loaded"
    reg.get("ai").add_node(_node("vault lock"))
    reg.save_all()

    assert (pathlib.Path(graphs_dir) / "graph_ai.db").exists(), (
        "il checkpoint ha saltato il contesto nuovo: al prossimo riavvio sparisce")
    assert [n.keyword for n in GraphRegistry(graphs_dir=graphs_dir).get("ai").nodes] \
        == ["vault lock"]


def test_save_all_still_writes_nothing_for_an_untouched_context(graphs_dir):
    """What the old guard was reaching for, kept — by the right gate.

    A context warm-started from the seed and never modified must not
    materialise a file. `save_sqlite` already returns early when the graph is
    not dirty, which is provenance-independent and therefore correct.
    """
    reg = GraphRegistry(graphs_dir=graphs_dir)
    reg.get("veicoli")                      # warm-start only, no edits
    reg.save_all()
    assert not list(pathlib.Path(graphs_dir).glob("graph_*.db")), (
        "un contesto mai modificato non deve creare un file")


def test_a_context_with_a_stored_turn_comes_back_whole(graphs_dir):
    """The round trip the feature exists for: switch, store, restart, still there."""
    reg = GraphRegistry(graphs_dir=graphs_dir)
    reg.switch("ai")
    reg.get("ai").add_node(_node("vault lock"))
    reg.save("ai")

    restarted = GraphRegistry(graphs_dir=graphs_dir)
    assert restarted.active == "ai"
    assert [n.keyword for n in restarted.get("ai").nodes] == ["vault lock"]
