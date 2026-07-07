"""E0.2 — guard di coerenza modello<->store in save/load_sqlite.

Vettori salvati con un modello diverso da quello attivo NON vanno confrontati
(spazi diversi): load_sqlite li ignora e li ricalcola col modello attivo, e
save_sqlite registra il modello nei meta. Puro (neuron.models + sqlite).
"""
import io
import os
import sqlite3
import sys
import tempfile
from contextlib import redirect_stderr

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import neuron.models as m


def _tmp():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd); os.unlink(path)
    return path


def _save_with_model(model, sentinel):
    m.EMBED_MODEL = model
    g = m.Graph(); g.turn_count = 1
    g.add_node(m.Node(keyword="alpha", turn=1, topic="t", domain="d", sentiment="neutral"))
    g.nodes[0].vector = [sentinel] * m.VECTOR_DIM
    g._dirty_vectors.add("alpha")
    path = _tmp()
    g.save_sqlite(path, context="default")
    return path


def test_save_writes_embed_model_meta():
    path = _save_with_model("model-A", 9.9)
    try:
        meta = dict(sqlite3.connect(path).execute("SELECT key, value FROM meta").fetchall())
        assert meta["embed_model"] == "model-A"
        assert meta["embed_dim"] == str(m.VECTOR_DIM)
    finally:
        m.EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
        os.unlink(path)


def test_mismatch_ignores_stored_vectors_and_warns():
    path = _save_with_model("model-A", 9.9)
    try:
        m.EMBED_MODEL = "model-B"                       # modello attivo diverso
        g = m.Graph()
        buf = io.StringIO()
        with redirect_stderr(buf):
            g.load_sqlite(path, context="default")
        warn = buf.getvalue()
        assert "reembed" in warn and "model-A" in warn   # avviso presente
        # vettore sentinella (9.9) IGNORATO -> ricalcolato (embed_fn assente => zeri)
        assert abs(g.nodes[0].vector[0] - 9.9) > 0.5
    finally:
        m.EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
        os.unlink(path)


def test_match_loads_stored_vectors():
    path = _save_with_model("model-A", 9.9)
    try:
        m.EMBED_MODEL = "model-A"                        # stesso modello
        g = m.Graph()
        buf = io.StringIO()
        with redirect_stderr(buf):
            g.load_sqlite(path, context="default")
        assert buf.getvalue() == ""                      # nessun avviso
        assert abs(g.nodes[0].vector[0] - 9.9) < 0.01    # vettore salvato caricato
    finally:
        m.EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
        os.unlink(path)
