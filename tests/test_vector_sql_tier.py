"""Il tier SQL vettoriale deve girare davvero, non cadere sempre nel fallback.

Regressione: la query usava ``f32blob(...)``, funzione che nessun engine
libSQL/pyturso espone. Ogni chiamata sollevava "no such function", veniva
inghiottita da un ``except`` con solo ``log.debug``, e il seed veniva chiuso e
riaperto al giro dopo — ~310 ms per chiamata con il file conteso, ~1.5 s/turno.
"""
import os
import sqlite3
import struct
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


def _make_store(path: str, dim: int) -> None:
    c = sqlite3.connect(path)
    c.execute(
        "CREATE TABLE node_vectors (context TEXT NOT NULL DEFAULT 'default', "
        "keyword TEXT NOT NULL, embedding BLOB NOT NULL, dim INTEGER NOT NULL, "
        "PRIMARY KEY (context, keyword))"
    )
    c.execute(
        "CREATE TABLE nodes (id INTEGER PRIMARY KEY AUTOINCREMENT, context TEXT DEFAULT 'default', "
        "keyword TEXT, turn INTEGER, topic TEXT, domain TEXT, sentiment TEXT, salience INTEGER, "
        "entities TEXT DEFAULT '[]', tags TEXT DEFAULT '[]', refs TEXT DEFAULT '[]')"
    )
    vec = [1.0] + [0.0] * (dim - 1)
    c.execute(
        "INSERT INTO node_vectors VALUES ('default', 'alpha', ?, ?)",
        (struct.pack(f"{dim}f", *vec), dim),
    )
    c.execute(
        "INSERT INTO nodes (context, keyword, turn, topic, domain, sentiment, salience) "
        "VALUES ('default', 'alpha', 1, 't', 'backend', 'neutral', 3)"
    )
    c.commit()
    c.close()


def test_sql_tier_runs_and_does_not_latch_off(tmp_path, monkeypatch):
    """Con un engine vettoriale presente, una ricerca reale NON deve degradare."""
    pytest.importorskip("mcp")
    pytest.importorskip("turso")
    import neuron.server as srv
    from neuron.models import Graph

    if not srv.TURSO_ENGINE:
        pytest.skip("nessun engine Turso/libSQL: il tier SQL non è applicabile")

    store = str(tmp_path / "graph_default.db")
    _make_store(store, srv.VECTOR_DIM)

    monkeypatch.setattr(srv, "_active_db_path", lambda: store)
    monkeypatch.setattr(srv, "_vector_sql_ok", True)
    monkeypatch.setattr(srv, "_get_embedding", lambda t: [1.0] + [0.0] * (srv.VECTOR_DIM - 1))
    srv._turn_search_cache.clear()

    rows = srv._search_embeddings(["alpha"], top_n=5, graph=Graph())

    # Il grafo è vuoto: se questi risultati esistono arrivano dal tier SQL.
    assert rows == [("alpha", 1.0)], f"il tier SQL non ha prodotto risultati: {rows}"
    assert srv._vector_sql_ok, "il tier SQL è degradato: la query non gira sull'engine"
