"""Facts (ADR-015): the unit of memory, indexed by concepts — sqlite stdlib tier.

Until 6.9 a fact (`episode`) lived inside its node and died with it, four ways:
the cap of 5 per node, the eviction past MAX_NODES, a merge, an orphan drop.
Each has its test here, through a real save and load: the store is the memory.
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from neuron import models  # noqa: E402
from neuron.models import FACT_MAX_CHARS, Graph, Node  # noqa: E402


def _node(kw, turn=1, salience=1):
    return Node(keyword=kw, turn=turn, topic="t", domain="general",
                sentiment="neutral", salience=salience)


def _graph(*kws):
    g = Graph()
    for kw in kws:
        g.add_node(_node(kw))
    return g


def _reload(g, path):
    g.save_sqlite(path)
    g2 = Graph()
    g2.load_sqlite(path)
    return g2


def _texts(g):
    return {f["text"] for f in g.facts.values()}


# --- the write -------------------------------------------------------------

def test_add_and_recent():
    g = _graph("retry backoff")
    assert g.add_episode("retry backoff", "chose https over wss", turn=3)
    assert g.add_episode("retry backoff", "backoff base 0.5s", turn=5)
    assert g.recent_episodes("retry backoff", 2) == ["backoff base 0.5s", "chose https over wss"]


def test_the_legacy_call_requires_an_existing_node_and_text():
    g = Graph()
    assert not g.add_episode("ghost", "fact", turn=1)
    g.add_node(_node("real"))
    assert not g.add_episode("real", "   ", turn=1)


def test_two_facts_of_the_same_turn_are_both_kept():
    """It used to be one episode per (keyword, turn), the second overwriting."""
    g = _graph("a")
    g.add_fact("decision", "v1", ["a"], 2)
    g.add_fact("fix", "v2", ["a"], 2)
    assert sorted(g.recent_episodes("a")) == ["v1", "v2"]


def test_a_fact_is_kept_whole_and_cut_only_at_the_trust_boundary():
    g = _graph("a")
    rep = g.add_fact("fact", "corto", ["a"], 1)
    assert rep["stored"] and rep["id"] and "truncated" not in rep
    long = "x" * 900                         # past the advisory 400: kept whole
    assert "truncated" not in g.add_fact("fact", long, ["a"], 2)
    assert long in g.recent_episodes("a", 5)
    words = ("parola " * FACT_MAX_CHARS).strip()
    cut = g.add_fact("fact", words, ["a"], 3)
    assert cut["truncated"] == len(words) - FACT_MAX_CHARS
    stored = g.facts[cut["id"]]["text"]
    assert len(stored) <= FACT_MAX_CHARS and stored.endswith("parola")


def test_the_same_text_again_is_the_same_fact():
    """A retried store_turn must not make a twin: it adds the new concepts."""
    g = _graph("a", "b")
    first = g.add_fact("fix", "il WAL resta bloccato con pyturso 0.6.1", ["a"], 1)
    again = g.add_fact("fix", "il WAL resta bloccato con pyturso 0.6.1", ["b"], 2)
    assert again["id"] == first["id"] and again["duplicate"]
    assert len(g.facts) == 1 and g.facts[first["id"]]["concepts"] == ["a", "b"]


def test_an_unknown_kind_is_stored_as_fact():
    g = _graph("a")
    rep = g.add_fact("ricordo", "testo", ["a"], 1)
    assert g.facts[rep["id"]]["kind"] == "fact"


# --- supersede: an update, not a delete --------------------------------------

def test_a_superseding_fact_takes_the_place_of_the_old_one(tmp_path):
    g = _graph("backup")
    old = g.add_fact("progress", "la memoria e' solo su questo PC", ["backup"], 1)
    new = g.add_fact("progress", "c'e' una copia della memoria sul disco esterno",
                     ["backup"], 2, supersedes=old["id"])
    assert new["superseded"] == old["id"]
    g2 = _reload(g, str(tmp_path / "g.db"))
    assert g2.recent_episodes("backup", 5) == ["c'e' una copia della memoria sul disco esterno"]
    assert g2.facts[old["id"]]["status"] == "superseded", "the old fact stays on disk"
    assert g2.facts[new["id"]]["supersedes"] == old["id"]


def test_superseding_an_unknown_id_is_reported_not_raised():
    g = _graph("a")
    rep = g.add_fact("fact", "testo", ["a"], 1, supersedes="deadbeef")
    assert rep["stored"] and rep["supersedes_unknown"] == "deadbeef"


# --- the four ways a fact was lost (ADR-015 §1.3) -----------------------------

def test_1_no_cap_per_node(tmp_path):
    g = _graph("release")
    for t in range(1, 13):
        g.add_fact("progress", f"passo {t} della release", ["release"], t)
    g2 = _reload(g, str(tmp_path / "g.db"))
    assert len(g2.facts) == 12, "the cap of 5 dropped the oldest seven"


def test_2_an_evicted_node_keeps_its_facts(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "MAX_NODES", 3)
    g = Graph()
    g.add_node(_node("wal", salience=0))           # lowest salience: evicted first
    g.add_fact("fix", "pyturso 0.6.1 tiene il WAL bloccato", ["wal"], 1)
    for kw in ("a", "b", "c"):
        g.add_node(_node(kw, salience=5))
    assert g.get_node("wal") is None, "the setup must evict 'wal'"
    path = str(tmp_path / "g.db")
    g2 = _reload(g, path)
    assert "pyturso 0.6.1 tiene il WAL bloccato" in _texts(g2)
    # the concept comes back and finds its fact again
    g2.add_node(_node("wal"))
    assert g2.recent_episodes("wal") == ["pyturso 0.6.1 tiene il WAL bloccato"]


def test_3_a_merge_moves_the_facts_to_the_survivor(tmp_path):
    g = _graph("turso", "pyturso")
    g.add_fact("decision", "Turso anche in locale, cloud facoltativo", ["pyturso"], 1)
    path = str(tmp_path / "g.db")
    g.save_sqlite(path)
    g._merge_into(g.get_node("turso"), g.get_node("pyturso"), 0.9, 2, [])
    g.mark_full_rewrite()                          # what consolidate does: the reconcile save
    g2 = _reload(g, path)
    assert g2.recent_episodes("turso") == ["Turso anche in locale, cloud facoltativo"]
    assert g2.recent_episodes("pyturso") == []
    with sqlite3.connect(path) as c:
        rows = c.execute("SELECT keyword FROM fact_concepts").fetchall()
    assert rows == [("turso",)]


def test_4_an_orphan_drop_keeps_the_facts(tmp_path):
    g = _graph("orfano", "altro")
    g.add_fact("idea", "eventi in stile aspect per gli stimoli", ["orfano"], 1)
    path = str(tmp_path / "g.db")
    g.save_sqlite(path)
    report: list = []
    g._drop_orphans(5, 0, 2, report)
    g.mark_full_rewrite()
    assert g.get_node("orfano") is None, "the setup must drop 'orfano'"
    g2 = _reload(g, path)
    assert "eventi in stile aspect per gli stimoli" in _texts(g2)


def test_a_removed_node_keeps_its_fact_rows(tmp_path):
    path = str(tmp_path / "g.db")
    g = _graph("a", "b")
    g.add_fact("fact", "fact a", ["a"], 1)
    g.save_sqlite(path)
    g.nodes = [nd for nd in g.nodes if nd.keyword != "a"]
    g._rebuild_node_map()
    g._removed_nodes.add("a")
    g._dirty = True
    g2 = _reload(g, path)
    assert g2.recent_episodes("a") == ["fact a"]


# --- persistence and migration -------------------------------------------------

def test_roundtrip_sqlite(tmp_path):
    path = str(tmp_path / "g.db")
    g = _graph("retry backoff", "salience")
    g.turn_count = 4
    g.add_episode("retry backoff", "chose https over wss", turn=4)
    g.save_sqlite(path)
    g.turn_count = 5                                # incremental save of a later fact
    g.add_episode("retry backoff", "backoff base 0.5s", turn=5)
    g2 = _reload(g, path)
    assert g2.recent_episodes("retry backoff", 2) == ["backoff base 0.5s", "chose https over wss"]
    assert g2.recent_episodes("salience") == []


def test_legacy_episodes_become_one_fact_per_text(tmp_path):
    """Before 6.10 store_turn copied the sentence onto every keyword: one row
    per keyword, one fact in truth. Loading twice never makes a twin."""
    path = str(tmp_path / "g.db")
    _graph("ngrx", "effect", "safari").save_sqlite(path)
    with sqlite3.connect(path) as c:
        c.executemany("INSERT INTO episodes (context, keyword, turn, text) VALUES ('default',?,?,?)",
                      [("ngrx", 3, "gli errori HTTP si gestiscono negli effect"),
                       ("effect", 3, "gli errori HTTP si gestiscono negli effect"),
                       ("safari", 7, "il p-dropdown perde il focus: appendTo body")])
    g = Graph()
    g.load_sqlite(path)
    by_text = {f["text"]: f for f in g.facts.values()}
    assert len(by_text) == 2
    http = by_text["gli errori HTTP si gestiscono negli effect"]
    assert sorted(http["concepts"]) == ["effect", "ngrx"]
    assert (http["kind"], http["source"], http["turn"]) == ("fact", "migration", 3)
    g.save_sqlite(path)
    g3 = Graph()
    g3.load_sqlite(path)
    assert len(g3.facts) == 2 and not g3._new_facts, "a second load re-migrated"
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 3, \
            "the legacy table is read, never rewritten"


# --- ADR-015 D8: the use of a fact is counted on the fact -----------------

def test_confirmations_persist_and_a_store_without_the_column_migrates(tmp_path):
    path = str(tmp_path / "g.db")
    g = _graph("wal")
    f = g.add_fact("fix", "pyturso 0.6.1 keeps the WAL locked", ["wal"], 1)
    g.save_sqlite(path)
    with sqlite3.connect(path) as c:             # a store written before D8
        c.execute("ALTER TABLE facts DROP COLUMN confirmed")
    g2 = Graph(); g2.load_sqlite(path)
    assert g2.facts[f["id"]]["confirmed"] == 0
    g2.confirm_fact(f["id"]); g2.confirm_fact("#" + f["id"])
    assert _reload(g2, path).facts[f["id"]]["confirmed"] == 2


def test_a_superseding_fact_carries_the_uses_of_the_old_one():
    g = _graph("backup")
    old = g.add_fact("progress", "memory only on this PC", ["backup"], 1)
    g.confirm_fact(old["id"], 3)
    new = g.add_fact("progress", "a copy on the external disk", ["backup"], 2, supersedes=old["id"])
    assert g.facts[new["id"]]["confirmed"] == 4          # 3 + superseding is a use
    assert not g.confirm_fact("nope") and g.confirm_fact(old["id"], -9)
    assert g.facts[old["id"]]["confirmed"] == 0          # a refute is floored at 0


def test_upgrade_all_migrates_a_context_nobody_opened(tmp_path):
    """An old graph file used to migrate only when its context was opened, and
    cross-context recall skipped it meanwhile. upgrade_all does it at start."""
    from neuron.registry import GraphRegistry
    g = _graph("wal")
    g.add_fact("fix", "pyturso 0.6.1 keeps the WAL locked", ["wal"], 1)
    path = tmp_path / "graph_old.db"
    g.save_sqlite(str(path))
    with sqlite3.connect(path) as c:              # as a 6.9 store left it
        c.execute("ALTER TABLE facts DROP COLUMN confirmed")
    reg = GraphRegistry(graphs_dir=str(tmp_path))
    assert reg._needs_upgrade(str(path))
    assert reg.upgrade_all() == ["old"]
    assert not reg._needs_upgrade(str(path)) and reg.upgrade_all() == []
