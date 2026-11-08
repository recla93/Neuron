"""E3.5 — richiamo cross-contesto in sola lettura.

I drift link nascono per OMONIMIA ESATTA fra due grafi caricati, e keyword
scritte come il tool chiede non si ripetono fra domini: su uno store reale,
191 keyword in `ai` e 144 in `default`, zero in comune. Il canale c'era e non
poteva scattare. Qui la somiglianza la decide il vettore.

Sola lettura per scelta: nessun link creato, quindi una soglia sbagliata si
corregge con una variabile d'ambiente invece che ripulendo il grafo.
"""
import neuron.search as ns


def _fake_conn(rows):
    class _C:
        def execute(self, *_a):
            class _R:
                def fetchall(_s): return rows
            return _R()
        def close(self): pass
    return _C()


def test_skips_the_active_context(tmp_path, monkeypatch):
    for name in ("graph_ai.db", "graph_veicoli.db"):
        (tmp_path / name).write_bytes(b"SQLite format 3\x00" + b"\x00" * 600)
    seen = []
    monkeypatch.setattr(ns, "_sqlite_usable", lambda p: True)
    monkeypatch.setattr(ns, "pack_vector", lambda v: b"blob")

    class _DB:
        @staticmethod
        def connect_local(path):
            seen.append(path)
            return _fake_conn([("auto elettrica", 0.63)])
    monkeypatch.setattr(ns, "_S", lambda: type("S", (), {"_db": _DB})())

    out = ns.cross_context_matches([0.1] * 384, "ai", str(tmp_path), top_n=5, threshold=0.5)
    assert all("graph_ai.db" not in p for p in seen), "il contesto attivo non va riletto"
    assert out == [("veicoli", "auto elettrica", 0.63)]


def test_a_broken_context_is_skipped_not_fatal(tmp_path, monkeypatch):
    """Motore senza vector SQL (degrado L2), file in uso, DB corrotto: il
    richiamo e' un extra, non deve poter far fallire un pre_turn."""
    for name in ("graph_a.db", "graph_b.db"):
        (tmp_path / name).write_bytes(b"SQLite format 3\x00" + b"\x00" * 600)
    monkeypatch.setattr(ns, "_sqlite_usable", lambda p: True)
    monkeypatch.setattr(ns, "pack_vector", lambda v: b"blob")

    class _DB:
        @staticmethod
        def connect_local(path):
            if path.endswith("graph_a.db"):
                raise RuntimeError("no such function: vector_distance_cos")
            return _fake_conn([("ok", 0.9)])
    monkeypatch.setattr(ns, "_S", lambda: type("S", (), {"_db": _DB})())

    out = ns.cross_context_matches([0.1] * 384, "ai", str(tmp_path), top_n=5, threshold=0.5)
    assert out == [("b", "ok", 0.9)], "il contesto sano deve rispondere lo stesso"


def test_disabled_by_env(tmp_path, monkeypatch):
    monkeypatch.setattr(ns, "CROSS_ENABLED", False)
    assert ns.cross_context_matches([0.1] * 384, "ai", str(tmp_path)) == []


def test_ranks_across_contexts_and_caps(tmp_path, monkeypatch):
    for name in ("graph_x.db", "graph_y.db"):
        (tmp_path / name).write_bytes(b"SQLite format 3\x00" + b"\x00" * 600)
    monkeypatch.setattr(ns, "_sqlite_usable", lambda p: True)
    monkeypatch.setattr(ns, "pack_vector", lambda v: b"blob")

    class _DB:
        @staticmethod
        def connect_local(path):
            return _fake_conn([("basso", 0.56)] if path.endswith("x.db") else [("alto", 0.88)])
    monkeypatch.setattr(ns, "_S", lambda: type("S", (), {"_db": _DB})())

    out = ns.cross_context_matches([0.1] * 384, "ai", str(tmp_path), top_n=1, threshold=0.5)
    assert out == [("y", "alto", 0.88)], "vince la similarita', non l'ordine dei file"


def test_only_concepts_that_hold_an_active_fact(tmp_path, monkeypatch):
    """Live 2026-11-08: `default` was 125 bare names of 127 nodes, and
    "pro e contro?" got routed there on `cosa`. A concept with no active fact
    has nothing to give after a switch, so it is not offered. Real SQL."""
    import sqlite3
    db = tmp_path / "graph_default.db"
    c = sqlite3.connect(db)
    c.executescript("""
        CREATE TABLE node_vectors (context TEXT, keyword TEXT, embedding BLOB, dim INTEGER);
        CREATE TABLE facts (context TEXT, id TEXT, status TEXT);
        CREATE TABLE fact_concepts (context TEXT, fact_id TEXT, keyword TEXT);
        INSERT INTO node_vectors VALUES ('default','cosa',x'00',1), ('default','venv',x'00',1),
                                        ('default','vecchio',x'00',1);
        INSERT INTO facts VALUES ('default','f1','active'), ('default','f2','superseded');
        INSERT INTO fact_concepts VALUES ('default','f1','venv'), ('default','f2','vecchio');
    """)
    c.commit(); c.close()
    monkeypatch.setattr(ns, "_sqlite_usable", lambda p: True)
    monkeypatch.setattr(ns, "pack_vector", lambda v: b"blob")

    class _DB:
        @staticmethod
        def connect_local(path):
            conn = sqlite3.connect(path)
            conn.create_function("vector_distance_cos", 2, lambda a, b: 0.1)
            return conn
    monkeypatch.setattr(ns, "_S", lambda: type("S", (), {"_db": _DB})())

    out = ns.cross_context_matches([0.1] * 384, "ai", str(tmp_path), top_n=5, threshold=0.5)
    assert out == [("default", "venv", 0.9)], out


if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-q"]))
