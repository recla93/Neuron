"""One-time migration: populate node_vectors in existing base_knowledge.db.
Run once, then commit the updated seed. Removes a stale -wal BEFORE opening.
"""
import os, sys, struct, time

SEED = os.path.join(os.path.dirname(__file__), "..", "knowledge", "base_knowledge.db")
SEED = os.path.normpath(SEED)

from neuron import db as sqlite3
TURSO = sqlite3.LOCAL_TURSO_ENGINE

# SSOT: models.py resolves NS_EMBED_MODEL. The hardcoded retired model here is
# exactly the drift reembed.py's header documents as a past bug.
from neuron.models import EMBED_MODEL as _MODEL_NAME
from fastembed import TextEmbedding
_embedder = TextEmbedding(_MODEL_NAME)
DIM = 384
print(f"fastembed — {DIM}-dim vectors ({_MODEL_NAME})")


def _get_embedding(text: str) -> list[float]:
    return list(_embedder.embed(text))[0].tolist()


def _pack_vector(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def main():
    if not os.path.exists(SEED):
        print(f"Seed not found: {SEED}")
        sys.exit(1)

    # Stale -wal must go BEFORE the connection opens: deleting a live database's
    # WAL can lose committed-but-not-checkpointed data.
    wal = str(SEED) + "-wal"
    if os.path.exists(wal):
        os.remove(wal)
        print("Removed stale -wal file")

    conn = sqlite3.connect(SEED)

    kw_count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    existing_vecs = conn.execute("SELECT COUNT(*) FROM node_vectors").fetchone()[0]
    print(f"Nodes: {kw_count}, Existing vectors: {existing_vecs}")

    if existing_vecs == kw_count:
        print("Vectors already populated. Nothing to do.")
        conn.close()
        return

    rows = conn.execute(
        "SELECT keyword, COALESCE(NULLIF(entities, '[]'), NULLIF(tags, '[]'), topic) "
        "FROM nodes ORDER BY id"
    ).fetchall()

    inserted = 0
    start = time.time()
    for i, (keyword, fallback) in enumerate(rows):
        text = keyword if len(keyword) > 3 else fallback or keyword
        vec = _get_embedding(text)
        blob = _pack_vector(vec)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO node_vectors (keyword, embedding, dim) VALUES (?, ?, ?)",
                (keyword, blob, DIM),
            )
            inserted += 1
        except Exception as e:
            print(f"Error at row {i}: {keyword} -> {e}")

        if i % 500 == 0 and i > 0:
            conn.commit()
            elapsed = time.time() - start
            print(f"  {i}/{kw_count} ({elapsed:.1f}s)")

    conn.commit()
    if not TURSO:
        conn.execute("VACUUM")
    conn.close()

    elapsed = time.time() - start
    print(f"Done. {inserted} vectors inserted in {elapsed:.1f}s")
    print(f"Updated file: {SEED}")


if __name__ == "__main__":
    main()
