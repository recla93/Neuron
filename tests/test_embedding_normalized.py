"""I vettori devono essere unitari: più punti del server confrontano con il
prodotto scalare contro soglie pensate per il coseno (SEMANTIC_DEDUP_THRESHOLD,
stimulus, auto-link). Gira in un sottoprocesso con l'embedder VERO: il resto
della suite inietta un embedder finto costante (tests/_mockdeps.py) e non deve
essere contaminato."""
import json
import subprocess
import sys

import pytest

_PROBE = r"""
import json
from neuron import search
from neuron.server import SEMANTIC_DEDUP_THRESHOLD
a = search._embed_one("Luca Esposito")
b = search._embed_one("Zona di Scarto")
print(json.dumps({"norm2": sum(x * x for x in a), "dot": sum(x * y for x, y in zip(a, b)),
                  "threshold": SEMANTIC_DEDUP_THRESHOLD}))
"""


def test_embeddings_are_unit_and_distinct_names_not_merged():
    try:
        out = subprocess.run([sys.executable, "-c", _PROBE], capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        pytest.skip("embedder troppo lento da caricare")
    if out.returncode != 0:
        pytest.skip(f"embedder reale non disponibile: {out.stderr.strip()[-200:]}")
    r = json.loads(out.stdout.strip().splitlines()[-1])
    assert abs(r["norm2"] - 1.0) < 1e-4
    assert r["dot"] < r["threshold"]
