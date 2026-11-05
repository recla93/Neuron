"""Embedding + hybrid vector search (T57, ADR-006 — logic moved out of server.py).

IMPORTANT — state stays in server, logic lives here. The test-suite (and the
runtime toggles) monkeypatch attributes ON THE SERVER MODULE: ``_srv._embedder``,
``_srv._embed_cache``, ``_srv.TURSO_ENGINE``, ``_srv._sqlite_usable``,
``_srv._active_db_path``, ``_srv._db.connect_local`` ...
To keep every one of those patches working with ZERO test migration, the
functions below resolve all mutable/patchable state through the server module
namespace AT CALL TIME (``_S()``, a lazy import — no circular import: server
imports this module at load, this module imports server only inside calls).

A later ADR-006 step may invert this dependency (state object injected, tests
patching ``neuron.search``), but that requires migrating the tests in the same
commit. Until then this file only owns LOGIC, byte-equivalent to the original.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import weakref
from array import array

from neuron.extraction import DOMAIN_ALIASES

log = logging.getLogger("neuron.search")
from neuron.db import SQLITE_MIN_VALID_SIZE
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
        # ponytail: see neurag/embedder.py for the measured rationale (~4%
        # lower resident memory, no latency cost) — keep the two in sync.
        s._embedder = s.TextEmbedding(s.NS_EMBED_MODEL, threads=2, enable_cpu_mem_arena=False)
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
    # Vettori unitari: dedup semantica, stimulus e auto-link confrontano con il
    # prodotto scalare e soglie in [-1, 1]. paraphrase-multilingual-MiniLM-L12-v2
    # (default da ADR-001) esce NON normalizzato (norma ~3-4), quindi ogni
    # coppia di keyword multi-parola superava 0.90 e veniva fusa nel primo nodo.
    # vector_distance_cos() e _cos() sono invarianti alla scala: nessun effetto lì.
    norm = sum(x * x for x in vec) ** 0.5
    if norm > 0:
        vec = [x / norm for x in vec]
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
# File checks
# ---------------------------------------------------------------------------


def _sqlite_usable(path: "str | None") -> bool:
    """True only if `path` is a real SQLite/Turso DB: not missing, not a stub.

    A valid SQLite file is >= 512 bytes and starts with the magic header. Used on
    the other contexts' graph files (cross_context_matches). It was named
    `_seed_usable` while the seed DB (base_knowledge.db) shipped; gone in 6.9.0."""
    try:
        if not path or not os.path.isfile(path) or os.path.getsize(path) < SQLITE_MIN_VALID_SIZE:
            return False
        with open(path, "rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Hybrid vector search: Turso SQL (vector_distance_cos) or Python fallback
# ---------------------------------------------------------------------------


def _permanent_vector_failure(exc: Exception, path: str | None = None) -> bool:
    """True if `exc` says the engine has NO vector_distance_cos at all.

    That's permanent for this process (plain sqlite3, or a pyturso build without
    it), so the SQL tier is latched off: retrying it every call costs the failed
    query *plus* the reconnect that follows it. Transient errors (locks, corrupt handle) are NOT latched — they
    keep the existing drop-and-retry behaviour.

    `path` distingue i due casi, che l'errore da solo non separa. Un FILE caduto
    su sqlite3 per il degrado L2 (un altro processo teneva il lock) alza la
    stessa `no such function` di un motore senza vettori, ma non dice niente sul
    motore: pyturso sta benissimo, e' quell'handle a non avere la funzione.
    Latchare il globale li' spegneva il tier SQL nativo anche sui grafi sani —
    e con esso `cross_context_matches`, cioe' l'instradamento di contesto. Con
    il path noto il salto resta file-scoped: si torna True (salta QUESTO file)
    senza toccare `_vector_sql_ok`.
    """
    if "no such function" not in str(exc).lower():
        return False
    s = _S()
    if path is not None and path in s._db.DEGRADED_PATHS:
        return True
    if s._vector_sql_ok:
        s._vector_sql_ok = False
        log.warning(
            "Engine has no vector_distance_cos (%s) — vector search falls back "
            "to the Python cosine loop for this process.", exc,
        )
    return True


HUB_MIN_EXCESS = 0.05   # below this the excess is measurement noise, not a hub


def hub_excess(g) -> dict[str, float]:
    """Per node, how much closer it sits to the whole graph than a normal node.

    Measured 2026-09-14 on 368 nodes: a dozen short rare tokens (`vram`,
    `lombok`, `gui`, `puntatore-contesto`) average 0.36-0.42 cosine to every
    other node against a graph mean of 0.24, so they fell in the 0.30-0.75
    band for 60-83% of all possible queries — in nearly every ranking, whatever
    the topic. That is the embedder, not the memory: it parks unknown tech
    words together. Centering by each node's excess (its mean to all others,
    minus the graph's mean of those) leaves a normal node untouched and costs
    a hub exactly what it is. Only the penalty side is ever applied (see
    hub_penalty): a node far from everything is far from this query too.

    Cached on the graph, keyed by how many nodes carry a vector: a new node
    recomputes it, a re-embedded one does not (rare, and the drift is small).
    Numpy is a fastembed dependency; without it there is no correction, not a
    crash.
    """
    nodes = [nd for nd in g.nodes if nd.vector is not None and len(nd.vector)]
    key = (len(nodes), len(g.nodes))
    cached = getattr(g, "_hub_cache", None)
    if cached is not None and cached[0] == key:
        return cached[1]
    excess: dict[str, float] = {}
    if len(nodes) >= 3:
        try:
            import numpy as np
        except ImportError:
            np = None
        if np is not None:
            # ponytail: all pairs on every recompute, ~10 ms at 400 nodes;
            # sample the columns if a graph ever grows past a few thousand.
            m = np.asarray([nd.vector for nd in nodes], dtype=np.float32)
            m /= (np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)
            sims = m @ m.T
            np.fill_diagonal(sims, 0.0)
            mean = sims.sum(axis=1) / (len(nodes) - 1)
            ex = mean - mean.mean()
            excess = {nd.keyword: float(e) for nd, e in zip(nodes, ex)}
    g._hub_cache = (key, excess)
    return excess


def hub_penalty(excess: dict[str, float], keyword: str) -> float:
    """What a keyword's similarity loses for being a hub: its excess, if it is one."""
    e = excess.get(keyword, 0.0)
    return e if e >= HUB_MIN_EXCESS else 0.0


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
    # Hub centering (hub_excess): applied on BOTH tiers, after the raw cosine.
    # Only hubs can lose rank, so fetching top_n + (number of hubs) from SQL
    # keeps the adjusted top_n exact.
    excess = hub_excess(g)
    n_hubs = sum(1 for e in excess.values() if e >= HUB_MIN_EXCESS)

    if s.TURSO_ENGINE and s._vector_sql_ok:
        # Only the active graph. The seed DB was merged in here too, and it is
        # Neuron's own manual: `store_turn`, `dedup`, `consolidate` came back
        # for every query in every context, even ones that never held them.
        # On the Turso Cloud tier saves go to the shared store: a local
        # graph_<ctx>.db is a FROZEN SNAPSHOT whose outdated vectors would
        # outrank current cloud ones (ghost concepts) — so no file there.
        adp = s._active_db_path()
        merged: dict[str, float] = {}
        if adp and os.path.exists(adp) and not getattr(s._db, "REMOTE_TURSO", False):
            try:
                conn = s._db.connect_local(adp)
                rows = conn.execute(
                    "SELECT keyword, sim FROM ("
                    "  SELECT keyword, 1.0 - vector_distance_cos(embedding, ?) AS sim "
                    "  FROM node_vectors"
                    ") WHERE sim > ? ORDER BY sim DESC LIMIT ?",
                    (query_blob, SIM_THRESHOLD, top_n + n_hubs),
                ).fetchall()
                conn.close()
                merged = {kw: round(sim, 4) for kw, sim in rows}
            except Exception as e:
                # Any DB/engine error must fall through to the Python path.
                if not _permanent_vector_failure(e, adp):
                    log.debug("Turso vector search failed (using Python fallback): %s", e)
        if merged:
            adjusted = [(kw, round(v - hub_penalty(excess, kw), 4)) for kw, v in merged.items()]
            result = sorted(((kw, v) for kw, v in adjusted if v > SIM_THRESHOLD),
                            key=lambda kv: -kv[1])[:top_n]
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
        sim = dot / denom - hub_penalty(excess, nd.keyword)
        # Same floor as the Turso tier (T78): the fallback used `sim > 0`, so
        # it returned weak matches the SQL tier would filter out — the two
        # tiers disagreed on what "related" means and could fill top_n with
        # noise. One threshold, both tiers.
        if sim > SIM_THRESHOLD:
            scores.append((nd.keyword, round(sim, 4)))
    scores.sort(key=lambda x: -x[1])
    result = scores[:top_n]
    s._turn_search_cache[_cache_key] = (weakref.ref(g), result)
    return result


