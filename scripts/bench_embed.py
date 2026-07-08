"""EX.2 — Benchmark degli embedding per Neuron (EN/IT).

Misura, per uno o più modelli fastembed, la qualità del retrieval cross-lingua e il
costo, così da decidere tra le opzioni di ADR-001 (A = modello multilingua unico,
A2 = pivot LLM a keyword inglesi) con i NUMERI e non a naso.

Metriche per modello:
  - recall@k complessivo e per lingua (query IT che deve recuperare il doc giusto,
    anche se il doc è in inglese → misura il cross-lingua);
  - MRR (mean reciprocal rank);
  - tempo medio per embedding (ms);
  - RSS di picco del processo (MB) → il "peso" in RAM del modello.

Uso:
    python scripts/bench_embed.py \
        --models sentence-transformers/all-MiniLM-L6-v2 intfloat/multilingual-e5-small \
        --k 3

La fixture di default è tests/fixtures/bench_pairs_en_it.jsonl (righe JSON con
{"type":"doc",...} e {"type":"query",...}). Nessuna rete oltre al download del modello.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

DEFAULT_FIXTURE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tests", "fixtures", "bench_pairs_en_it.jsonl",
)
DEFAULT_MODELS = [
    "sentence-transformers/all-MiniLM-L6-v2",       # Opzione A2 baseline (EN)
    "intfloat/multilingual-e5-small",               # Opzione A (multilingua)
]


# ---------------------------------------------------------------------------
# Pure logic (no fastembed) — unit-testable
# ---------------------------------------------------------------------------

def load_fixture(path: str) -> tuple[list[dict], list[dict]]:
    docs, queries = [], []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            (docs if row.get("type") == "doc" else queries).append(row)
    return docs, queries


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def evaluate(embed_fn, docs: list[dict], queries: list[dict], k: int = 3) -> dict:
    """Embed docs+queries via embed_fn(text)->vector and score retrieval.

    Returns overall/per-language recall@k and MRR. Pure: swap embed_fn for a fake
    in tests. embed_fn receives a text and returns a list[float]."""
    doc_vecs = {d["id"]: embed_fn(d["text"]) for d in docs}
    doc_ids = list(doc_vecs)

    hits = 0
    rr_sum = 0.0
    per_lang: dict[str, list[int]] = {}
    for q in queries:
        qv = embed_fn(q["text"])
        ranked = sorted(doc_ids, key=lambda did: _cosine(qv, doc_vecs[did]), reverse=True)
        target = q["target"]
        rank = ranked.index(target) + 1 if target in ranked else None
        hit = 1 if (rank is not None and rank <= k) else 0
        hits += hit
        rr_sum += (1.0 / rank) if rank else 0.0
        per_lang.setdefault(q.get("lang", "?"), []).append(hit)

    n = len(queries) or 1
    return {
        "recall_at_k": hits / n,
        "mrr": rr_sum / n,
        "n_queries": len(queries),
        "per_lang_recall": {lg: (sum(v) / len(v)) for lg, v in per_lang.items()},
    }


# ---------------------------------------------------------------------------
# fastembed wiring + resource measurement
# ---------------------------------------------------------------------------

def _rss_mb() -> float:
    try:
        import resource
        # ru_maxrss: KB on Linux, bytes on macOS
        val = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return val / 1024 if sys.platform != "darwin" else val / (1024 * 1024)
    except Exception:
        return float("nan")


def bench_model(model_name: str, docs, queries, k: int) -> dict:
    from fastembed import TextEmbedding  # imported here so the pure logic needs no deps
    emb = TextEmbedding(model_name)

    timings: list[float] = []

    def embed_fn(text: str) -> list[float]:
        t0 = time.perf_counter()
        vec = list(emb.embed([text]))[0]
        timings.append((time.perf_counter() - t0) * 1000.0)
        return list(vec)

    # warm up (first call pays model load / graph init — excluded from mean)
    _ = embed_fn(docs[0]["text"] if docs else "warmup")
    timings.clear()

    metrics = evaluate(embed_fn, docs, queries, k)
    dim = len(embed_fn(queries[0]["text"])) if queries else None
    metrics.update({
        "model": model_name,
        "dim": dim,
        "ms_per_embed": (sum(timings) / len(timings)) if timings else float("nan"),
        "rss_mb": _rss_mb(),
    })
    return metrics


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Benchmark embedding models on EN/IT retrieval.")
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    ap.add_argument("--fixture", default=DEFAULT_FIXTURE)
    ap.add_argument("--k", type=int, default=3)
    args = ap.parse_args(argv)

    try:  # Windows console is cp1252 by default → the arrows/ellipses below would crash
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    docs, queries = load_fixture(args.fixture)
    print(f"Fixture: {len(docs)} docs, {len(queries)} queries (k={args.k})\n")

    try:
        import fastembed  # noqa: F401
    except ImportError:
        print("fastembed non installato: pip install -e .[dev] (o .) per eseguire il benchmark.",
              file=sys.stderr)
        return 1

    rows = []
    for m in args.models:
        print(f"→ {m} ...", flush=True)
        try:
            rows.append(bench_model(m, docs, queries, args.k))
        except Exception as exc:  # noqa: BLE001
            print(f"  ERRORE su {m}: {type(exc).__name__}: {exc}", file=sys.stderr)

    if not rows:
        return 1

    print(f"\n{'model':<48} {'dim':>4} {'recall@k':>9} {'mrr':>6} {'ms/emb':>7} {'RSS MB':>7}  per-lang")
    print("-" * 100)
    for r in rows:
        pl = " ".join(f"{lg}={v:.2f}" for lg, v in sorted(r['per_lang_recall'].items()))
        print(f"{r['model']:<48} {str(r['dim']):>4} {r['recall_at_k']:>9.2f} "
              f"{r['mrr']:>6.2f} {r['ms_per_embed']:>7.1f} {r['rss_mb']:>7.0f}  {pl}")
    print("\nLettura: recall@k alto sulle query IT (per-lang it=…) = buon cross-lingua (Opzione A). "
          "Se A2 (pivot LLM) ti dà keyword già in inglese, il baseline EN qui è una stima "
          "pessimistica del suo recall reale.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
