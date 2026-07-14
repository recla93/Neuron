"""Embedding + hybrid vector search (T57, ADR-006 — logic moved out of server.py).

IMPORTANT — state stays in server, logic lives here. The test-suite (and the
runtime toggles) monkeypatch attributes ON THE SERVER MODULE: ``_srv._embedder``,
``_srv._embed_cache``, ``_srv.TURSO_ENGINE``, ``_srv._seed_usable``,
``_srv._active_db_path``, ``_srv._db.connect_local``, ``_srv._g._seed_path`` ...
To keep every one of those patches working with ZERO test migration, the
functions below resolve all mutable/patchable state through the server module
namespace AT CALL TIME (``_S()``, a lazy import — no circular import: server
imports this module at load, this module imports server only inside calls).

A later ADR-006 step may invert this dependency (state object injected, tests
patching ``neuron.search``), but that requires migrating the tests in the same
commit. Until then this file only owns LOGIC, byte-equivalent to the original.
"""

from __future__ import annotations

import logging
import weakref
from typing import Any

from neuron.extraction import DOMAIN_ALIASES

log = logging.getLogger("neuron.search")
from neuron.models import pack_vector


def _S():
    """The server module = the shared state/config namespace (see module docstring)."""
    from neuron import server as _srv
    return _srv


# ---------------------------------------------------------------------------
# Embedder (state: _embedder / _embed_dim_checked / _embed_cache on server)
# ---------------------------------------------------------------------------


def _get_embedder():
    """Lazy-load the embedding model on first use (avoids slow startup)."""
    s = _S()
    if s._embedder is None:
        s._embedder = s.TextEmbedding(s.NS_EMBED_MODEL)
    return s._embedder


def _embed_one(text: str) -> list[float]:
    """Uncached single embed + one-shot dimension guard.

    The guard runs ONCE per process (flag set on first call) — it's a startup
    sanity check that the configured model matches VECTOR_DIM, not a per-call
    gate. Keeping it one-shot is load-bearing: the test suite shares a global
    embedder across cases, and a re-arming guard would make a mismatch in one
    test cascade into every later one."""
    s = _S()
    # fastembed yields numpy arrays; coerce to a plain list of floats so that
    # downstream truthiness checks (``if not v``), JSON export and struct
    # packing never see an ndarray ("truth value of an array is ambiguous").
    vec = [float(x) for x in list(_get_embedder().embed([text]))[0]]
    if not s._embed_dim_checked:
        s._embed_dim_checked = True
        if len(vec) != s.VECTOR_DIM:
            raise RuntimeError(
                f"Embedding model '{s.NS_EMBED_MODEL}' produces {len(vec)}-dim vectors "
                f"but VECTOR_DIM={s.VECTOR_DIM}. Set NS_EMBED_DIM={len(vec)} and re-embed "
                f"the store (scripts/reembed.py), or choose a {s.VECTOR_DIM}-dim model."
            )
    return vec


