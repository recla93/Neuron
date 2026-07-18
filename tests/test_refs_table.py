"""G2 — refs come tabella propria (context, keyword, path, project_id, by).

Anti-clobber: due writer che aggiungono ref allo stesso nodo producono righe
diverse (INSERT OR IGNORE su chiave naturale), non un read-modify-write sul
blob JSON. Il blob nodes.refs resta legacy: letto in union, mai riscritto dal
path atomico.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


def _node(kw, refs=None):
    from neuron.models import Node
    return Node(keyword=kw, turn=1, topic="t", domain="d", sentiment="neutral",
                references=refs)


def _ref(path, pid="P1", by="claudio"):
    return {"path": path, "project_id": pid, "by": by}


def test_refs_roundtrip_table(tmp_path):
    from neuron.models import Graph
    p = str(tmp_path / "g.db")
    g = Graph(); g.turn_count = 1
    g.add_node(_node("kafka", [_ref("src/a.py")]))
    g._dirty = True
    g.save_sqlite(p, force=True)
    g2 = Graph(); g2.load_sqlite(p)
    assert _ref("src/a.py") in g2.get_node("kafka").references


def test_two_writers_no_clobber(tmp_path):
    """Writer concorrenti su nodo condiviso: entrambi i ref sopravvivono."""
    from neuron.models import Graph
    p = str(tmp_path / "shared.db")
    g = Graph(); g.turn_count = 1
    g.add_node(_node("kafka"))
    g._dirty = True
    g.save_sqlite(p, force=True)

    w1 = Graph(); w1.load_sqlite(p)
    w2 = Graph(); w2.load_sqlite(p)   # caricato PRIMA del save di w1 (stale)
    w1.get_node("kafka").references = [_ref("src/w1.py", by="alice")]
    w1.mark_node_dirty("kafka"); w1._dirty = True
    w1.save_sqlite(p, force=True)
    w2.get_node("kafka").references = [_ref("src/w2.py", by="bob")]
    w2.mark_node_dirty("kafka"); w2._dirty = True
    w2.save_sqlite(p, force=True)

    g3 = Graph(); g3.load_sqlite(p)
    paths = {r["path"] for r in g3.get_node("kafka").references}
    assert {"src/w1.py", "src/w2.py"} <= paths   # nessun update perso


def test_same_logical_file_merges(tmp_path):
    """Stesso (path, project_id, by) da due save = una riga sola."""
    from neuron.models import Graph
    p = str(tmp_path / "g.db")
    for _ in range(2):
        g = Graph()
        if os.path.exists(p):
            g.load_sqlite(p)
        else:
            g.add_node(_node("kafka"))
        g.get_node("kafka").references = [_ref("src/a.py")]
        g.mark_node_dirty("kafka"); g._dirty = True
        g.save_sqlite(p, force=True)
    g2 = Graph(); g2.load_sqlite(p)
    assert len([r for r in g2.get_node("kafka").references
                if r["path"] == "src/a.py"]) == 1


def test_removed_node_drops_its_refs(tmp_path):
    from neuron.models import Graph
    import neuron.db as _db
    p = str(tmp_path / "g.db")
    g = Graph(); g.turn_count = 1
    g.add_node(_node("kafka", [_ref("src/a.py")]))
    g._dirty = True
    g.save_sqlite(p, force=True)
    g.nodes = [nd for nd in g.nodes if nd.keyword != "kafka"]
    g._rebuild_node_map(); g._record_removed_node("kafka")
    g._dirty = True
    g.save_sqlite(p, force=True)
    conn = _db.connect(p)
    n = conn.execute("SELECT COUNT(*) FROM refs WHERE keyword='kafka'").fetchone()[0]
    conn.close()
    assert n == 0


def test_legacy_blob_still_loads(tmp_path):
    """Store con soli refs nel blob JSON (pre-G2): union li conserva."""
    import json
    import sqlite3
    from neuron.models import Graph
    p = str(tmp_path / "old.db")
    conn = sqlite3.connect(p)
    conn.executescript(
        "CREATE TABLE nodes (id INTEGER PRIMARY KEY AUTOINCREMENT, keyword TEXT, "
        "turn INTEGER, topic TEXT, domain TEXT, sentiment TEXT, salience INTEGER, "
        "entities TEXT DEFAULT '[]', tags TEXT DEFAULT '[]', refs TEXT DEFAULT '[]');"
        "CREATE TABLE links (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, "
        "target TEXT, link_type TEXT, weight TEXT, rationale TEXT, created_turn INTEGER, "
        "last_active_turn INTEGER, inactive_turns INTEGER);"
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);")
    conn.execute("INSERT INTO nodes (keyword, turn, topic, domain, sentiment, salience, refs) "
                 "VALUES ('old', 1, 't', 'd', 'neutral', 1, ?)",
                 (json.dumps([_ref("legacy.py")]),))
    conn.commit(); conn.close()
    g = Graph(); g.load_sqlite(p)
    assert _ref("legacy.py") in g.get_node("old").references