def _recall_texts(g, nd) -> list[str]:
    """What a node KNOWS, one text per fact: `name. topic`, then `name. fact`
    for each fact. Not all in one text: the model reads 128 tokens and cuts
    the rest, so a node with five facts was judged on the first one or two."""
    return ([f"{nd.keyword}. {nd.topic or ''}"]
            + [f"{nd.keyword}. {e['text']}" for e in g.episodes.get(nd.keyword, [])])


# (id(embedder), text) -> unit vector. Its own cache, not `_embed_cache`: one
# vector per fact is ~6000 texts at MAX_NODES=1000, past that cache's 4096 cap,
# and an evicted text would be embedded again on every query.
_RECALL_VECS: dict = {}
# Embedding the whole `ai` graph took 47 s (713 texts, 2026-11-04): paid once,
# then kept in a file next to the graphs — a cache, rebuilt if it is lost.
# More missing texts than this are embedded in the background, so the first
# query after an install answers with what is there instead of blocking.
_RECALL_FILE = "_recall_vectors.bin"
_RECALL_INLINE_MAX = 64
_recall_disk: "dict | None" = None          # sha1(text) -> vector, read once
_recall_warming = False


def _persistable(emb) -> bool:
    """Only the real model's vectors go to disk, never a test's fake embedder."""
    return type(emb).__module__.startswith("fastembed")


