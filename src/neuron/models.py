"""Neuron data models: Node, Link, Graph.

Separated from server.py to break the circular import with registry.py.
"""

from __future__ import annotations

import json
import os
import sqlite3
import struct
from dataclasses import dataclass, field
from typing import Any, Callable

from neuron import db as _db

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

Weight    = str
LinkType  = str
Domain    = str
Sentiment = str
Intent    = str

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TANGENTIAL_EXPIRY_TURNS  = 5
WEIGHT_ORDER             = {"strong": 3, "medium": 2, "tangential": 1}
SALIENCE_DECAY_THRESHOLD = 5
SALIENCE_DECAY_AMOUNT    = 1
# Hebbian reinforcement (E2.1): links whose endpoints co-occur in a turn get their
# co_activation_count bumped (at most once per HEBBIAN_COOLDOWN turns, so a single
# chatty turn or rapid repeats can't inflate it), and the weight is promoted at the
# thresholds below. Promotion is monotone (never a downgrade), reusing the atomic
# weight CASE from T11.
HEBBIAN_COOLDOWN         = 2   # min turns between two counts on the same link
HEBBIAN_UPGRADE_MEDIUM   = 3   # co_activation_count promoting tangential -> medium
HEBBIAN_UPGRADE_STRONG   = 8   # co_activation_count promoting medium    -> strong
# Embedding dimension. Default 384 (fastembed all-MiniLM-L6-v2 and the common
# 384-dim multilingual models). Overridable via NS_EMBED_DIM for a model with a
# different width — must match NS_EMBED_MODEL (see server._get_embedding guard).
VECTOR_DIM               = int(os.environ.get("NS_EMBED_DIM", "384"))
# Nome del modello di embedding attivo (deve combaciare con i vettori nello
# store: vettori di modelli diversi non sono confrontabili — vedi load_sqlite).
EMBED_MODEL              = os.environ.get("NS_EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2").strip()
MAX_NODES                = 500   # evict lowest-salience nodes beyond this cap

# ---------------------------------------------------------------------------
# Embedding callback — registered by server.py at startup (lazy, avoids circ.)
# ---------------------------------------------------------------------------

_embed_fn: Callable[[str], list[float]] | None = None


def register_embed_fn(fn: Callable[[str], list[float]]) -> None:
    global _embed_fn
    _embed_fn = fn


def _get_vector(text: str) -> list[float]:
    """Call the registered embedding function, or return zeros if not ready."""
    if _embed_fn is not None:
        return _embed_fn(text)
    return [0.0] * VECTOR_DIM


# ---------------------------------------------------------------------------
# Vector helpers
# ---------------------------------------------------------------------------

