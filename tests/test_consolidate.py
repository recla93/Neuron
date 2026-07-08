"""E1.2 — Graph.consolidate(): merge near-duplicati + archivio _graveyard.

Puro (neuron.models + sqlite), gira ovunque.
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import neuron.models as m
from neuron.models import Graph, Node, Link


def _vec(i):
    v = [0.0] * m.VECTOR_DIM
    v[i] = 1.0
    return v


def _link(src, tgt):
    return Link(source=src, target=tgt, link_type="deepening", weight="medium",
                rationale="", created_turn=1, last_active_turn=1)


def _graph_with_dupes():
    g = Graph(); g.turn_count = 5
    g.add_node(Node(keyword="spring", turn=1, topic="t", domain="backend", sentiment="neutral", salience=3))
    g.add_node(Node(keyword="spring-boot", turn=1, topic="t", domain="backend", sentiment="neutral", salience=5))
    g.add_node(Node(keyword="docker", turn=1, topic="t", domain="devops", sentiment="neutral", salience=2))
    g.get_node("spring").vector = _vec(0)
    g.get_node("spring-boot").vector = _vec(0)   # identico a 'spring' -> cos 1.0
    g.get_node("docker").vector = _vec(1)        # ortogonale
    g.add_link(_link("spring-boot", "docker"))   # sarà riscritto a spring->docker
    g.add_link(_link("spring", "spring-boot"))   # diventerà self-loop -> rimosso
    return g


def test_merges_shorter_name_sums_salience_archives():
    g = _graph_with_dupes()
    rep = g.consolidate(sim_threshold=0.85)
    assert len(rep) == 1 and rep[0]["kept"] == "spring" and rep[0]["absorbed"] == "spring-boot"
    assert g.get_node("spring-boot") is None                 # assorbito
    assert g.get_node("spring").salience == 8                # 3 + 5
    assert any(e["keyword"] == "spring-boot" for e in g._graveyard)


def test_rewires_links_and_drops_self_loops():
    g = _graph_with_dupes()
    g.consolidate(sim_threshold=0.85)
    keys = {(l.source, l.target) for l in g.links}
    assert ("spring", "docker") in keys        # riscritto da spring-boot
    assert not any(l.source == l.target for l in g.links)  # nessun self-loop


def test_protect_high_salience_prevents_merge():
    g = _graph_with_dupes()
    rep = g.consolidate(sim_threshold=0.85, protect_salience=5)  # 'spring-boot' sal 5 -> protetto
    assert rep == []
    assert g.get_node("spring-boot") is not None


def test_idempotent():
    g = _graph_with_dupes()
    g.consolidate(sim_threshold=0.85)
    rep2 = g.consolidate(sim_threshold=0.85)
    assert rep2 == []


def test_graveyard_persisted_on_save():
    g = _graph_with_dupes()
    g.consolidate(sim_threshold=0.85)
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd); os.unlink(path)
    try:
        g.save_sqlite(path, context="default")
        conn = sqlite3.connect(path)
        rows = conn.execute(
            "SELECT keyword, salience, reason FROM _graveyard").fetchall()
        conn.close()
        assert len(rows) == 1 and rows[0][0] == "spring-boot"
        assert "merged into spring" in rows[0][2]
        assert g._graveyard == []               # buffer svuotato dopo il flush
    finally:
        os.path.exists(path) and os.unlink(path)


# --- E1.3: drop orfani ------------------------------------------------------

def _graph_orphans():
    g = Graph(); g.turn_count = 20
    g.add_node(Node(keyword="keep", turn=1, topic="t", domain="d", sentiment="neutral", salience=5))
    g.add_node(Node(keyword="orphan", turn=1, topic="t", domain="d", sentiment="neutral", salience=1))
    g.add_node(Node(keyword="recent", turn=1, topic="t", domain="d", sentiment="neutral", salience=1))
    g.add_node(Node(keyword="stale", turn=1, topic="t", domain="d", sentiment="neutral", salience=1))
    for kw in ("keep", "orphan", "recent", "stale"):
        g.get_node(kw).vector = None            # niente merge, solo orfani
    fresh = _link("recent", "keep"); fresh.inactive_turns = 0
    old = _link("stale", "keep"); old.inactive_turns = 15
    g.add_link(fresh); g.add_link(old)
    return g


def test_drops_only_true_orphans():
    g = _graph_orphans()
    rep = g.consolidate(drop_orphans=True, orphan_salience=2, orphan_inactive=10)
    dropped = {r["dropped"] for r in rep if "dropped" in r}
    assert "orphan" in dropped                       # salienza bassa, nessun link
    assert "stale" in dropped                        # salienza bassa, link inattivo >=10
    assert "recent" not in dropped                   # ha un link attivo -> salvo
    assert "keep" not in dropped                     # salienza alta -> salvo
    assert g.get_node("orphan") is None and g.get_node("recent") is not None
    assert any(e["keyword"] == "orphan" for e in g._graveyard)


def test_drop_orphans_off_by_default():
    g = _graph_orphans()
    rep = g.consolidate()                            # drop_orphans=False di default
    assert rep == [] and g.get_node("orphan") is not None