def _recall_path() -> "str | None":
    d = getattr(getattr(_S(), "_g", None), "_graphs_dir", None)
    return os.path.join(d, _RECALL_FILE) if d else None


def _recall_from_disk() -> dict:
    global _recall_disk
    if _recall_disk is None:
        _recall_disk = {}
        path = _recall_path()
        try:
            with open(path, "rb") as f:
                head = json.loads(f.readline())
                if head.get("model") == _S().NS_EMBED_MODEL:
                    dim = int(head["dim"])
                    while len(h := f.read(20)) == 20:
                        vec = array("f")
                        vec.frombytes(f.read(4 * dim))
                        _recall_disk[h] = list(vec)
        except (OSError, TypeError, ValueError, KeyError):
            pass
    return _recall_disk


def _recall_to_disk(eid: int, texts: "set[str]") -> None:
    path = _recall_path()
    rows = [(t, _RECALL_VECS[(eid, t)]) for t in texts if (eid, t) in _RECALL_VECS]
    if not path or not rows:
        return
    try:
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write((json.dumps({"model": _S().NS_EMBED_MODEL, "dim": len(rows[0][1])}) + "\n").encode())
            for t, v in rows:
                f.write(hashlib.sha1(t.encode()).digest() + array("f", v).tobytes())
        os.replace(tmp, path)
    except OSError as e:
        log.debug("recall vectors not saved: %s", e)


def _embed_recall(emb, eid: int, texts: list[str]) -> None:
    for t, v in zip(texts, emb.embed(texts)):
        v = [float(x) for x in v]
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        _RECALL_VECS[(eid, t)] = [x / norm for x in v]