def _get_embedding(text: str) -> list[float]:
    """Semantic embedding via fastembed (model = NS_EMBED_MODEL, dim = VECTOR_DIM),
    memoized per (embedder, text). See ``_embed_one`` for the dimension guard."""
    s = _S()
    key = (id(_get_embedder()), text)
    cached = s._embed_cache.get(key)
    if cached is not None:
        return cached
    vec = _embed_one(text)
    if len(s._embed_cache) >= s._EMBED_CACHE_MAX:
        # Evict a batch of the oldest keys at once (dict preserves insertion order)
        # so we amortize eviction instead of paying it on every insert past the cap.
        for stale in list(s._embed_cache)[: max(1, s._EMBED_CACHE_MAX // 10)]:
            s._embed_cache.pop(stale, None)
    s._embed_cache[key] = vec
    return vec


# ---------------------------------------------------------------------------
# Seed DB access (state: _seed_conn_cache on server)
# ---------------------------------------------------------------------------


def _seed_usable(path: "str | None") -> bool:
    """True only if `path` is a real SQLite/Turso DB — not missing, and not the
    tiny placeholder stub shipped before base_knowledge.db is generated.

    A valid SQLite file is >= 512 bytes and starts with the magic header.
    Mirrors GraphRegistry._seed_is_loadable."""
    import os
    try:
        if not path or not os.path.isfile(path) or os.path.getsize(path) < 512:
            return False
        with open(path, "rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _seed_connection(path: str):
    s = _S()
    conn = s._seed_conn_cache.get(path)
    if conn is None:
        conn = s._db.connect_local(path)
        s._seed_conn_cache[path] = conn
    return conn


def _drop_seed_connection(path: str) -> None:
    """Evict (and close) a cached seed connection — call after any error on it so
    a broken handle isn't reused."""
    conn = _S()._seed_conn_cache.pop(path, None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Hybrid vector search: Turso SQL (vector_distance_cos) or Python fallback
# ---------------------------------------------------------------------------


def _search_embeddings(
    query_keywords: list[str],
    top_n: int = 8,
    graph=None,
) -> list[tuple[str, float]]:
    import os
    s = _S()
    g = graph or s._g.get()
    _cache_key = (id(g), tuple(sorted(query_keywords)), top_n)
    _cached = s._turn_search_cache.get(_cache_key)
    if _cached is not None and _cached[0]() is g:
        return _cached[1]
    query_vec = s._get_embedding(" ".join(query_keywords))
    query_blob = pack_vector(query_vec)

    SIM_THRESHOLD = 0.3

    if s.TURSO_ENGINE:
        seed_path = getattr(s._g, '_seed_path', None)
        db_paths = []
        if s._seed_usable(seed_path):
            db_paths.append(seed_path)
        adp = s._active_db_path()
        if adp and os.path.exists(adp):
            db_paths.append(adp)
        # MERGE across all DBs; keep the max sim per keyword, then rank.
        merged: dict[str, float] = {}
        for db in db_paths:
            is_seed = (db == seed_path)
            try:
                # Seed connection is cached (immutable DB); active is per-call.
                conn = s._seed_connection(db) if is_seed else s._db.connect_local(db)
                rows = conn.execute(
                    "SELECT keyword, sim FROM ("
                    "  SELECT keyword, 1.0 - vector_distance_cos(f32blob(embedding), f32blob(?)) AS sim "
                    "  FROM node_vectors"
                    ") WHERE sim > ? ORDER BY sim DESC LIMIT ?",
                    (query_blob, SIM_THRESHOLD, top_n),
                ).fetchall()
                if not is_seed:
                    conn.close()
                for kw, sim in rows:
                    v = round(sim, 4)
                    if kw not in merged or v > merged[kw]:
                        merged[kw] = v
            except Exception as e:
                if is_seed:
                    s._drop_seed_connection(db)
                # Any DB/engine error must fall through to the Python path.
                log.debug("Turso vector search failed (using Python fallback): %s", e)
        if merged:
            result = sorted(merged.items(), key=lambda kv: -kv[1])[:top_n]
            s._turn_search_cache[_cache_key] = (weakref.ref(g), result)
            return result

    # --- Python fallback (non-Turso, or every Turso query failed) ---
    # TRUE cosine; a missing vector is embedded ONCE and cached on the node.
    # Complexity ceiling (P1 #7): this is an O(N) linear scan over all nodes per
    # query — fine up to ~1000 nodes (MAX_NODES caps the store at 500 by default).
    # Beyond that, prefer the Turso vector_distance_cos path (native index).
    q_norm = (sum(x * x for x in query_vec) ** 0.5) or 1.0
    scores: list[tuple[str, float]] = []
    for nd in g.nodes:
        if nd.vector is None:
            nd.vector = s._get_embedding(nd.keyword)
            g.mark_vector_dirty(nd.keyword)
        v = nd.vector
        # length check, not truthiness — numpy arrays raise on `not v`
        if v is None or len(v) == 0:
            continue
        dot = sum(qi * vi for qi, vi in zip(query_vec, v))
        denom = q_norm * ((sum(x * x for x in v) ** 0.5) or 1.0)
        sim = dot / denom
        if sim > 0:
            scores.append((nd.keyword, round(sim, 4)))
    scores.sort(key=lambda x: -x[1])
    result = scores[:top_n]
    s._turn_search_cache[_cache_key] = (weakref.ref(g), result)
    return result


# ---------------------------------------------------------------------------
# Domain refinement
# ---------------------------------------------------------------------------


def _normalize_domain(domain: str) -> str:
    """Normalize domain name: lowercase, alias mapping, strip noise."""
    cleaned = domain.lower().strip().replace("-", "").replace(" ", "")
    return DOMAIN_ALIASES.get(cleaned, DOMAIN_ALIASES.get(domain.lower(), domain.lower()))


def _refine_domain(keywords: list[str]) -> tuple["str | None", list[str]]:
    """Vector search via Turso vector_distance_cos against seed node_vectors.

    Returns (best_domain, alternative_domains): best is the highest-scoring
    specific domain (non-general) above threshold (0.35), or None; alternatives
    are domains within the tie margin (0.05) for multi-domain tagging."""
    s = _S()
    query_vec = s._get_embedding(" ".join(keywords))
    query_blob = pack_vector(query_vec)

    rows: list[tuple[str, float]] = []

    if s.TURSO_ENGINE:
        seed_path = getattr(s._g, '_seed_path', None)
        if s._seed_usable(seed_path):
            try:
                conn = s._seed_connection(seed_path)   # cached: immutable seed DB
                rows = conn.execute("""
                    SELECT n.domain, 1.0 - vector_distance_cos(f32blob(nv.embedding), f32blob(?)) AS sim
                    FROM node_vectors nv
                    JOIN nodes n ON n.keyword = nv.keyword
                    WHERE n.domain != 'general'
                    ORDER BY sim DESC LIMIT 30
                """, (query_blob,)).fetchall()
            except Exception as e:
                log.debug("seed vector search failed (using Python fallback): %s", e)
                s._drop_seed_connection(seed_path)

    # Fallback: Python loop over loaded graphs — TRUE cosine, same scale as Turso.
    if not rows:
        q_norm = (sum(x * x for x in query_vec) ** 0.5) or 1.0
        for ctx_g in list(getattr(s._g, '_graphs', {}).values()):
            for nd in (ctx_g.nodes or []):
                v = nd.vector
                # length check, not truthiness — numpy arrays raise on `not v`
                if v is None or len(v) == 0:
                    continue
                dot = sum(qi * vi for qi, vi in zip(query_vec, v))
                sim = dot / (q_norm * ((sum(x * x for x in v) ** 0.5) or 1.0))
                if sim > 0.3:
                    rows.append((nd.domain, sim))

    if not rows:
        return (None, [])

    SIMILARITY_THRESHOLD = 0.3
    TIE_MARGIN = 0.05
    BEST_THRESHOLD = 0.35

    domain_sims: dict[str, list[float]] = {}
    for domain, sim in rows:
        if sim > SIMILARITY_THRESHOLD:
            domain_sims.setdefault(domain, []).append(sim)

    scores: dict[str, float] = {}
    for domain, sims in domain_sims.items():
        top = sorted(sims, reverse=True)[:3]
        scores[domain] = sum(top) / len(top)

    if not scores:
        return (None, [])

    ranked = sorted(scores.items(), key=lambda x: -x[1])
    best, best_score = ranked[0]

    if best_score < BEST_THRESHOLD:
        return (None, [])

    alt = [d for d, sc in ranked[1:] if best_score - sc < TIE_MARGIN and d != "general"]
    return (best, alt)