def _cos(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors (0 if a norm is 0)."""
    dot = sum(x * y for x, y in zip(a, b))
    na = (sum(x * x for x in a) ** 0.5) or 1.0
    nb = (sum(y * y for y in b) ** 0.5) or 1.0
    return dot / (na * nb)


def pack_vector(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def unpack_vector(data: bytes) -> list[float]:
    return list(struct.unpack(f"{len(data) // 4}f", data))


# ---------------------------------------------------------------------------
# Upsert SQL (T12) — targeted writes keyed on natural keys, no global wipe.
# ON CONFLICT DO UPDATE (not INSERT OR REPLACE) preserves the row id, keeping
# load_sqlite's ``ORDER BY id`` stable across updates.
# ---------------------------------------------------------------------------

# Rows are keyed by (context, ...) so that multiple contexts can share one
# physical store (Turso Cloud, T11 option B) without colliding — a node "spring"
# in context "java" is distinct from "spring" in "python". Locally each context
# is still a separate file, so the context column is redundant-but-harmless there.
_NODE_UPSERT = (
    "INSERT INTO nodes (context, keyword, turn, topic, domain, sentiment, salience, "
    "entities, tags, refs) VALUES (?,?,?,?,?,?,?,?,?,?) "
    "ON CONFLICT(context, keyword) DO UPDATE SET "
    "turn=excluded.turn, topic=excluded.topic, domain=excluded.domain, "
    "sentiment=excluded.sentiment, salience=excluded.salience, "
    "entities=excluded.entities, tags=excluded.tags, refs=excluded.refs"
)

_LINK_UPSERT = (
    "INSERT INTO links (context, source, target, link_type, weight, rationale, "
    "created_turn, last_active_turn, inactive_turns, co_activation_count) "
    "VALUES (?,?,?,?,?,?,?,?,?,?) "
    "ON CONFLICT(context, source, target, link_type) DO UPDATE SET "
    "weight=excluded.weight, rationale=excluded.rationale, "
    "created_turn=excluded.created_turn, last_active_turn=excluded.last_active_turn, "
    "inactive_turns=excluded.inactive_turns, "
    "co_activation_count=excluded.co_activation_count"
)

_VEC_UPSERT = ("INSERT OR REPLACE INTO node_vectors (context, keyword, embedding, dim) "
               "VALUES (?,?,?,?)")

# --- Concurrency-safe variants (T11 Fase 2b), used only on the incremental path.
# Salience is applied as a RELATIVE delta (``salience + ?``) instead of writing an
# absolute value, so two members incrementing the same node concurrently both
# count (no lost update); decay is just a negative delta, and ``MAX(0, …)`` keeps
# the clamp — no separate decay semantics needed. The extra trailing ``?`` is the
# per-node delta since this graph's last save.
_NODE_UPSERT_ATOMIC = (
    "INSERT INTO nodes (context, keyword, turn, topic, domain, sentiment, salience, "
    "entities, tags, refs) VALUES (?,?,?,?,?,?,?,?,?,?) "
    "ON CONFLICT(context, keyword) DO UPDATE SET "
    "turn=excluded.turn, topic=excluded.topic, domain=excluded.domain, "
    "sentiment=excluded.sentiment, salience=MAX(0, salience + ?), "
    "entities=excluded.entities, tags=excluded.tags, refs=excluded.refs"
)

# Weight is promoted monotonically: a concurrent writer can only ever RAISE a
# link's weight (tangential < medium < strong), never silently downgrade it.
_WEIGHT_RANK = ("CASE {c} WHEN 'strong' THEN 3 WHEN 'medium' THEN 2 "
                "WHEN 'tangential' THEN 1 ELSE 0 END")
_LINK_UPSERT_ATOMIC = (
    "INSERT INTO links (context, source, target, link_type, weight, rationale, "
    "created_turn, last_active_turn, inactive_turns, co_activation_count) "
    "VALUES (?,?,?,?,?,?,?,?,?,?) "
    "ON CONFLICT(context, source, target, link_type) DO UPDATE SET "
    "weight=CASE WHEN " + _WEIGHT_RANK.format(c="excluded.weight") + " > "
    + _WEIGHT_RANK.format(c="weight") + " THEN excluded.weight ELSE weight END, "
    "rationale=excluded.rationale, created_turn=excluded.created_turn, "
    "last_active_turn=excluded.last_active_turn, inactive_turns=excluded.inactive_turns, "
    # Hebbian count only ever grows: MAX() so a stale concurrent writer can't lower it.
    "co_activation_count=MAX(co_activation_count, excluded.co_activation_count)"
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Node:
    keyword:    str
    turn:       int
    topic:      str
    domain:     Domain
    sentiment:  Sentiment
    salience:   int            = 0
    entities:   list[str] | None   = None
    tags:       list[str] | None   = None
    references: list[dict] | None  = None
    vector:     list[float] | None = None


@dataclass
class Link:
    source:          str
    target:          str
    link_type:       LinkType
    weight:          Weight
    rationale:       str
    created_turn:    int
    last_active_turn: int
    inactive_turns:  int = 0
    co_activation_count: int = 0   # Hebbian reinforcement counter (E2.1)


@dataclass
class Graph:
    nodes:              list[Node]  = field(default_factory=list)
    links:              list[Link]  = field(default_factory=list)
    turn_count:         int         = 0
    session_id:         str         = ""
    last_sentiment:     Sentiment   = "neutral"
    last_topic:         str         = ""
    last_keywords:      list[str]   = field(default_factory=list)
    compressed_summary: str         = ""
    pruned_count:       int         = 0

    # internal bookkeeping — not serialised
    _node_map: dict[str, Node] = field(default_factory=dict)
    _dirty:    bool            = field(default=False)

    # --- Incremental-save change tracking (T12) -----------------------------
    # These record *what* changed since the last save so save_sqlite() can
    # upsert/delete only the delta instead of rewriting the whole graph every
    # turn (cost O(delta) vs O(graph), and — crucially for a shared Turso Cloud
    # store, T11 option B — never a global ``DELETE FROM`` wipe that would drop
    # rows written concurrently by other writers).
    #   nodes  -> keyed by normalized keyword
    #   links  -> keyed by the (source, target, link_type) tuple
    _dirty_nodes:   set = field(default_factory=set)   # keywords to upsert
    _dirty_vectors: set = field(default_factory=set)   # keywords whose vector changed
    _dirty_links:   set = field(default_factory=set)   # link keys to upsert
    _removed_nodes: set = field(default_factory=set)   # keywords to delete
    _removed_links: set = field(default_factory=set)   # link keys to delete
    # Archived nodes (merged near-duplicates / dropped orphans) — recoverable,
    # flushed to the _graveyard table on save, then cleared. (E1.2/E1.3)
    _graveyard: list = field(default_factory=list)
    # Save mode for the next write:
    #   _needs_full_write  — upsert EVERY in-memory row (not just the dirty delta),
    #     additively (no deletes). Needed when the store may be missing rows we
    #     hold: a fresh graph, or a context warm-started from the seed DB. Safe on
    #     a shared store because it never deletes.
    #   _needs_diff_delete — reconcile: absolute upsert-all + delete store rows no
    #     longer in memory. ONLY for structural ops (merge). This is the single
    #     place a save may delete another writer's rows, so it is never the default.
    # Neither set → incremental atomic delta (the per-turn hot path).
    _needs_full_write:  bool = True
    _needs_diff_delete: bool = False
    # Schema DDL (CREATE/ALTER/index migrations) only needs to run once per
    # process per store — gate it so per-turn saves don't re-probe the schema
    # (important on remote Turso where every statement is an HTTP round-trip).
    _schema_ready:  bool = field(default=False)
    # Per-node salience snapshot at the last load/save. The incremental save
    # persists ``salience - baseline`` as an atomic relative delta (T11 Fase 2b)
    # so concurrent writers on the same node don't lose each other's increments.
    _salience_baseline: dict = field(default_factory=dict)
    # Hebbian cooldown: link key -> turn of its last co-activation count. In-memory
    # only (not persisted): losing it on restart merely allows one extra count after
    # a reload, which is harmless anti-noise state, not worth a schema column. (E2.1)
    _coact_cooldown: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Node map helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _norm(kw: str) -> str:
        """Normalize keyword: strip + lowercase. Used as map key."""
        return kw.strip().lower()

    def _rebuild_node_map(self) -> None:
        self._node_map.clear()
        for nd in self.nodes:
            self._node_map[self._norm(nd.keyword)] = nd

    def get_node(self, keyword: str) -> Node | None:
        return self._node_map.get(self._norm(keyword))

    # ------------------------------------------------------------------
    # Change tracking (T12)
    # ------------------------------------------------------------------

    @staticmethod
    def _link_key(lk: "Link") -> tuple:
        """Natural key of a link, matching the DB unique index."""
        return (lk.source, lk.target, lk.link_type)

    def mark_node_dirty(self, keyword: str) -> None:
        """Record that a node changed in place so the next save upserts it.

        Call this after mutating a node's fields directly (e.g. ``salience +=``)
        outside of ``add_node`` — otherwise the change is invisible to the
        incremental save and would be lost until a full reconcile.
        """
        kw = self._norm(keyword)
        self._dirty_nodes.add(kw)
        self._removed_nodes.discard(kw)
        self._dirty = True

    def mark_link_dirty(self, link: "Link") -> None:
        """Record that a link changed in place so the next save upserts it."""
        key = self._link_key(link)
        self._dirty_links.add(key)
        self._removed_links.discard(key)
        self._dirty = True

    def mark_vector_dirty(self, keyword: str) -> None:
        """Record that a node's vector must be (re)written on the next save — e.g.
        when it was computed lazily during search because it was missing on disk,
        so the same vector is never recomputed again (E1.1)."""
        kw = self._norm(keyword)
        self._dirty_vectors.add(kw)
        self._dirty = True

    def mark_full_rewrite(self) -> None:
        """Force the next save to reconcile the whole graph (upsert-all +
        delete-stale-by-diff). Use after structural mutations that rewrite
        node/link identity in bulk (e.g. merge), where tracking individual
        keys is unreliable."""
        self._needs_full_write = True
        self._needs_diff_delete = True
        self._dirty = True

    def _record_removed_node(self, keyword: str) -> None:
        kw = self._norm(keyword)
        self._removed_nodes.add(kw)
        self._dirty_nodes.discard(kw)
        self._dirty_vectors.discard(kw)
        self._dirty = True

    def _record_removed_link(self, link: "Link") -> None:
        key = self._link_key(link)
        self._removed_links.add(key)
        self._dirty_links.discard(key)
        self._dirty = True

    def _reset_tracking(self) -> None:
        """Clear all pending state → the next save is a plain incremental delta."""
        self._dirty = False
        self._needs_full_write = False
        self._needs_diff_delete = False
        self._dirty_nodes.clear()
        self._dirty_vectors.clear()
        self._dirty_links.clear()
        self._removed_nodes.clear()
        self._removed_links.clear()

    def _snapshot_salience(self) -> None:
        """Record current salience per node as the baseline for the next
        incremental save's relative delta (T11 Fase 2b)."""
        self._salience_baseline = {nd.keyword: nd.salience for nd in self.nodes}

    # ------------------------------------------------------------------
    # Mutation — node
    # ------------------------------------------------------------------

    def add_node(self, node: Node) -> None:
        # Normalize keyword on ingestion — prevents case duplicates
        node.keyword = self._norm(node.keyword)

        # Dedup: if a node with same normalized keyword already exists, merge salience
        existing = self._node_map.get(node.keyword)
        if existing:
            existing.salience = max(existing.salience, node.salience)
            self._dirty = True
            self._dirty_nodes.add(node.keyword)   # salience may have changed
            return

        if node.vector is None:
            node.vector = _get_vector(node.keyword)

        # cap: evict lowest-salience nodes when over MAX_NODES
        if len(self.nodes) >= MAX_NODES:
            self.nodes.sort(key=lambda n: n.salience)
            evict = self.nodes[:max(1, len(self.nodes) - MAX_NODES + 1)]
            evict_kws = {n.keyword for n in evict}
            self.nodes = [n for n in self.nodes if n.keyword not in evict_kws]
            # also drop links that reference evicted nodes (track the removals)
            kept_links = []
            for lk in self.links:
                if lk.source in evict_kws or lk.target in evict_kws:
                    self._record_removed_link(lk)
                else:
                    kept_links.append(lk)
            self.links = kept_links
            for kw in evict_kws:
                self._record_removed_node(kw)
            self._rebuild_node_map()

        self.nodes.append(node)
        self._node_map[node.keyword] = node
        self._dirty = True
        self._dirty_nodes.add(node.keyword)
        self._dirty_vectors.add(node.keyword)   # new node -> new vector row
        self._removed_nodes.discard(node.keyword)

    # ------------------------------------------------------------------
    # Mutation — link
    # ------------------------------------------------------------------

    def add_link(self, link: Link) -> None:
        # Normalize source/target to match node map keys
        link.source = self._norm(link.source)
        link.target = self._norm(link.target)
        # Never create a self-link. Central guard so NO path (auto-link, store_turn,
        # semantic flash, ...) can produce e.g. `react --analogy--> react`; _norm has
        # already lowercased/trimmed, so this also catches case variants (react/React).
        if link.source == link.target:
            return
        # dedup: skip if an equivalent link already exists in either direction
        for existing in self.links:
            if (existing.source == link.source and existing.target == link.target
                    and existing.link_type == link.link_type):
                # upgrade weight if new one is stronger
                if WEIGHT_ORDER.get(link.weight, 0) > WEIGHT_ORDER.get(existing.weight, 0):
                    existing.weight = link.weight
                    self.mark_link_dirty(existing)
                return
            if (existing.source == link.target and existing.target == link.source
                    and existing.link_type == link.link_type):
                if WEIGHT_ORDER.get(link.weight, 0) > WEIGHT_ORDER.get(existing.weight, 0):
                    existing.weight = link.weight
                    self.mark_link_dirty(existing)
                return
        self.links.append(link)
        self._dirty = True
        self.mark_link_dirty(link)

    def remove_link(self, link: Link) -> None:
        self.links.remove(link)
        self._record_removed_link(link)

    @staticmethod
    def _hebbian_weight(count: int) -> Weight:
        if count >= HEBBIAN_UPGRADE_STRONG:
            return "strong"
        if count >= HEBBIAN_UPGRADE_MEDIUM:
            return "medium"
        return "tangential"

    def reinforce_coactivation(self, keywords, turn: int | None = None) -> list[Link]:
        """Hebbian reinforcement (E2.1): 'neurons that fire together wire together'.

        For every EXISTING link whose both endpoints are among ``keywords`` (the
        keywords co-active this turn), bump ``co_activation_count`` — at most once
        per HEBBIAN_COOLDOWN turns per link, so a single chatty turn or rapid
        repeats can't inflate it — and promote the link weight monotonically at
        the thresholds. Only reinforces links that already exist (creating links
        stays with auto-link). Returns the links whose weight was upgraded."""
        turn = self.turn_count if turn is None else turn
        active = {self._norm(k) for k in keywords}
        if len(active) < 2:
            return []
        upgraded: list[Link] = []
        for lk in self.links:
            if lk.source not in active or lk.target not in active:
                continue
            key = self._link_key(lk)
            last = self._coact_cooldown.get(key)
            if last is not None and turn - last < HEBBIAN_COOLDOWN:
                continue
            lk.co_activation_count += 1
            self._coact_cooldown[key] = turn
            new_weight = self._hebbian_weight(lk.co_activation_count)
            if WEIGHT_ORDER.get(new_weight, 0) > WEIGHT_ORDER.get(lk.weight, 0):
                lk.weight = new_weight
                upgraded.append(lk)
            self.mark_link_dirty(lk)   # count changed → must be persisted
        return upgraded

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_active_links(self) -> list[Link]:
        return [lk for lk in self.links if lk.weight in ("strong", "medium")]

    def top_links(self, n: int = 5) -> list[Link]:
        active = self.get_active_links()
        return sorted(
            active,
            key=lambda lk: (WEIGHT_ORDER[lk.weight], -lk.inactive_turns),
            reverse=True,
        )[:n]

    def spreading_activation(self, seeds, k: int = 2, decay: float = 0.5,
                             min_activation: float = 0.01) -> list[tuple[str, float]]:
        """Spread activation from ``seeds`` along links, k hops out (E2.3).

        Each hop's contribution is ``activation × link_strength × salience_factor ×
        decay``: link_strength comes from the (Hebbian-promoted, E2.1) weight, the
        salience_factor makes salient nodes act as activation hubs, and ``decay``
        (<1) plus small ``k`` keep it from flooding. Returns the reached NON-seed
        nodes ranked by accumulated activation — the top one is the associative
        stimulus, surfacing even without a direct vector match. Pure graph walk."""
        seed_set = {self._norm(s) for s in seeds}
        seed_set = {s for s in seed_set if self.get_node(s) is not None}
        if not seed_set:
            return []
        max_sal = max((nd.salience for nd in self.nodes), default=0) or 1
        adj: dict[str, list[tuple[str, str]]] = {}
        for lk in self.links:
            adj.setdefault(lk.source, []).append((lk.target, lk.weight))
            adj.setdefault(lk.target, []).append((lk.source, lk.weight))

        activation = {s: 1.0 for s in seed_set}
        frontier = dict(activation)
        for _hop in range(k):
            nxt: dict[str, float] = {}
            for src, act in frontier.items():
                for other, weight in adj.get(src, ()):
                    strength = WEIGHT_ORDER.get(weight, 0) / 3.0
                    nd = self.get_node(other)
                    if nd is None or strength == 0:
                        continue
                    contrib = act * strength * (1.0 + nd.salience / max_sal) * decay
                    if contrib < min_activation:
                        continue
                    activation[other] = activation.get(other, 0.0) + contrib
                    nxt[other] = nxt.get(other, 0.0) + contrib
            frontier = nxt
            if not frontier:
                break
        out = [(kw, round(a, 4)) for kw, a in activation.items() if kw not in seed_set]
        out.sort(key=lambda x: -x[1])
        return out

    # ------------------------------------------------------------------
    # Decay / pruning
    # ------------------------------------------------------------------

    def prune_tangential(self) -> int:
        kept, removed_links = [], []
        for lk in self.links:
            if lk.weight == "tangential" and lk.inactive_turns > TANGENTIAL_EXPIRY_TURNS:
                removed_links.append(lk)
            else:
                kept.append(lk)
        self.links = kept
        removed = len(removed_links)
        if removed:
            for lk in removed_links:
                self._record_removed_link(lk)
            self.pruned_count += removed
            self._dirty = True
        return removed

    def increment_inactivity(self, active_sources: set[str]) -> None:
        # NOTE (T12 / Fase 2): every link's ``inactive_turns`` changes each turn,
        # so every link is marked dirty and re-upserted per turn. This still
        # avoids the destructive global DELETE, but doesn't shrink link writes.
        # A future optimization (Fase 2) is to stop persisting inactive_turns and
        # derive it on load as ``turn_count - last_active_turn`` (an invariant
        # this loop preserves), so only the few *active* links need a write.
        for lk in self.links:
            if lk.source in active_sources or lk.target in active_sources:
                lk.inactive_turns = 0
                lk.last_active_turn = self.turn_count
            else:
                lk.inactive_turns += 1
            self._dirty_links.add(self._link_key(lk))
        for nd in self.nodes:
            if nd.keyword not in active_sources and nd.salience > 0:
                if (self.turn_count - nd.turn) > SALIENCE_DECAY_THRESHOLD:
                    nd.salience = max(0, nd.salience - SALIENCE_DECAY_AMOUNT)
                    self._dirty_nodes.add(nd.keyword)   # only decayed nodes
        self._dirty = True

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export(self) -> dict[str, Any]:
        return {
            "session_id":   self.session_id,
            "turn_count":   self.turn_count,
            "nodes":        [{k: v for k, v in nd.__dict__.items() if k != "vector"} for nd in self.nodes],
            "links":        [lk.__dict__ for lk in self.links],
            "pruned_count": self.pruned_count,
        }

    # ------------------------------------------------------------------
    # Persistence — SQLite
    # ------------------------------------------------------------------

    # -- row builders -------------------------------------------------------

    def _node_row(self, nd: Node, context: str) -> tuple:
        return (context, nd.keyword, nd.turn, nd.topic, nd.domain, nd.sentiment, nd.salience,
                json.dumps(nd.entities or []), json.dumps(nd.tags or []),
                json.dumps(nd.references or []))

    def _link_row(self, lk: Link, context: str) -> tuple:
        return (context, lk.source, lk.target, lk.link_type, lk.weight, lk.rationale,
                lk.created_turn, lk.last_active_turn, lk.inactive_turns,
                lk.co_activation_count)

    def _vec_row(self, nd: Node, context: str) -> tuple:
        vec = nd.vector if nd.vector is not None else _get_vector(nd.keyword)
        return (context, nd.keyword, pack_vector(vec), VECTOR_DIM)

    @staticmethod
    def _table_cols(conn, table: str) -> set:
        return {c[1] for c in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    def _ensure_schema(self, conn, context: str) -> None:
        """Idempotently migrate an existing store to the context-aware schema:
        add the ``context`` column, swap single-column unique indexes for
        composite ``(context, ...)`` ones, and rebuild ``node_vectors`` whose
        primary key must become ``(context, keyword)``.

        Legacy rows (written before contexts existed, or by an older version)
        are backfilled with ``context`` — for a local per-file store that is
        exactly this file's context; for a fresh shared remote DB there are no
        such rows. Runs once per process (gated by ``_schema_ready``)."""
        # nodes: add context, composite unique index. When the column is
        # missing the store predates contexts — a local single-context file —
        # so ALL its rows belong to this context and are stamped accordingly
        # (ALTER's DEFAULT 'default' would otherwise mislabel a non-default file).
        # When the column already exists (remote shared store, or an already
        # migrated file) rows carry their own contexts and must NOT be rewritten.
        if "context" not in self._table_cols(conn, "nodes"):
            conn.execute("ALTER TABLE nodes ADD COLUMN context TEXT DEFAULT 'default'")
            conn.execute("UPDATE nodes SET context=?", (context,))
        conn.execute("DROP INDEX IF EXISTS idx_nodes_keyword")
        self._create_unique(conn, "idx_nodes_ctx_kw", "nodes", "context, keyword",
                            dedupe_group="context, keyword")

        # links: same one-context-per-legacy-file backfill rule
        if "context" not in self._table_cols(conn, "links"):
            conn.execute("ALTER TABLE links ADD COLUMN context TEXT DEFAULT 'default'")
            conn.execute("UPDATE links SET context=?", (context,))
        # E2.1: Hebbian counter column (older stores lack it)
        if "co_activation_count" not in self._table_cols(conn, "links"):
            conn.execute("ALTER TABLE links ADD COLUMN co_activation_count INTEGER DEFAULT 0")
        conn.execute("DROP INDEX IF EXISTS idx_links_unique")
        self._create_unique(conn, "idx_links_ctx", "links",
                            "context, source, target, link_type",
                            dedupe_group="context, source, target, link_type")

        # node_vectors: primary key must become (context, keyword) -> rebuild
        if "context" not in self._table_cols(conn, "node_vectors"):
            conn.execute("ALTER TABLE node_vectors RENAME TO node_vectors_legacy")
            conn.execute("""CREATE TABLE node_vectors (
                context   TEXT NOT NULL DEFAULT 'default',
                keyword   TEXT NOT NULL,
                embedding BLOB NOT NULL,
                dim       INTEGER NOT NULL,
                PRIMARY KEY (context, keyword))""")
            conn.execute("INSERT OR IGNORE INTO node_vectors (context, keyword, embedding, dim) "
                         "SELECT ?, keyword, embedding, dim FROM node_vectors_legacy", (context,))
            conn.execute("DROP TABLE node_vectors_legacy")

    @staticmethod
    def _create_unique(conn, name: str, table: str, cols: str, *, dedupe_group: str) -> None:
        """Create a UNIQUE index, deduping pre-existing conflicting rows first."""
        try:
            conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON {table}({cols})")
        except Exception:
            conn.execute(f"DELETE FROM {table} WHERE id NOT IN "
                         f"(SELECT MIN(id) FROM {table} GROUP BY {dedupe_group})")
            conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON {table}({cols})")

    # ------------------------------------------------------------------
    # Consolidation (E1.2) — merge near-duplicates, archive to _graveyard
    # ------------------------------------------------------------------

    def consolidate(self, sim_threshold: float = 0.85,
                    protect_salience: "int | None" = None,
                    turn: "int | None" = None,
                    drop_orphans: bool = False,
                    orphan_salience: int = 2,
                    orphan_inactive: int = 10) -> list[dict]:
        """Merge near-duplicate nodes (cosine > sim_threshold) into a survivor.

        Survivor = shorter keyword (tie -> higher salience). Salience is summed,
        links are rewired+deduped, self-loops dropped, and the absorbed node is
        archived into ``self._graveyard`` (recoverable). Nodes whose salience is
        >= protect_salience are never absorbed (preserve what matters). Idempotent:
        a second call after the duplicates are gone is a no-op. Returns a report.
        """
        from itertools import combinations
        report: list[dict] = []
        turn = self.turn_count if turn is None else turn
        guard = 0
        merged_any = True
        while merged_any and guard < 10000:
            merged_any = False
            guard += 1
            nodes = [n for n in self.nodes if n.vector is not None]
            found = None
            for a, b in combinations(nodes, 2):
                sim = _cos(a.vector, b.vector)
                if sim <= sim_threshold:
                    continue
                if (len(a.keyword), -a.salience) <= (len(b.keyword), -b.salience):
                    survivor, absorbed = a, b
                else:
                    survivor, absorbed = b, a
                if protect_salience is not None and absorbed.salience >= protect_salience:
                    continue
                found = (survivor, absorbed, sim)
                break
            if found:
                self._merge_into(*found, turn, report)
                merged_any = True
        if drop_orphans:
            self._drop_orphans(orphan_salience, orphan_inactive, turn, report)
        if report:
            # merge rewrites node/link identity in bulk -> reconcile on next save
            self.mark_full_rewrite()
        return report

    def _merge_into(self, survivor: Node, absorbed: Node, sim: float,
                    turn: int, report: list[dict]) -> None:
        survivor.salience += absorbed.salience
        a, s = absorbed.keyword, survivor.keyword
        for lk in self.links:
            if lk.source == a:
                lk.source = s
            if lk.target == a:
                lk.target = s
        # drop self-loops, then dedup links by natural key
        seen: set = set()
        uniq: list = []
        for lk in self.links:
            if lk.source == lk.target:
                continue
            key = self._link_key(lk)
            if key not in seen:
                seen.add(key)
                uniq.append(lk)
        self.links = uniq
        self._graveyard.append({
            "keyword": a, "salience": absorbed.salience, "domain": absorbed.domain,
            "reason": f"merged into {s} (cos={round(sim, 3)})", "turn": turn,
        })
        self.nodes = [n for n in self.nodes if n.keyword != a]
        self._rebuild_node_map()
        self.mark_node_dirty(s)
        report.append({"kept": s, "absorbed": a, "cos": round(sim, 3)})

    def _drop_orphans(self, orphan_salience: int, inactive_turns: int,
                      turn: int, report: list[dict]) -> None:
        """Archive low-salience nodes with no *active* link (E1.3). A node is an
        orphan if salience < orphan_salience AND it has no incident link, or all
        its incident links have been inactive for >= inactive_turns. Archived to
        _graveyard (recoverable); its dangling links are dropped too."""
        incident: dict[str, list[int]] = {}
        for lk in self.links:
            incident.setdefault(lk.source, []).append(lk.inactive_turns)
            incident.setdefault(lk.target, []).append(lk.inactive_turns)
        drop: set[str] = set()
        for nd in self.nodes:
            if nd.salience >= orphan_salience:
                continue
            inc = incident.get(nd.keyword)
            if not inc or min(inc) >= inactive_turns:
                drop.add(nd.keyword)
        if not drop:
            return
        for kw in drop:
            nd = self._node_map.get(kw)
            self._graveyard.append({
                "keyword": kw, "salience": nd.salience if nd else 0,
                "domain": nd.domain if nd else "",
                "reason": f"orphan drop (salience<{orphan_salience}, inactive>={inactive_turns})",
                "turn": turn,
            })
            report.append({"dropped": kw, "salience": nd.salience if nd else 0})
        self.links = [lk for lk in self.links if lk.source not in drop and lk.target not in drop]
        self.nodes = [nd for nd in self.nodes if nd.keyword not in drop]
        self._rebuild_node_map()

    def save_sqlite(self, path: str, *, context: str = "default", force: bool = False) -> None:
        """Persist to SQLite/Turso, scoped to ``context``. Skips the write
        entirely if the graph is not dirty (unless ``force=True``).

        All rows carry ``context`` so several contexts can coexist in one
        physical store (Turso Cloud, T11 option B) — every read/delete below is
        scoped ``WHERE context=?`` so a save for one context never touches
        another's rows. Locally each context is a separate file, so the column
        is redundant-but-harmless.

        Three modes (see the save-mode fields on ``Graph``):

        * **incremental delta** (default per-turn path) — atomic upsert of only
          the changed nodes/vectors/links (salience as a relative delta, weight
          promoted monotonically) + targeted deletes. No diff-delete, so it's
          safe under concurrent writers on a shared store.
        * **additive full write** (``_needs_full_write``: fresh graph or a
          context warm-started from the seed) — same atomic upsert but over
          *every* in-memory row, so the store gets rows it's missing. Still no
          diff-delete.
        * **reconcile** (``_needs_diff_delete``: merge only) — absolute upsert
          of all rows + delete this context's store rows no longer in memory.
          The one mode that may delete another writer's rows; never the default.
        """
        if not self._dirty and not force:
            return

        conn = _db.connect(path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            if not self._schema_ready:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
                    CREATE TABLE IF NOT EXISTS nodes (
                        id      INTEGER PRIMARY KEY AUTOINCREMENT,
                        context TEXT DEFAULT 'default',
                        keyword TEXT, turn INTEGER, topic TEXT,
                        domain  TEXT, sentiment TEXT, salience INTEGER,
                        entities TEXT DEFAULT '[]',
                        tags     TEXT DEFAULT '[]',
                        refs     TEXT DEFAULT '[]');
                    CREATE TABLE IF NOT EXISTS node_vectors (
                        context   TEXT NOT NULL DEFAULT 'default',
                        keyword   TEXT NOT NULL,
                        embedding BLOB NOT NULL,
                        dim       INTEGER NOT NULL,
                        PRIMARY KEY (context, keyword));
                    CREATE TABLE IF NOT EXISTS links (
                        id               INTEGER PRIMARY KEY AUTOINCREMENT,
                        context          TEXT DEFAULT 'default',
                        source           TEXT, target TEXT, link_type TEXT, weight TEXT,
                        rationale        TEXT, created_turn INTEGER,
                        last_active_turn INTEGER, inactive_turns INTEGER,
                        co_activation_count INTEGER DEFAULT 0);
                    CREATE INDEX IF NOT EXISTS idx_links_source ON links(source);
                    CREATE INDEX IF NOT EXISTS idx_links_target ON links(target);
                    CREATE INDEX IF NOT EXISTS idx_links_turn   ON links(created_turn);
                """)
                # Migration: add legacy JSON columns if missing
                for col in ("entities", "tags", "refs"):
                    try:
                        conn.execute(f"ALTER TABLE nodes ADD COLUMN {col} TEXT DEFAULT '[]'")
                    except Exception:
                        pass
                # Migration: context column + composite unique indexes + node_vectors PK
                self._ensure_schema(conn, context)
                self._schema_ready = True

            # Meta (cheap, always rewritten)
            conn.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", [
                ("session_id",     self.session_id),
                ("turn_count",     str(self.turn_count)),
                ("last_sentiment", self.last_sentiment),
                ("last_topic",     self.last_topic),
                ("last_keywords",  json.dumps(self.last_keywords)),
                ("embed_model",    EMBED_MODEL),
                ("embed_dim",      str(VECTOR_DIM)),
            ])

            # Flush archived nodes (E1.2/E1.3) — recoverable, then clear the buffer.
            if self._graveyard:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS _graveyard "
                    "(context TEXT, keyword TEXT, salience INTEGER, domain TEXT, "
                    "reason TEXT, turn INTEGER)")
                conn.executemany(
                    "INSERT INTO _graveyard (context, keyword, salience, domain, reason, turn) "
                    "VALUES (?,?,?,?,?,?)",
                    [(context, e["keyword"], int(e.get("salience", 0)), e.get("domain", ""),
                      e.get("reason", ""), int(e.get("turn", 0))) for e in self._graveyard],
                )
                self._graveyard = []

            if self._needs_diff_delete:
                self._save_reconcile(conn, context)      # merge: absolute + diff-delete
            else:
                self._save_delta(conn, context, all_rows=self._needs_full_write)

            conn.commit()
            self._reset_tracking()
            # New baseline: our in-memory salience is now what we've persisted our
            # delta against. The store may hold a higher value (concurrent adds);
            # that reconciles on the next load. Deltas are always relative to here.
            self._snapshot_salience()
        finally:
            conn.close()

    def _save_delta(self, conn, context: str, *, all_rows: bool) -> None:
        """Atomic, additive write — no row is ever deleted except the ones this
        graph explicitly removed. Salience is applied as a relative delta and
        link weight is promoted monotonically, so concurrent writers on the same
        node/link don't clobber each other.

        ``all_rows=False`` writes only the tracked dirty delta (per-turn hot
        path). ``all_rows=True`` writes every in-memory row (fresh graph, or a
        context warm-started from the seed) so the store gets rows it's missing —
        still without any diff-delete, so it's safe on a shared store.
        """
        # Targeted deletes first (a key removed-then-readded in the same cycle was
        # already discarded from the removed set by the mutation helpers).
        if self._removed_nodes:
            conn.executemany("DELETE FROM nodes WHERE context=? AND keyword=?",
                             [(context, k) for k in self._removed_nodes])
            conn.executemany("DELETE FROM node_vectors WHERE context=? AND keyword=?",
                             [(context, k) for k in self._removed_nodes])
        if self._removed_links:
            conn.executemany(
                "DELETE FROM links WHERE context=? AND source=? AND target=? AND link_type=?",
                [(context, s, t, lt) for (s, t, lt) in self._removed_links])

        node_set = self.nodes if all_rows else [nd for nd in self.nodes
                                                if nd.keyword in self._dirty_nodes]
        if node_set:
            rows = []
            for nd in node_set:
                delta = nd.salience - self._salience_baseline.get(nd.keyword, 0)
                rows.append(self._node_row(nd, context) + (delta,))
            conn.executemany(_NODE_UPSERT_ATOMIC, rows)

        vec_set = self.nodes if all_rows else [nd for nd in self.nodes
                                               if nd.keyword in self._dirty_vectors]
        if vec_set:
            conn.executemany(_VEC_UPSERT, [self._vec_row(nd, context) for nd in vec_set])

        link_set = self.links if all_rows else [lk for lk in self.links
                                                if self._link_key(lk) in self._dirty_links]
        if link_set:
            conn.executemany(_LINK_UPSERT_ATOMIC, [self._link_row(lk, context) for lk in link_set])

    def _save_reconcile(self, conn, context: str) -> None:
        """Structural reconcile (merge only): absolute upsert of every in-memory
        row, then delete this context's store rows no longer present in memory
        (delete-by-diff, scoped to this context). This is the one save that may
        delete another writer's rows, so it's reserved for deliberate structural
        edits, never the per-turn path."""
        if self.nodes:
            conn.executemany(_NODE_UPSERT, [self._node_row(nd, context) for nd in self.nodes])
            conn.executemany(_VEC_UPSERT,  [self._vec_row(nd,  context) for nd in self.nodes])
        if self.links:
            conn.executemany(_LINK_UPSERT, [self._link_row(lk, context) for lk in self.links])

        mem_kw      = {nd.keyword for nd in self.nodes}
        existing_kw = {row[0] for row in
                       conn.execute("SELECT keyword FROM nodes WHERE context=?", (context,))}
        stale_nodes = existing_kw - mem_kw
        if stale_nodes:
            conn.executemany("DELETE FROM nodes WHERE context=? AND keyword=?",
                             [(context, k) for k in stale_nodes])
            conn.executemany("DELETE FROM node_vectors WHERE context=? AND keyword=?",
                             [(context, k) for k in stale_nodes])

        mem_lk      = {self._link_key(lk) for lk in self.links}
        existing_lk = {(r[0], r[1], r[2]) for r in
                       conn.execute("SELECT source, target, link_type FROM links WHERE context=?",
                                    (context,))}
        stale_links = existing_lk - mem_lk
        if stale_links:
            conn.executemany(
                "DELETE FROM links WHERE context=? AND source=? AND target=? AND link_type=?",
                [(context, s, t, lt) for (s, t, lt) in stale_links])

    def load_sqlite(self, path: str, domain_filter: str | None = None,
                    *, context: str = "default", warm_start: bool = False) -> None:
        if not _db.REMOTE_TURSO and not os.path.exists(path):
            return
        conn = _db.connect(path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
            self.session_id    = meta.get("session_id", "")
            self.turn_count    = int(meta.get("turn_count", "0"))
            self.last_sentiment = meta.get("last_sentiment", "neutral")
            self.last_topic    = meta.get("last_topic", "")
            try:
                self.last_keywords = json.loads(meta.get("last_keywords", "[]"))
            except (json.JSONDecodeError, TypeError):
                self.last_keywords = []

            # Guard modello<->store: i vettori salvati con un modello diverso vivono
            # in uno spazio diverso e NON sono confrontabili col modello attivo. Se
            # non combaciano, ignora i vettori salvati (verranno ricalcolati col
            # modello attivo) e avvisa di rigenerare lo store con scripts/reembed.py.
            _stored_model = meta.get("embed_model")
            _stored_dim   = meta.get("embed_dim")
            _vec_incompatible = bool(
                (_stored_model and _stored_model != EMBED_MODEL)
                or (_stored_dim and _stored_dim != str(VECTOR_DIM))
            )
            if _vec_incompatible:
                import sys as _sys
                print(
                    f"neuron: store '{path}' ha vettori del modello '{_stored_model}' "
                    f"(dim {_stored_dim}) ma il modello attivo e' '{EMBED_MODEL}' "
                    f"(dim {VECTOR_DIM}). Vettori salvati IGNORATI (ricalcolati col "
                    f"modello attivo). Rigenera: python scripts/reembed.py",
                    file=_sys.stderr,
                )

            cols     = [c[1] for c in conn.execute("PRAGMA table_info(nodes)").fetchall()]
            has_extra   = "entities" in cols
            # Scope by context only when the store has the column — the seed DB
            # and legacy pre-context files don't, and must load unfiltered.
            has_context = "context" in cols
            base_sql  = ("SELECT keyword, turn, topic, domain, sentiment, salience, "
                         "entities, tags, refs FROM nodes")
            conds: list[str] = []
            params: list = []
            if has_context:
                conds.append("context=?"); params.append(context)
            if domain_filter:
                conds.append("domain=?"); params.append(domain_filter)
            where = (" WHERE " + " AND ".join(conds)) if conds else ""
            rows = conn.execute(f"{base_sql}{where} ORDER BY id", params).fetchall()

            self.nodes.clear()
            for row in rows:
                nd = Node(keyword=row[0], turn=row[1], topic=row[2],
                          domain=row[3], sentiment=row[4], salience=row[5])
                if has_extra and row[6]:
                    nd.entities   = json.loads(row[6]) if isinstance(row[6], str) else row[6]
                    nd.tags       = json.loads(row[7]) if isinstance(row[7], str) else row[7]
                    nd.references = json.loads(row[8]) if isinstance(row[8], str) else row[8]
                self.nodes.append(nd)

            # Load vectors (scoped by context when available)
            vec_map: dict[str, list[float]] = {}
            try:
                if _vec_incompatible:
                    vrows = []
                elif has_context:
                    vrows = conn.execute("SELECT keyword, embedding FROM node_vectors "
                                         "WHERE context=?", (context,))
                else:
                    vrows = conn.execute("SELECT keyword, embedding FROM node_vectors")
                for row in vrows:
                    vec_map[row[0]] = unpack_vector(row[1])
            except Exception:
                pass
            for nd in self.nodes:
                nd.vector = vec_map.get(nd.keyword) or _get_vector(nd.keyword)
            self._rebuild_node_map()

            # Load links (scoped by context when available)
            self.links.clear()
            node_kws = {nd.keyword for nd in self.nodes}
            # co_activation_count is absent in legacy/seed stores → select it only
            # when present, else default to 0 (E2.1).
            link_cols = [c[1] for c in conn.execute("PRAGMA table_info(links)").fetchall()]
            coact_sql = "co_activation_count" if "co_activation_count" in link_cols else "0"
            select = ("SELECT source, target, link_type, weight, rationale, "
                      f"created_turn, last_active_turn, inactive_turns, {coact_sql} FROM links")
            if has_context:
                link_rows = conn.execute(select + " WHERE context=? ORDER BY id", (context,))
            else:
                link_rows = conn.execute(select + " ORDER BY id")
            for row in link_rows:
                if domain_filter and row[0] not in node_kws and row[1] not in node_kws:
                    continue
                link = Link(
                    source=row[0], target=row[1], link_type=row[2], weight=row[3],
                    rationale=row[4] or "",
                    created_turn=row[5] or 0,
                    last_active_turn=row[6] or 0,
                    inactive_turns=row[7] or 0,
                    co_activation_count=row[8] or 0,
                )
                self.links.append(link)
        finally:
            conn.close()
        self._rebuild_node_map()
        self._reset_tracking()
        # Normal load (from our own store): the loaded rows already live there, so
        # the next save only needs the dirty delta (incremental, atomic — safe
        # under concurrent writers). A warm start from a DIFFERENT store (the seed
        # DB) means the save target is missing these rows, so the next save must
        # write them all — additively, never diff-deleting.
        self._needs_full_write = warm_start
        self._snapshot_salience()   # baseline for atomic relative-salience saves