def _search_recall(query: str, top_n: int, graph) -> list[tuple[str, float]]:
    """Nodes ranked by what they know, not by their name: each node scores its
    best text (see _recall_texts).

    `_search_embeddings` compares the query with the vector of the node's NAME:
    a sentence like "il python lanciato dal repo importa i pacchetti sbagliati"
    against the label `cwd-shadowing`. The name vector stays as it is for
    dedup, which compares a keyword with a keyword: a vector full of facts
    would make two different concepts look alike again.

    Measured with the full pre_turn on a copy of the real `ai` graph
    (2026-11-04), 18 questions on topic and 12 off topic, threshold 0.43:
    name only 3/14 on an earlier set; name+topic+facts in one text 10/18 found
    and 8/12 suppressed; one text per fact 14/18 and 8/12.

    A text is embedded only when it is new (a new fact, a new topic); the
    vectors survive a restart in _RECALL_FILE (see the notes above it)."""
    global _RECALL_VECS, _recall_warming
    emb = _get_embedder()
    eid = id(emb)
    persist = _persistable(emb)
    per = {nd.keyword: _recall_texts(graph, nd) for nd in graph.nodes}
    texts = {t for ts in per.values() for t in ts}
    missing = [t for t in texts if (eid, t) not in _RECALL_VECS]
    if missing and persist:
        disk = _recall_from_disk()
        for t in missing:
            v = disk.get(hashlib.sha1(t.encode()).digest())
            if v is not None:
                _RECALL_VECS[(eid, t)] = v
        missing = [t for t in missing if (eid, t) not in _RECALL_VECS]
    if missing and len(missing) <= _RECALL_INLINE_MAX:
        _embed_recall(emb, eid, missing)
        if persist:
            _recall_to_disk(eid, texts)
    elif missing and not _recall_warming:
        _recall_warming = True

        def _warm():
            global _recall_warming
            try:
                _embed_recall(emb, eid, missing)
                if persist:
                    _recall_to_disk(eid, texts)
            finally:
                _recall_warming = False
        threading.Thread(target=_warm, name="recall-warm", daemon=True).start()
    if not _recall_warming and len(_RECALL_VECS) > 2 * len(texts) + 1000:
        # old facts, other graphs: drop them, keep this graph's
        _RECALL_VECS = {k: v for k, v in _RECALL_VECS.items() if k[1] in texts}
    q = _get_embedding(query)
    # ponytail: O(texts) Python dot products, ~30 ms for 713 texts at 500
    # nodes; numpy or a vector column in the store if MAX_NODES nears 5000.
    scores = []
    for kw, ts in per.items():
        vecs = [_RECALL_VECS.get((eid, t)) for t in ts]
        sims = [sum(a * b for a, b in zip(q, v)) for v in vecs if v is not None]
        if sims:          # while warming, a node not embedded yet is skipped
            scores.append((kw, round(max(sims), 4)))
    scores.sort(key=lambda x: -x[1])
    return scores[:top_n]


# ---------------------------------------------------------------------------
# Domain refinement
# ---------------------------------------------------------------------------


def _normalize_domain(domain: str) -> str:
    """Normalize domain name: lowercase, alias mapping, strip noise."""
    cleaned = domain.lower().strip().replace("-", "").replace(" ", "")
    return DOMAIN_ALIASES.get(cleaned, DOMAIN_ALIASES.get(domain.lower(), domain.lower()))


def _refine_domain(keywords: list[str]) -> tuple["str | None", list[str]]:
    """Guess a specific domain for `keywords` from the user's OWN loaded graphs.

    Returns (best_domain, alternative_domains): best is the highest-scoring
    specific domain (non-general) above threshold (0.35), or None; alternatives
    are domains within the tie margin (0.05) for multi-domain tagging.

    It used to vote first on the seed DB's node_vectors. The seed was Neuron's
    own manual, and its only six non-general nodes were Neuron tools tagged
    `backend`: a message that resembled Neuron's docs got that domain, and the
    domain feeds `auto`'s context auto-switch. The seed is gone in 6.9.0."""
    s = _S()
    query_vec = s._get_embedding(" ".join(keywords))

    rows: list[tuple[str, float]] = []
    # Python loop over loaded graphs — TRUE cosine.
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


