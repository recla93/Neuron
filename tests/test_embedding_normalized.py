"""I vettori devono essere unitari: più punti del server confrontano con il
prodotto scalare contro soglie pensate per il coseno (SEMANTIC_DEDUP_THRESHOLD,
stimulus, auto-link).

Due prove. Quella unitaria gira sempre, con un embedder finto dalla norma 5: è la
guardia della normalizzazione. Quella col modello VERO gira in un sottoprocesso,
perché il resto della suite inietta un embedder finto costante
(tests/_mockdeps.py) che non va contaminato; si salta se il modello non c'è."""
import json
import os
import pathlib
import subprocess
import sys

import pytest

_SRC = pathlib.Path(__file__).resolve().parent.parent / "src"

_PROBE = r"""
import json
from neuron import search
from neuron.server import SEMANTIC_DEDUP_THRESHOLD
a = search._embed_one("Luca Esposito")
b = search._embed_one("Zona di Scarto")
print(json.dumps({"norm2": sum(x * x for x in a), "dot": sum(x * y for x, y in zip(a, b)),
                  "threshold": SEMANTIC_DEDUP_THRESHOLD}))
"""


def test_embed_one_returns_a_unit_vector(monkeypatch):
    pytest.importorskip("mcp")
    pytest.importorskip("fastembed")
    import neuron.server as srv
    from neuron import search

    class _LongEmbedder:
        def embed(self, texts):
            return [[3.0, 4.0] + [0.0] * (srv.VECTOR_DIM - 2) for _ in texts]

    monkeypatch.setattr(srv, "_embedder", _LongEmbedder())
    monkeypatch.setattr(srv, "_embed_dim_checked", True)
    v = search._embed_one("qualunque")
    assert v[:2] == pytest.approx([0.6, 0.8])  # stessa direzione, norma 1


def test_embeddings_are_unit_and_distinct_names_not_merged():
    # Il sottoprocesso deve importare il sorgente sotto test, non il neuron
    # installato: un'installazione editable può puntare a un altro checkout.
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(
        p for p in (str(_SRC), os.environ.get("PYTHONPATH", "")) if p)}
    try:
        out = subprocess.run([sys.executable, "-c", _PROBE], capture_output=True,
                             text=True, timeout=300, env=env)
    except subprocess.TimeoutExpired:
        pytest.skip("embedder troppo lento da caricare")
    if out.returncode != 0:
        pytest.skip(f"embedder reale non disponibile: {out.stderr.strip()[-200:]}")
    r = json.loads(out.stdout.strip().splitlines()[-1])
    assert abs(r["norm2"] - 1.0) < 1e-4
    assert r["dot"] < r["threshold"]
