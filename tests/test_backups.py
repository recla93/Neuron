"""Backup a rotazione dei grafi locali, e il guard sul consolidate (2026-09-12).

Un `consolidate` con drop_orphans ha archiviato 255 nodi su 296, e il graveyard
"recuperabile" teneva solo keyword/salience/dominio. Da qui: una copia al giorno
alla prima apertura (ne restano 5), una copia prima di ogni drop, e un rifiuto
sopra il 20% del grafo senza conferma esplicita.
"""
from __future__ import annotations

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _graph_file(path: str, rows: int = 3) -> None:
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE nodes (context TEXT, keyword TEXT)")
    c.executemany("INSERT INTO nodes VALUES ('ai', ?)", [(f"k{i}",) for i in range(rows)])
    c.commit()
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("INSERT INTO nodes VALUES ('ai', 'in-wal')")
    c.commit()
    # sqlite3 puro: alla close fa checkpoint. Qui va bene — e' il file di test.
    c.close()
    # pad: sotto SQLITE_MIN_VALID_SIZE lo snapshot non parte
    assert os.path.getsize(path) >= 512


def test_daily_snapshot_once_a_day_and_pruned_to_keep(tmp_path, monkeypatch):
    from neuron import db
    path = str(tmp_path / "graph_ai.db")
    _graph_file(path)
    bdir = tmp_path / "_backups"

    first = db.snapshot(path, keep=5)
    assert first and first.endswith(".db") and os.path.exists(first)
    assert db.snapshot(path, keep=5) is None, "una al giorno: la seconda apertura non copia"
    # la copia e' un DB autonomo e leggibile
    c = sqlite3.connect(first)
    assert c.execute("select count(*) from nodes").fetchone()[0] == 4
    assert c.execute("pragma journal_mode").fetchone()[0] == "delete"
    c.close()

    # sette giorni finti -> restano i 5 piu' recenti
    for d in ("2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-05", "2026-09-06"):
        (bdir / f"graph_ai.{d}.db").write_bytes(b"x")
    monkeypatch.setattr(db._time, "strftime", lambda *_a: "2026-09-07")
    db.snapshot(path, keep=5)
    kept = sorted(os.listdir(bdir))
    assert len(kept) == 5, kept                       # solo .db: niente -wal/-shm accanto
    assert "graph_ai.2026-09-07.db" in kept and "graph_ai.2026-09-01.db" not in kept, kept


def test_tagged_snapshot_is_overwritten_and_never_pruned(tmp_path):
    from neuron import db
    path = str(tmp_path / "graph_ai.db")
    _graph_file(path)
    a = db.snapshot(path, tag="pre-consolidate")
    b = db.snapshot(path, tag="pre-consolidate")
    assert a == b and a.endswith("graph_ai.pre-consolidate.db")


def test_snapshot_leaves_a_live_wal_alone(tmp_path):
    """La copia passa da una connessione read-only: i frame del WAL entrano
    nella copia, il WAL del worker resta dov'e' (vedi test_second_opener)."""
    turso = pytest.importorskip("turso")
    from neuron import db
    path = str(tmp_path / "graph_ai.db")
    live = turso.connect(path)
    live.execute("CREATE TABLE nodes (context TEXT, keyword TEXT)")
    live.executemany("INSERT INTO nodes VALUES ('ai', ?)", [(f"k{i}",) for i in range(200)])
    live.commit()
    wal = path + "-wal"
    before = os.path.getsize(wal)
    out = db.snapshot(path, keep=5)
    assert os.path.getsize(wal) == before, "il WAL vivo non si tocca"
    assert sqlite3.connect(out).execute("select count(*) from nodes").fetchone()[0] == 200, \
        "la copia contiene anche cio' che stava solo nel WAL"
    live.close()


def test_disabled_missing_or_remote_means_no_backup(tmp_path, monkeypatch):
    from neuron import db
    assert db.snapshot(str(tmp_path / "nope.db")) is None
    path = str(tmp_path / "graph_ai.db"); _graph_file(path)
    assert db.snapshot(path, keep=0) is None
    monkeypatch.setattr(db, "REMOTE_TURSO", True)
    assert db.snapshot(path) is None


def test_registry_takes_the_daily_backup_on_first_load(tmp_path):
    from neuron.registry import GraphRegistry
    from neuron.models import Node
    reg = GraphRegistry(str(tmp_path))
    g = reg.get("ai")
    g.add_node(Node(keyword="x", turn=1, topic="t", domain="ai", sentiment="neutral", salience=1))
    reg.save("ai")
    assert not (tmp_path / "_backups").exists(), "nessuna copia di un file che non c'era"
    reg2 = GraphRegistry(str(tmp_path))
    reg2.get("ai")                                   # prima apertura: copia
    assert any(f.startswith("graph_ai.20") for f in os.listdir(tmp_path / "_backups"))


# ---------- il guard sul drop ----------

def _big_graph(n: int):
    from neuron.models import Graph, Node
    g = Graph(); g.turn_count = 50
    for i in range(n):
        g.add_node(Node(keyword=f"k{i}", turn=1, topic="t", domain="ai", sentiment="neutral", salience=0))
        g.get_node(f"k{i}").vector = None            # niente merge
    return g


def test_a_mass_drop_is_refused_and_says_so():
    g = _big_graph(50)
    rep = g.consolidate(drop_orphans=True)           # max_drop_fraction=0.2 di default
    assert len(g.nodes) == 50, "nessun nodo archiviato"
    (refused,) = [r for r in rep if "refused_drop" in r]
    assert refused["refused_drop"] == 50 and refused["of"] == 50
    assert "confirm_mass_drop" in refused["reason"]


def test_a_confirmed_mass_drop_goes_through():
    g = _big_graph(50)
    g.consolidate(drop_orphans=True, max_drop_fraction=None)
    assert len(g.nodes) == 0 and len(g._graveyard) == 50


def test_a_small_drop_on_a_small_graph_is_not_a_mass_drop():
    """Sotto i 10 nodi il 20% non ha senso: il floor assoluto lascia passare."""
    g = _big_graph(8)
    g.consolidate(drop_orphans=True)
    assert len(g.nodes) == 0