# ---------------------------------------------------------------------------
# Cross-context recall (READ-ONLY)
# ---------------------------------------------------------------------------
# Perche' esiste. I contesti erano compartimenti stagni nei fatti, non nelle
# intenzioni: i drift link nascono solo per OMONIMIA ESATTA fra due grafi
# caricati (server.py, form_drift_link su `kw, kw`), e keyword scritte bene —
# concettuali, specifiche, come il tool stesso chiede — non si ripetono fra
# domini. Misurato su uno store reale: 191 keyword in `ai`, 144 in `default`,
# ZERO in comune. Il canale c'era e non poteva scattare.
#
# Questa e' la meta' in LETTURA, e solo quella: nessun link viene creato, niente
# tocca il disco. Se la soglia e' sbagliata si cambia una variabile d'ambiente e
# non resta traccia nel grafo — al contrario di un drift scritto a tappeto, che
# poi va ripulito. La promozione a link persistente, se mai, spetta a `confirm`:
# solo i ponti che sono serviti davvero meritano di sopravvivere al turno.
#
# Costo, misurato: ~0.9 ms per grafo via `vector_distance_cos` nativo, contro i
# 7.5 ms dell'embedding della query che si paga comunque. Quattro contesti in
# piu' stanno sotto il rumore.
#
# Richiede il tier vettoriale SQL: senza (sqlite3 puro dopo un degrado L2) la
# funzione tace invece di caricare in Python i vettori di ogni grafo. Un extra
# di richiamo non giustifica una scansione lineare su tutto lo store.
CROSS_ENABLED = os.environ.get(
    "NEURON_CROSS_CONTEXT", "1").strip().lower() not in ("0", "false", "no", "off")
# Alta di proposito. Il refine-domain usa 0.3, ma quello sceglie fra domini gia'
# candidati; qui si pesca a strascico in archivi che non c'entrano, e il codice
# stesso definisce i drift "the noisiest". Meglio due ponti veri che dieci forse.
CROSS_SIM = float(os.environ.get("NEURON_CROSS_SIM", "0.55"))
CROSS_TOP_N = int(os.environ.get("NEURON_CROSS_TOP_N", "2"))


def cross_context_matches(query_vec, active_ctx: str, graphs_dir: str,
                          top_n: int = 0, threshold: float = 0.0) -> list[tuple]:
    """Concetti simili che vivono in ALTRI contesti. Ritorna [(ctx, keyword, sim)].

    Interroga i file `graph_*.db` per path, senza istanziare i Graph: servono le
    righe di `node_vectors`, non la memoria di lavoro di ogni contesto. Il lock
    esclusivo di pyturso non e' un problema qui — vale fra processi, e queste
    aperture stanno tutte in quello corrente, esattamente come `resolve_chain`
    tiene gia' aperti corrente e default insieme.

    Best-effort per costruzione: qualunque errore su un contesto lo salta. E'
    un extra di richiamo, non deve poter far fallire un pre_turn.
    """
    import glob
    if not CROSS_ENABLED or not query_vec:
        return []
    # On the Turso Cloud tier the local graph files are stale snapshots:
    # cross-context recall over them surfaces fossils, not other contexts'
    # real state. Read-only extra — better honest-empty than wrong.
    try:
        from neuron import db as _db
        if getattr(_db, "REMOTE_TURSO", False):
            return []
    except Exception:  # noqa: BLE001
        pass
    top_n = top_n or CROSS_TOP_N
    threshold = threshold or CROSS_SIM
    s = _S()
    active_file = os.path.basename(
        os.path.join(graphs_dir,
                      f"graph_{active_ctx.replace('/', '__') if active_ctx != 'default' else 'default'}.db"))
    query_blob = pack_vector(query_vec)
    out: list[tuple] = []
    for db in sorted(glob.glob(os.path.join(graphs_dir, "graph_*.db"))):
        base = os.path.basename(db)
        if base == active_file or not _sqlite_usable(db):
            continue
        ctx = base[len("graph_"):-len(".db")].replace("__", "/")
        try:
            conn = s._db.connect_local(db)
            try:
                rows = conn.execute(
                    "SELECT keyword, sim FROM ("
                    "  SELECT keyword, 1.0 - vector_distance_cos(embedding, ?) AS sim "
                    "  FROM node_vectors"
                    ") WHERE sim > ? ORDER BY sim DESC LIMIT ?",
                    (query_blob, threshold, top_n),
                ).fetchall()
            finally:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
            out.extend((ctx, kw, round(float(sim), 4)) for kw, sim in rows)
        except Exception as e:  # noqa: BLE001 — motore senza vector SQL, file in uso, ...
            log.debug("cross-context search skipped %s: %s", ctx, e)
    out.sort(key=lambda r: -r[2])
    return out[:top_n]
