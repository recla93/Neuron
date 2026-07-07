"""E0.3 — Re-embed dello store Neuron dopo un cambio di modello.

I vettori prodotti da modelli diversi NON sono confrontabili: cambiando
NS_EMBED_MODEL (ADR-001) i `node_vectors` esistenti vanno rigenerati, altrimenti
la ricerca semantica confronta vettori di spazi diversi (rumore). Questo script
ricalcola tutti i vettori con il modello attivo e aggiorna `meta.embed_model`.

Uso:
    NS_EMBED_MODEL=intfloat/multilingual-e5-small python scripts/reembed.py --all
    python scripts/reembed.py --context java/spring
    python scripts/reembed.py --db path/to/graph.db --dry-run

Tier:
  - file locali: rigenera ogni `graph_*.db` in NS_GRAPHS_DIR (o quello indicato);
  - Turso cloud: se TURSO_* sono impostate, rigenera l'unico store condiviso.

È idempotente e non distruttivo (INSERT OR REPLACE sui soli vettori); con
--dry-run non scrive nulla e riporta solo cosa farebbe.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

NS_EMBED_MODEL = os.environ.get("NS_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2").strip()


# ---------------------------------------------------------------------------
# Core (testabile: conn + embed_fn iniettabili) — nessuna dipendenza pesante
# ---------------------------------------------------------------------------

def _table_cols(conn, table: str) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def reembed_conn(conn, embed_fn, pack_fn, dim: int, model_name: str, dry_run: bool = False) -> dict:
    """Rigenera node_vectors per una connessione. Ritorna un report.

    embed_fn(text)->list[float]; pack_fn(list[float])->bytes. Scoped per context se
    la colonna esiste, altrimenti tutto sotto 'default' (DB legacy)."""
    ncols = _table_cols(conn, "nodes")
    if not ncols:
        return {"nodes": 0, "contexts": [], "skipped": "no nodes table"}

    has_ctx = "context" in ncols
    if has_ctx:
        rows = conn.execute("SELECT context, keyword FROM nodes").fetchall()
    else:
        rows = [("default", r[0]) for r in conn.execute("SELECT keyword FROM nodes").fetchall()]

    contexts = sorted({str(c) for c, _ in rows})
    if dry_run:
        return {"nodes": len(rows), "contexts": contexts, "dry_run": True}

    for ctx, kw in rows:
        blob = pack_fn(embed_fn(kw))
        conn.execute(
            "INSERT OR REPLACE INTO node_vectors (context, keyword, embedding, dim) VALUES (?,?,?,?)",
            (ctx, kw, blob, dim),
        )
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('embed_model', ?)", (model_name,))
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('embed_dim', ?)", (str(dim),))
    try:
        conn.commit()
    except Exception:
        pass
    return {"nodes": len(rows), "contexts": contexts, "dry_run": False}


# ---------------------------------------------------------------------------
# Wiring (fastembed + neuron.db) — solo in esecuzione reale
# ---------------------------------------------------------------------------

def _build_embed():
    from fastembed import TextEmbedding
    from neuron.models import pack_vector, VECTOR_DIM
    emb = TextEmbedding(NS_EMBED_MODEL)

    def embed_fn(text: str) -> list[float]:
        vec = list(emb.embed([text]))[0]
        if len(vec) != VECTOR_DIM:
            raise RuntimeError(
                f"Il modello '{NS_EMBED_MODEL}' produce vettori {len(vec)}-dim ma "
                f"VECTOR_DIM={VECTOR_DIM}. Imposta NS_EMBED_DIM={len(vec)} e rilancia."
            )
        return vec

    return embed_fn, pack_vector, VECTOR_DIM


def _discover_local_dbs(graphs_dir: str, context: str | None, db: str | None) -> list[str]:
    if db:
        return [db]
    if context:
        safe = context.replace("/", "__") if context != "default" else "default"
        return [os.path.join(graphs_dir, f"graph_{safe}.db")]
    import glob
    return sorted(glob.glob(os.path.join(graphs_dir, "graph_*.db")))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Re-embed dello store dopo un cambio di modello.")
    ap.add_argument("--all", action="store_true", help="tutti i graph_*.db (default se nessun target)")
    ap.add_argument("--context", help="solo questo contesto (graph_<ctx>.db)")
    ap.add_argument("--db", help="un file DB specifico")
    ap.add_argument("--dry-run", action="store_true", help="non scrive, riporta soltanto")
    args = ap.parse_args(argv)

    print(f"Modello attivo (NS_EMBED_MODEL): {NS_EMBED_MODEL}")

    try:
        import neuron.db as _db
    except Exception as exc:  # noqa: BLE001
        print(f"Impossibile importare neuron.db: {exc}", file=sys.stderr)
        return 1

    if not args.dry_run:
        try:
            embed_fn, pack_fn, dim = _build_embed()
        except ImportError:
            print("fastembed non installato: pip install -e . (o .[dev]) per rigenerare i vettori.",
                  file=sys.stderr)
            return 1
    else:
        # dry-run: non serve il modello, non tocchiamo gli embedding
        from neuron.models import pack_vector, VECTOR_DIM
        embed_fn, pack_fn, dim = (lambda t: [0.0] * VECTOR_DIM), pack_vector, VECTOR_DIM

    total_nodes = 0
    targets: list[tuple[str, object]] = []

    if getattr(_db, "REMOTE_TURSO", False):
        print(f"Tier: {_db.ENGINE_NAME} — store condiviso remoto")
        targets.append(("<remote Turso>", _db.connect("")))
    else:
        graphs_dir = os.environ.get("NS_GRAPHS_DIR") or os.path.join(
            os.path.expanduser("~"), ".local", "share", "neuron", "graphs")
        graphs_dir = os.path.normpath(graphs_dir)
        paths = _discover_local_dbs(graphs_dir, args.context, args.db)
        if not paths:
            print(f"Nessun DB trovato in {graphs_dir} (contesto={args.context}).", file=sys.stderr)
            return 1
        for p in paths:
            if not os.path.exists(p):
                print(f"  skip (assente): {p}"); continue
            targets.append((p, _db.connect_local(p)))

    for label, conn in targets:
        try:
            rep = reembed_conn(conn, embed_fn, pack_fn, dim, NS_EMBED_MODEL, dry_run=args.dry_run)
            tag = "DRY-RUN" if args.dry_run else "OK"
            print(f"  [{tag}] {label}: {rep['nodes']} nodi, contesti={rep['contexts']}")
            total_nodes += rep["nodes"]
        except Exception as exc:  # noqa: BLE001
            print(f"  ERRORE su {label}: {type(exc).__name__}: {exc}", file=sys.stderr)
        finally:
            try: conn.close()
            except Exception: pass

    print(f"\n{'(dry-run) ' if args.dry_run else ''}Totale nodi processati: {total_nodes}. "
          f"meta.embed_model → {NS_EMBED_MODEL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
