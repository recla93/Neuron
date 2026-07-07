"""EX.2 — test della logica pura del benchmark embedding (no fastembed).

Copre load_fixture + evaluate (recall@k, MRR, per-lingua) con un embedder finto
deterministico, così gira ovunque (anche senza fastembed/mcp).
"""
import importlib.util
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_module():
    path = os.path.join(_ROOT, "scripts", "bench_embed.py")
    spec = importlib.util.spec_from_file_location("bench_embed", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


be = _load_module()


def test_load_fixture_shape():
    docs, queries = be.load_fixture(
        os.path.join(_ROOT, "tests", "fixtures", "bench_pairs_en_it.jsonl"))
    assert len(docs) >= 10 and len(queries) >= 10
    assert all("id" in d and "text" in d for d in docs)
    assert all("target" in q and "lang" in q for q in queries)
    langs = {q["lang"] for q in queries}
    assert "it" in langs and "en" in langs  # deve testare il cross-lingua


def _onehot(vocab, text):
    v = [0.0] * len(vocab)
    for i, tok in enumerate(vocab):
        if tok in text:
            v[i] = 1.0
    return v


def test_evaluate_perfect_recall():
    # ogni doc/query condivide un token unico -> match perfetto
    docs = [{"id": "a", "text": "alpha"}, {"id": "b", "text": "beta"},
            {"id": "c", "text": "gamma"}]
    queries = [{"text": "alpha", "target": "a", "lang": "en"},
               {"text": "beta", "target": "b", "lang": "it"}]
    vocab = ["alpha", "beta", "gamma"]
    m = be.evaluate(lambda t: _onehot(vocab, t), docs, queries, k=1)
    assert m["recall_at_k"] == 1.0
    assert m["mrr"] == 1.0
    assert m["per_lang_recall"] == {"en": 1.0, "it": 1.0}


def test_evaluate_miss_lowers_recall_and_mrr():
    docs = [{"id": "a", "text": "alpha"}, {"id": "b", "text": "beta"}]
    # la query punta a 'b' ma il testo somiglia ad 'a' -> target al rank 2
    queries = [{"text": "alpha", "target": "b", "lang": "it"}]
    vocab = ["alpha", "beta"]
    m = be.evaluate(lambda t: _onehot(vocab, t), docs, queries, k=1)
    assert m["recall_at_k"] == 0.0           # non entra nel top-1
    assert 0.0 < m["mrr"] <= 0.5             # rank 2 -> rr 0.5
    m2 = be.evaluate(lambda t: _onehot(vocab, t), docs, queries, k=2)
    assert m2["recall_at_k"] == 1.0          # con k=2 il target rientra
