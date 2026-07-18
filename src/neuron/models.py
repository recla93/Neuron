"""Neuron data models: Node, Link, Graph.

Separated from server.py to break the circular import with registry.py.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import struct
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from neuron import db as _db

log = logging.getLogger("neuron.models")

__all__ = [
    "Node", "Link", "Graph", "compute_health",
    "register_embed_fn", "pack_vector", "unpack_vector",
    "WEIGHT_ORDER", "VECTOR_DIM", "MAX_NODES", "TANGENTIAL_EXPIRY_TURNS",
]

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
# Tunables read from the environment with the historical value as the default,
# so operators can adjust memory dynamics per deployment without code changes
# (P1 #9). Malformed values silently fall back to the default.
from neuron.config import env_int as _env_int, env_float as _env_float

TANGENTIAL_EXPIRY_TURNS  = _env_int("NEURON_TANGENTIAL_EXPIRY_TURNS", 5)
WEIGHT_ORDER             = {"strong": 3, "medium": 2, "tangential": 1}
SALIENCE_DECAY_THRESHOLD = _env_int("NEURON_SALIENCE_DECAY_THRESHOLD", 5)
SALIENCE_DECAY_AMOUNT    = _env_int("NEURON_SALIENCE_DECAY_AMOUNT", 1)
# Hebbian reinforcement (E2.1): links whose endpoints co-occur in a turn get their
# co_activation_count bumped (at most once per HEBBIAN_COOLDOWN turns, so a single
# chatty turn or rapid repeats can't inflate it), and the weight is promoted at the
# thresholds below. Promotion is monotone (never a downgrade), reusing the atomic
# weight CASE from T11.
HEBBIAN_COOLDOWN         = _env_int("NEURON_HEBBIAN_COOLDOWN", 2)   # min turns between two counts on the same link
HEBBIAN_UPGRADE_MEDIUM   = _env_int("NEURON_HEBBIAN_UPGRADE_MEDIUM", 3)   # co_activation_count promoting tangential -> medium
HEBBIAN_UPGRADE_STRONG   = _env_int("NEURON_HEBBIAN_UPGRADE_STRONG", 8)   # co_activation_count promoting medium    -> strong
# Drift links (E3.1): implicit cross-context associations formed without a rationale
# when a node from another *visited* context surfaces alongside the current keywords.
# Highest noise risk, so the rules are strict: born tangential, one per DRIFT_COOLDOWN
# turns, and pruned faster than intra-context tangentials (DRIFT_EXPIRY_TURNS < 5).
DRIFT_COOLDOWN           = _env_int("NEURON_DRIFT_COOLDOWN", 5)   # min turns between forming/reinforcing the same drift
DRIFT_EXPIRY_TURNS       = _env_int("NEURON_DRIFT_EXPIRY_TURNS", 3)   # inactive turns before a drift link is pruned
# Sleep-mode (E3.3/E3.4): when a graph is loaded after being idle longer than
# SLEEP_IDLE_SECONDS, consolidate it and pre-stage the top stimulus so pre_turn
# can serve it "warm". The staged stimulus is only served while fresher than
# STAGE_FRESH_SECONDS (else it's dropped as stale).
SLEEP_IDLE_SECONDS       = _env_int("NEURON_SLEEP_IDLE_SECONDS", 1800)     # 30 min idle → sleep-mode on next load
STAGE_FRESH_SECONDS      = _env_int("NEURON_STAGE_FRESH_SECONDS", 6 * 3600) # staged stimulus valid for 6h

# Episodic payload (T56): nodes carry compact FACTS, not just themes.
EPISODES_PER_NODE = _env_int("NEURON_EPISODES_PER_NODE", 5)     # cap per node; oldest dropped (consolidation-lite)
EPISODE_MAX_CHARS = _env_int("NEURON_EPISODE_MAX_CHARS", 200)   # one compact sentence, ~40 tokens
# Embedding dimension. Default 384 (fastembed all-MiniLM-L6-v2 and the common
# 384-dim multilingual models). Overridable via NS_EMBED_DIM for a model with a
# different width — must match NS_EMBED_MODEL (see server._get_embedding guard).
VECTOR_DIM               = int(os.environ.get("NS_EMBED_DIM", "384"))
# Nome del modello di embedding attivo (deve combaciare con i vettori nello
# store: vettori di modelli diversi non sono confrontabili — vedi load_sqlite).
EMBED_MODEL              = os.environ.get("NS_EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2").strip()
MAX_NODES                = _env_int("NEURON_MAX_NODES", 500)   # evict lowest-salience nodes beyond this cap

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


def compute_health(nodes, links, pruned_count: int, turn_count: int) -> dict:
    """Single source of truth for graph health metrics (P1 #8).

    The status/summary tools each recomputed strong/medium ratios, pruned ratio
    and nodes-per-turn inline; they now all read the same numbers from here.
    Works on any link objects exposing ``.weight`` / ``.link_type``.
    """
    total = len(links)
    strong = sum(1 for lk in links if lk.weight == "strong")
    medium = sum(1 for lk in links if lk.weight == "medium")
    return {
        "total":             total,
        "strong":            strong,
        "medium":            medium,
        "tangential":        total - strong - medium,
        "types":             len({lk.link_type for lk in links}),
        "pruned":            pruned_count,
        "strong_medium_pct": ((strong + medium) / total * 100) if total else 0.0,
        "pruned_pct":        (pruned_count / (total + pruned_count) * 100)
                             if (total + pruned_count) else 0.0,
        "nodes_per_turn":    len(nodes) / max(turn_count, 1),
    }


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
    "entities, tags, refs, trust) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
    "ON CONFLICT(context, keyword) DO UPDATE SET "
    "turn=excluded.turn, topic=excluded.topic, domain=excluded.domain, "
    "sentiment=excluded.sentiment, salience=excluded.salience, "
    "entities=excluded.entities, tags=excluded.tags, refs=excluded.refs, "
    "trust=excluded.trust"
)

_LINK_UPSERT = (
    "INSERT INTO links (context, source, target, link_type, weight, rationale, "
    "created_turn, last_active_turn, inactive_turns, co_activation_count, target_context) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
    "ON CONFLICT(context, source, target, link_type) DO UPDATE SET "
    "weight=excluded.weight, rationale=excluded.rationale, "
    "created_turn=excluded.created_turn, last_active_turn=excluded.last_active_turn, "
    "inactive_turns=excluded.inactive_turns, "
    "co_activation_count=excluded.co_activation_count, "
    "target_context=excluded.target_context"
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
    "entities, tags, refs, trust) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
    "ON CONFLICT(context, keyword) DO UPDATE SET "
    "turn=excluded.turn, topic=excluded.topic, domain=excluded.domain, "
    "sentiment=excluded.sentiment, salience=MAX(0, salience + ?), "
    # G2: refs NON viene toccato sul path concorrente — il blob JSON è legacy
    # (read-only), i ref nuovi vivono nella tabella `refs` (append-only).
    "entities=excluded.entities, tags=excluded.tags, "
    # B2/L1: trust segue lo stesso schema delta-relativo della salience —
    # due writer concorrenti sommano entrambi, nessun update perso.
    "trust=MAX(0, trust + ?)"
)

# Weight is promoted monotonically: a concurrent writer can only ever RAISE a
# link's weight (tangential < medium < strong), never silently downgrade it.
_WEIGHT_RANK = ("CASE {c} WHEN 'strong' THEN 3 WHEN 'medium' THEN 2 "
                "WHEN 'tangential' THEN 1 ELSE 0 END")
_LINK_UPSERT_ATOMIC = (
    "INSERT INTO links (context, source, target, link_type, weight, rationale, "
    "created_turn, last_active_turn, inactive_turns, co_activation_count, target_context) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
    "ON CONFLICT(context, source, target, link_type) DO UPDATE SET "
    "weight=CASE WHEN " + _WEIGHT_RANK.format(c="excluded.weight") + " > "
    + _WEIGHT_RANK.format(c="weight") + " THEN excluded.weight ELSE weight END, "
    "rationale=excluded.rationale, created_turn=excluded.created_turn, "
    "last_active_turn=excluded.last_active_turn, inactive_turns=excluded.inactive_turns, "
    # Hebbian count only ever grows: MAX() so a stale concurrent writer can't lower it.
    "co_activation_count=MAX(co_activation_count, excluded.co_activation_count), "
    "target_context=excluded.target_context"
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
    trust:      float          = 0.0   # B2 — feedback confermato (confirm/confidence)
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
    target_context:  str | None = None   # set on drift links → context the target lives in (E3.1)


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
    # Episodic payload (T56): keyword -> [{"turn": int, "text": str}], capped at
    # EPISODES_PER_NODE. Nodes carry FACTS ("we chose https because wss got 400"),
    # not just themes. Persisted in the `episodes` table, scoped by context.
    episodes: dict = field(default_factory=dict)
    _dirty_episodes:   set = field(default_factory=set)   # (keyword, turn) to upsert
    _removed_episodes: set = field(default_factory=set)   # (keyword, turn) to delete
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
    _trust_baseline:    dict = field(default_factory=dict)   # B2, stesso pattern
    # Hebbian cooldown: link key -> turn of its last co-activation count. In-memory
    # only (not persisted): losing it on restart merely allows one extra count after
    # a reload, which is harmless anti-noise state, not worth a schema column. (E2.1)
    _coact_cooldown: dict = field(default_factory=dict)
    # Drift cooldown: (source, target, target_context) -> last turn formed/reinforced.
    # In-memory anti-noise, same rationale as _coact_cooldown. (E3.1)
    _drift_cooldown: dict = field(default_factory=dict)
    # Sleep-mode / pre-staging (E3.3/E3.4). staged_stimulus + its timestamp persist
    # in meta so a background/previous run can leave a warm stimulus for pre_turn;
    # _loaded_ts is the last-active timestamp read at load (transient).
    staged_stimulus: "str | None" = None
    _staged_ts: "float | None" = None
    _loaded_ts: "float | None" = None

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
        self._dirty_episodes.clear()
        self._removed_episodes.clear()

    def _snapshot_salience(self) -> None:
        """Record current salience/trust per node as the baseline for the next
        incremental save's relative delta (T11 Fase 2b, B2)."""
        self._salience_baseline = {nd.keyword: nd.salience for nd in self.nodes}
        self._trust_baseline = {nd.keyword: nd.trust for nd in self.nodes}

    # ------------------------------------------------------------------
    # Episodes (T56) — compact facts attached to a node
    # ------------------------------------------------------------------

    def add_episode(self, keyword: str, text: str, turn: "int | None" = None) -> bool:
        """Attach one compact fact sentence to ``keyword`` for this turn.

        Capped at EPISODES_PER_NODE per node (oldest dropped, its row deleted at
        the next save). One episode per (keyword, turn): a second call in the
        same turn overwrites. Returns False when the node doesn't exist —
        episodes only ever decorate real concepts."""
        keyword = self._norm(keyword)
        if self._node_map.get(keyword) is None:
            return False
        text = (text or "").strip()[:EPISODE_MAX_CHARS]
        if not text:
            return False
        turn = self.turn_count if turn is None else turn
        eps = self.episodes.setdefault(keyword, [])
        eps[:] = [e for e in eps if e["turn"] != turn]
        eps.append({"turn": turn, "text": text})
        eps.sort(key=lambda e: e["turn"])
        while len(eps) > EPISODES_PER_NODE:
            old = eps.pop(0)
            self._removed_episodes.add((keyword, old["turn"]))
            self._dirty_episodes.discard((keyword, old["turn"]))
        self._dirty_episodes.add((keyword, turn))
        self._removed_episodes.discard((keyword, turn))
        self._dirty = True
        return True

    def recent_episodes(self, keyword: str, n: int = 2) -> list[str]:
        """Latest ``n`` fact texts for a node, newest first (for pre_turn)."""
        eps = self.episodes.get(self._norm(keyword), [])
        return [e["text"] for e in sorted(eps, key=lambda e: -e["turn"])[:n]]

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
            existing.trust = max(existing.trust, node.trust)
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

    def form_drift_link(self, source: str, target: str, target_context: str,
                        turn: int | None = None) -> "Link | None":
        """Form (or gently reinforce) an implicit cross-context drift link (E3.1):
        ``source`` (this context) ↔ ``target`` which lives in ``target_context`` —
        an association discovered without an explicit rationale. Born tangential,
        at most once per DRIFT_COOLDOWN turns, and it reuses the Hebbian counter so
        a bridge noticed repeatedly strengthens. Caller must pass a *visited*
        ``target_context``. Returns the link, or None on cooldown / invalid input."""
        source = self._norm(source)
        target = self._norm(target)
        if not source or not target or not target_context:
            return None
        if source == target and target_context is None:   # never a real self-drift
            return None
        turn = self.turn_count if turn is None else turn
        key = (source, target, target_context)
        last = self._drift_cooldown.get(key)
        if last is not None and turn - last < DRIFT_COOLDOWN:
            return None
        self._drift_cooldown[key] = turn
        for lk in self.links:
            if (lk.link_type == "drift" and lk.source == source
                    and lk.target == target and lk.target_context == target_context):
                lk.co_activation_count += 1
                lk.inactive_turns = 0
                lk.last_active_turn = turn
                new_weight = self._hebbian_weight(lk.co_activation_count)
                if WEIGHT_ORDER.get(new_weight, 0) > WEIGHT_ORDER.get(lk.weight, 0):
                    lk.weight = new_weight
                self.mark_link_dirty(lk)
                return lk
        lk = Link(source=source, target=target, link_type="drift", weight="tangential",
                  rationale="", created_turn=turn, last_active_turn=turn,
                  co_activation_count=1, target_context=target_context)
        self.links.append(lk)
        self._dirty = True
        self.mark_link_dirty(lk)
        return lk

    def drift_links(self) -> list["Link"]:
        """All active cross-context drift links (E3.1/E3.2)."""
        return [lk for lk in self.links if lk.link_type == "drift"]

    # ------------------------------------------------------------------
    # Sleep-mode / pre-staging (E3.3 / E3.4)
    # ------------------------------------------------------------------

    def sleep_maybe(self, now: "float | None" = None,
                    idle_threshold: float = SLEEP_IDLE_SECONDS,
                    do_consolidate: bool = False) -> "dict | None":
        """If this graph was loaded after being idle > idle_threshold, run
        sleep-mode: optionally consolidate (merge near-dupes + drop orphans),
        then pre-stage the top stimulus (spreading activation from the most
        salient nodes) so the next pre_turn can serve it warm (E3.4). Returns a
        summary, or None if not idle / no last-active timestamp. This is the
        "consolidate at startup if inactive" fallback when no scheduler drives it."""
        now = time.time() if now is None else now
        if self._loaded_ts is None or (now - self._loaded_ts) <= idle_threshold:
            return None
        actions = 0
        if do_consolidate:
            actions = len(self.consolidate(drop_orphans=True))
        # Seed from the last turn's focus, or fall back to the single most-salient
        # node — a single anchor leaves the rest of the graph as activation targets
        # (seeding from all top-salient nodes would starve a tiny graph of targets).
        top = sorted(self.nodes, key=lambda n: -n.salience)
        seeds = self.last_keywords or ([top[0].keyword] if top else [])
        ranked = self.spreading_activation(seeds, k=2) if seeds else []
        if ranked:
            kw, act = ranked[0]
            self.staged_stimulus = f"{kw} (act={act:.2f})"
            self._staged_ts = now
        return {"consolidated": do_consolidate, "actions": actions,
                "staged": self.staged_stimulus}

    def take_staged_stimulus(self, now: "float | None" = None,
                             fresh: float = STAGE_FRESH_SECONDS) -> "str | None":
        """Return the pre-staged stimulus once if still fresh, then clear it
        (one-shot: the "while you were away" nudge). Stale → dropped, None (E3.4)."""
        now = time.time() if now is None else now
        s, ts = self.staged_stimulus, self._staged_ts
        self.staged_stimulus = None          # one-shot, whether served or dropped
        self._staged_ts = None
        if s and ts is not None and (now - ts) < fresh:
            return s
        return None

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_active_links(self) -> list[Link]:
        # Drift links are cross-context and surface only on deep get_context (E3.2),
        # never in the normal active-links view.
        return [lk for lk in self.links
                if lk.weight in ("strong", "medium") and lk.link_type != "drift"]

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
            if lk.link_type == "drift":
                continue   # drift targets live in another context — not this graph's walk
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

    def stimulus_candidates(self, seeds, k: int = 2, decay: float = 0.5,
                            min_activation: float = 0.01) -> list[dict]:
        """Balanced stimulus ranking (T66): activation × (1 + novelty bonuses).

        Stimuli must serve BOTH recall (useful memory, strong relations) and
        creative sparks (tangential/surprising connections) — so novelty is a
        BONUS multiplier, never a gate: a high-activation known neighbour still
        competes with a dormant cross-domain leap. Tracks the best path from a
        seed for interpretability ("java ⇢ servlet ⇢ CORS" fires the impulse;
        a bare keyword doesn't).

        Bonuses: dormancy (up to +0.5, silent nodes resurface), domain shift
        (+0.4 vs the seeds' majority domain), tangential edge in the path
        (+0.3, the fragile unexpected connection). Familiarity damping: a first
        edge heavily co-activated with the seeds (Hebbian count) is a synonym,
        not a surprise → mild penalty. Returns dicts sorted by score:
        {keyword, act, score, path, hops, reasons}."""
        seed_set = {self._norm(s) for s in seeds}
        seed_set = {s for s in seed_set if self.get_node(s) is not None}
        if not seed_set:
            return []
        max_sal = max((nd.salience for nd in self.nodes), default=0) or 1
        adj: dict[str, list] = {}
        for lk in self.links:
            if lk.link_type == "drift":
                continue
            adj.setdefault(lk.source, []).append((lk.target, lk))
            adj.setdefault(lk.target, []).append((lk.source, lk))

        seed_domains = [self.get_node(s).domain for s in seed_set]
        majority_dom = max(set(seed_domains), key=seed_domains.count) if seed_domains else None

        # Walk with best-path tracking: state per node = (act, path, tang, coact0)
        best: dict[str, tuple] = {s: (1.0, [s], False, 0) for s in seed_set}
        frontier = dict(best)
        acc: dict[str, float] = {}
        for _hop in range(k):
            nxt: dict[str, tuple] = {}
            for src, (act, path, tang, coact0) in frontier.items():
                for other, lk in adj.get(src, ()):
                    if other in path:
                        continue
                    strength = WEIGHT_ORDER.get(lk.weight, 0) / 3.0
                    nd = self.get_node(other)
                    if nd is None or strength == 0:
                        continue
                    contrib = act * strength * (1.0 + nd.salience / max_sal) * decay
                    if contrib < min_activation:
                        continue
                    acc[other] = acc.get(other, 0.0) + contrib
                    n_tang = tang or (lk.weight == "tangential")
                    n_coact0 = coact0 if len(path) > 1 else getattr(lk, "co_activation_count", 0)
                    prev = nxt.get(other)
                    if prev is None or contrib > prev[0]:
                        nxt[other] = (contrib, path + [other], n_tang, n_coact0)
                    if other not in best or contrib > best[other][0]:
                        best[other] = (contrib, path + [other], n_tang, n_coact0)
            frontier = nxt
            if not frontier:
                break

        out = []
        for kw, act in acc.items():
            if kw in seed_set:
                continue
            _, path, tang, coact0 = best[kw]
            nd = self.get_node(kw)
            dormancy = max(0, self.turn_count - nd.turn)
            reasons: list[str] = []
            bonus = 0.0
            if dormancy >= 6:
                bonus += 0.5 * min(dormancy / 12.0, 1.0)
                reasons.append(f"dormant {dormancy}t")
            if majority_dom and nd.domain != majority_dom and nd.domain != "general":
                bonus += 0.4
                reasons.append(f"→{nd.domain}")
            if tang:
                bonus += 0.3
                reasons.append("tangential")
            damping = 1.0 + 0.15 * min(coact0, 6)   # over-familiar first edge
            score = act * (1.0 + bonus) / damping
            if not reasons:
                reasons.append("recall")
            out.append({"keyword": kw, "act": round(act, 4), "score": round(score, 4),
                        "path": path, "hops": len(path) - 1, "reasons": reasons})
        out.sort(key=lambda c: -c["score"])
        return out

    # ------------------------------------------------------------------
    # Decay / pruning
    # ------------------------------------------------------------------

    def prune_tangential(self) -> int:
        kept, removed_links = [], []
        for lk in self.links:
            # Drift links are the noisiest, so they expire faster (E3.1).
            expiry = DRIFT_EXPIRY_TURNS if lk.link_type == "drift" else TANGENTIAL_EXPIRY_TURNS
            if lk.weight == "tangential" and lk.inactive_turns > expiry:
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
        # A1 (Piano 05, closes the T12/Fase 2 residual): only the links that are
        # ACTIVE this turn get persisted (their last_active_turn changes). The
        # in-memory ``inactive_turns`` of every other link still ticks up — the
        # runtime logic (prune, ordering, orphan drop) relies on it — but the
        # stored value is now derived at load time as
        # ``turn_count - last_active_turn`` (an invariant this loop preserves),
        # so inactive links no longer need an O(total links) re-upsert per turn.
        # On Turso Cloud that was O(L) network rows per turn.
        for lk in self.links:
            if lk.source in active_sources or lk.target in active_sources:
                lk.inactive_turns = 0
                lk.last_active_turn = self.turn_count
                self._dirty_links.add(self._link_key(lk))
            else:
                lk.inactive_turns += 1   # in-memory only; derived on load
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
                json.dumps(nd.references or []), nd.trust)

    def _link_row(self, lk: Link, context: str) -> tuple:
        return (context, lk.source, lk.target, lk.link_type, lk.weight, lk.rationale,
                lk.created_turn, lk.last_active_turn, lk.inactive_turns,
                lk.co_activation_count, lk.target_context)

    def _write_refs(self, conn, node_set, context: str) -> None:
        """G2 — refs come righe proprie: INSERT OR IGNORE su chiave naturale
        (context, keyword, path, project_id, by). Append di due writer = righe
        diverse, nessun clobber. Il blob JSON nodes.refs resta come legacy."""
        rows = [(context, nd.keyword, r.get("path", ""), r.get("project_id", ""),
                 r.get("by", ""))
                for nd in node_set for r in (nd.references or [])
                if isinstance(r, dict) and r.get("path")]
        if rows:
            conn.executemany(
                "INSERT OR IGNORE INTO refs (context, keyword, path, project_id, by) "
                "VALUES (?,?,?,?,?)", rows)

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
        # E3.1: drift link target-context column (older stores lack it)
        if "target_context" not in self._table_cols(conn, "links"):
            conn.execute("ALTER TABLE links ADD COLUMN target_context TEXT")
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
            # Early termination (P1 #7): with <2 candidates no pair can merge, and
            # if every node is protected nothing is absorbable — skip the whole
            # O(N^2) pair scan (common on the auto-consolidate / sleep path).
            if len(nodes) < 2:
                break
            # C2 — trust-aware: un nodo confermato vale salience + trust, quindi
            # un nodo poco saliente ma fidato non viene assorbito/droppato.
            if protect_salience is not None and all(
                    n.salience + n.trust >= protect_salience for n in nodes):
                break
            found = None
            for a, b in combinations(nodes, 2):
                sim = _cos(a.vector, b.vector)
                if sim <= sim_threshold:
                    continue
                if (len(a.keyword), -a.salience) <= (len(b.keyword), -b.salience):
                    survivor, absorbed = a, b
                else:
                    survivor, absorbed = b, a
                if protect_salience is not None and (
                        absorbed.salience + absorbed.trust >= protect_salience):
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
        survivor.trust = max(survivor.trust, absorbed.trust)
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
            if nd.salience + nd.trust >= orphan_salience:   # C2: trust protegge
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

    # ------------------------------------------------------------------
    # Per-user session-state sidecar (T11 P1)
    # ------------------------------------------------------------------
    # On a shared remote Turso store the graph (nodes/links/vectors) is shared,
    # but each user's session working-state (turn counter, staged stimulus,
    # last-topic, ...) is personal and must NOT go in the store's global meta.
    # It lives in a small local JSON file next to where the local graph file
    # would sit — the graphs dir is already per-user, like ``_cross_links.json``.

    @staticmethod
    def _session_sidecar_path(path: str, context: str) -> str:
        base, _ext = os.path.splitext(path)
        return base + ".session.json"

    def _save_session_sidecar(self, path: str, context: str, data: dict) -> None:
        sp = self._session_sidecar_path(path, context)
        try:
            d = os.path.dirname(sp)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(sp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except OSError:
            pass

    def _load_session_sidecar(self, path: str, context: str) -> dict:
        sp = self._session_sidecar_path(path, context)
        try:
            with open(sp, encoding="utf-8") as f:
                loaded = json.load(f)
            return loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError, ValueError):
            return {}

    def _create_store_schema(self, conn, context: str) -> None:
        """Create/migrate the store schema (tables + indexes). Idempotent — safe to
        run repeatedly. Shared by ``save_sqlite`` (lazy, once per process) and by the
        one-shot cloud initializer ``ensure_schema`` so a shared Turso Cloud DB can
        be migrated ONCE up front instead of racing across clients on first write
        (P4)."""
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS nodes (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                context TEXT DEFAULT 'default',
                keyword TEXT, turn INTEGER, topic TEXT,
                domain  TEXT, sentiment TEXT, salience INTEGER,
                entities TEXT DEFAULT '[]',
                tags     TEXT DEFAULT '[]',
                refs     TEXT DEFAULT '[]',
                trust    REAL DEFAULT 0);
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
                co_activation_count INTEGER DEFAULT 0,
                target_context   TEXT);
            CREATE TABLE IF NOT EXISTS _graveyard (
                context TEXT, keyword TEXT, salience INTEGER, domain TEXT,
                reason TEXT, turn INTEGER);
            CREATE TABLE IF NOT EXISTS refs (
                context    TEXT NOT NULL DEFAULT 'default',
                keyword    TEXT NOT NULL,
                path       TEXT NOT NULL,
                project_id TEXT NOT NULL DEFAULT '',
                by         TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (context, keyword, path, project_id, by));
            CREATE TABLE IF NOT EXISTS episodes (
                context TEXT NOT NULL DEFAULT 'default',
                keyword TEXT NOT NULL,
                turn    INTEGER NOT NULL,
                text    TEXT NOT NULL,
                PRIMARY KEY (context, keyword, turn));
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
        try:   # B2 migration
            conn.execute("ALTER TABLE nodes ADD COLUMN trust REAL DEFAULT 0")
        except Exception:
            pass
        # Migration: context column + composite unique indexes + node_vectors PK
        self._ensure_schema(conn, context)

    def ensure_schema(self, path: str = "", context: str = "default") -> None:
        """Connect and create/migrate the store schema, then close. For a ONE-SHOT
        cloud initialization (scripts/init_cloud.py) run once before multiple
        writers connect, so lazy per-client migration never races on a fresh shared
        DB (P4). ``path`` is ignored on the remote tier (the store is the cloud DB)."""
        conn = _db.connect(path)
        try:
            self._create_store_schema(conn, context)
            conn.commit()
        finally:
            conn.close()

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
                self._create_store_schema(conn, context)
                self._schema_ready = True

            # P5 — embed-model write guard. Vectors from different embedding models
            # live in different spaces, so on a SHARED store they must never mix. If
            # the store already declares a different model, keep writing the
            # model-agnostic nodes/links but SKIP vectors, and don't overwrite the
            # store's model declaration.
            _stored_model_row = conn.execute(
                "SELECT value FROM meta WHERE key='embed_model'").fetchone()
            _stored_model = _stored_model_row[0] if _stored_model_row else None
            write_vectors = (not _stored_model) or (_stored_model == EMBED_MODEL)
            if not write_vectors:
                import sys as _sys
                print(
                    f"neuron: lo store '{path}' usa il modello di embedding "
                    f"'{_stored_model}', quello attivo e' '{EMBED_MODEL}'. Vettori NON "
                    f"scritti (spazi non confrontabili). Allinea NS_EMBED_MODEL per "
                    f"tutti gli scrittori, o rigenera lo store (scripts/reembed.py).",
                    file=_sys.stderr,
                )

            # P2 — wrap the DATA writes below in a single atomic transaction on the
            # remote tier: commit() flushes them as one all-or-nothing batch, so a
            # concurrent load never observes a half-applied save. Schema/DDL above
            # ran outside it (idempotent, once per process); reads inside still see
            # committed state. On the local tier begin() is not called and the plain
            # commit() behaves exactly as before.
            if _db.REMOTE_TURSO:
                conn.begin()

            # Meta split (T11 P1): SHARED graph-level settings vs PER-USER session
            # state. embed_dim (and embed_model, only when compatible) describe the
            # shared vector space and live in the store meta. The turn counter,
            # staged stimulus, last-topic/keywords and session id are one user's
            # working state; on a shared remote store the global meta has no
            # per-user key, so they go to a LOCAL per-user sidecar instead of
            # bleeding across colleagues. On a local single-writer store they stay
            # in meta (unchanged behaviour, keeps legacy files working).
            _shared_meta = [("embed_dim", str(VECTOR_DIM))]
            if write_vectors:
                _shared_meta.append(("embed_model", EMBED_MODEL))
            conn.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", _shared_meta)

            session_meta = [
                ("session_id",            self.session_id),
                ("turn_count",            str(self.turn_count)),
                ("last_sentiment",        self.last_sentiment),
                ("last_topic",            self.last_topic),
                ("last_keywords",         json.dumps(self.last_keywords)),
                ("last_active_timestamp", str(time.time())),           # E3.3
                ("staged_stimulus",       self.staged_stimulus or ""),  # E3.4
                ("staged_ts",             str(self._staged_ts or "")),
            ]
            if _db.REMOTE_TURSO:
                self._save_session_sidecar(path, context, dict(session_meta))
            else:
                conn.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", session_meta)

            # Flush archived nodes (E1.2/E1.3) — recoverable, then clear the buffer.
            # The _graveyard table is created in _create_store_schema, so this is a
            # plain INSERT (no per-save DDL inside the transaction).
            if self._graveyard:
                conn.executemany(
                    "INSERT INTO _graveyard (context, keyword, salience, domain, reason, turn) "
                    "VALUES (?,?,?,?,?,?)",
                    [(context, e["keyword"], int(e.get("salience", 0)), e.get("domain", ""),
                      e.get("reason", ""), int(e.get("turn", 0))) for e in self._graveyard],
                )
                self._graveyard = []

            # P3 — a reconcile (diff-delete) can drop rows another colleague added
            # to a shared context since we loaded. On a shared remote store, skip
            # the destructive delete unless explicitly allowed (coordinated
            # maintenance): fall back to an additive write so the merge's upserts
            # still land and no one else's rows are lost.
            if self._needs_diff_delete:
                _allow_shared = os.environ.get(
                    "NS_ALLOW_SHARED_RECONCILE", "").strip().lower() in ("1", "true", "yes", "on")
                if _db.REMOTE_TURSO and not _allow_shared:
                    import sys as _sys
                    print(
                        "neuron: reconcile/consolidamento su store cloud condiviso "
                        "declassato a scrittura additiva (nessuna cancellazione di "
                        "righe altrui). Per la manutenzione coordinata avvia con "
                        "NS_ALLOW_SHARED_RECONCILE=1.",
                        file=_sys.stderr,
                    )
                    self._save_delta(conn, context, all_rows=True, write_vectors=write_vectors)
                else:
                    self._save_reconcile(conn, context, write_vectors=write_vectors)
            else:
                self._save_delta(conn, context, all_rows=self._needs_full_write,
                                 write_vectors=write_vectors)

            conn.commit()
            self._reset_tracking()
            # New baseline: our in-memory salience is now what we've persisted our
            # delta against. The store may hold a higher value (concurrent adds);
            # that reconciles on the next load. Deltas are always relative to here.
            self._snapshot_salience()
        except Exception:
            # Discard the buffered remote transaction so a failed save applies
            # nothing (next turn re-sends the still-dirty state).
            if _db.REMOTE_TURSO:
                try:
                    conn.rollback()
                except Exception:
                    pass
            raise
        finally:
            conn.close()

    def _save_delta(self, conn, context: str, *, all_rows: bool,
                    write_vectors: bool = True) -> None:
        """Atomic, additive write — no row is ever deleted except the ones this
        graph explicitly removed. Salience is applied as a relative delta and
        link weight is promoted monotonically, so concurrent writers on the same
        node/link don't clobber each other.

        ``all_rows=False`` writes only the tracked dirty delta (per-turn hot
        path). ``all_rows=True`` writes every in-memory row (fresh graph, or a
        context warm-started from the seed) so the store gets rows it's missing —
        still without any diff-delete, so it's safe on a shared store.

        ``write_vectors=False`` skips vector upserts (P5: the store's embedding
        model differs from the active one, so the vectors aren't comparable).
        """
        # Targeted deletes first (a key removed-then-readded in the same cycle was
        # already discarded from the removed set by the mutation helpers).
        if self._removed_nodes:
            conn.executemany("DELETE FROM nodes WHERE context=? AND keyword=?",
                             [(context, k) for k in self._removed_nodes])
            conn.executemany("DELETE FROM node_vectors WHERE context=? AND keyword=?",
                             [(context, k) for k in self._removed_nodes])
            conn.executemany("DELETE FROM episodes WHERE context=? AND keyword=?",
                             [(context, k) for k in self._removed_nodes])
            conn.executemany("DELETE FROM refs WHERE context=? AND keyword=?",
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
                t_delta = nd.trust - self._trust_baseline.get(nd.keyword, 0.0)
                rows.append(self._node_row(nd, context) + (delta, t_delta))
            conn.executemany(_NODE_UPSERT_ATOMIC, rows)
            self._write_refs(conn, node_set, context)

        vec_set = self.nodes if all_rows else [nd for nd in self.nodes
                                               if nd.keyword in self._dirty_vectors]
        if vec_set and write_vectors:
            conn.executemany(_VEC_UPSERT, [self._vec_row(nd, context) for nd in vec_set])

        link_set = self.links if all_rows else [lk for lk in self.links
                                                if self._link_key(lk) in self._dirty_links]
        if link_set:
            conn.executemany(_LINK_UPSERT_ATOMIC, [self._link_row(lk, context) for lk in link_set])

        # Episodes (T56): targeted deletes (cap overflow) + upsert of new facts.
        if self._removed_episodes:
            conn.executemany(
                "DELETE FROM episodes WHERE context=? AND keyword=? AND turn=?",
                [(context, k, t) for (k, t) in self._removed_episodes])
        ep_rows = []
        if all_rows:
            for kw, eps in self.episodes.items():
                ep_rows += [(context, kw, e["turn"], e["text"]) for e in eps]
        else:
            for (kw, t) in self._dirty_episodes:
                for e in self.episodes.get(kw, []):
                    if e["turn"] == t:
                        ep_rows.append((context, kw, t, e["text"]))
        if ep_rows:
            conn.executemany(
                "INSERT INTO episodes(context, keyword, turn, text) VALUES(?,?,?,?) "
                "ON CONFLICT(context, keyword, turn) DO UPDATE SET text=excluded.text",
                ep_rows)

    def _save_reconcile(self, conn, context: str, *, write_vectors: bool = True) -> None:
        """Structural reconcile (merge only): absolute upsert of every in-memory
        row, then delete this context's store rows no longer present in memory
        (delete-by-diff, scoped to this context). This is the one save that may
        delete another writer's rows, so it's reserved for deliberate structural
        edits, never the per-turn path. ``write_vectors=False`` skips vector
        upserts when the store's embedding model differs from the active one (P5)."""
        if self.nodes:
            conn.executemany(_NODE_UPSERT, [self._node_row(nd, context) for nd in self.nodes])
            self._write_refs(conn, self.nodes, context)
            if write_vectors:
                conn.executemany(_VEC_UPSERT, [self._vec_row(nd, context) for nd in self.nodes])
        if self.links:
            conn.executemany(_LINK_UPSERT, [self._link_row(lk, context) for lk in self.links])
        # Episodes (T56): reconcile writes all in-memory facts; stale episode rows
        # of surviving nodes are left alone (harmless), those of deleted nodes are
        # removed below together with the node.
        ep_rows = [(context, kw, e["turn"], e["text"])
                   for kw, eps in self.episodes.items() for e in eps]
        if ep_rows:
            conn.executemany(
                "INSERT INTO episodes(context, keyword, turn, text) VALUES(?,?,?,?) "
                "ON CONFLICT(context, keyword, turn) DO UPDATE SET text=excluded.text",
                ep_rows)

        mem_kw      = {nd.keyword for nd in self.nodes}
        existing_kw = {row[0] for row in
                       conn.execute("SELECT keyword FROM nodes WHERE context=?", (context,))}
        stale_nodes = existing_kw - mem_kw
        if stale_nodes:
            conn.executemany("DELETE FROM nodes WHERE context=? AND keyword=?",
                             [(context, k) for k in stale_nodes])
            conn.executemany("DELETE FROM node_vectors WHERE context=? AND keyword=?",
                             [(context, k) for k in stale_nodes])
            conn.executemany("DELETE FROM episodes WHERE context=? AND keyword=?",
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
            # T64: a PRISTINE store can exist without a schema — pyturso creates
            # an empty 0-byte file on connect (see db._ensure_parent_dir notes),
            # and a fresh shared cloud DB has no tables until the first save.
            # Reading meta then raises "no such table: meta" and crashed the
            # whole load (hence store_turn). Treat it as "empty store": load
            # nothing; the first save creates the schema, exactly like the
            # missing-file path above.
            try:
                meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
            except Exception as e:
                log.debug("no meta table; treating store as empty: %s", e)
                return

            def _as_float(v):
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None

            # Session/working state (T11 P1): on a shared remote store read it
            # from THIS user's local sidecar, not the shared global meta; on a
            # local store it's just the store's meta (single writer). embed_model /
            # embed_dim below always come from the store (shared, must be consistent).
            smeta = self._load_session_sidecar(path, context) if _db.REMOTE_TURSO else meta
            self.session_id    = smeta.get("session_id", "")
            try:
                self.turn_count = int(smeta.get("turn_count", "0") or 0)
            except (TypeError, ValueError):
                self.turn_count = 0
            self.last_sentiment = smeta.get("last_sentiment", "neutral")
            self.last_topic    = smeta.get("last_topic", "")
            try:
                self.last_keywords = json.loads(smeta.get("last_keywords", "[]"))
            except (json.JSONDecodeError, TypeError):
                self.last_keywords = []
            # Sleep-mode / pre-staging state (E3.3/E3.4)
            self._loaded_ts      = _as_float(smeta.get("last_active_timestamp"))
            self.staged_stimulus = smeta.get("staged_stimulus") or None
            self._staged_ts      = _as_float(smeta.get("staged_ts"))

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
            has_trust   = "trust" in cols
            base_sql  = ("SELECT keyword, turn, topic, domain, sentiment, salience, "
                         "entities, tags, refs" + (", trust" if has_trust else "")
                         + " FROM nodes")
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
                if has_trust:
                    nd.trust = float(row[9] or 0.0)
                self.nodes.append(nd)

            # G2 — union del blob legacy con la tabella refs (dedup su chiave
            # naturale, cap 20 come merge_refs). Tabella assente = store legacy.
            try:
                rq = "SELECT keyword, path, project_id, by FROM refs"
                rrows = (conn.execute(rq + " WHERE context=?", (context,))
                         if has_context else conn.execute(rq)).fetchall()
            except Exception:
                rrows = []
            if rrows:
                by_kw: dict = {}
                for kw, rpath, pid, by in rrows:
                    by_kw.setdefault(kw, []).append(
                        {"path": rpath, "project_id": pid, "by": by})
                for nd in self.nodes:
                    extra = by_kw.get(nd.keyword)
                    if not extra:
                        continue
                    seen = {(r.get("path"), r.get("project_id"), r.get("by"))
                            for r in (nd.references or [])}
                    merged = list(nd.references or [])
                    merged += [r for r in extra
                               if (r["path"], r["project_id"], r["by"]) not in seen]
                    nd.references = merged[:20]

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
            except Exception as e:
                log.warning("vector load failed (falling back to per-keyword embed): %s", e)
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
            tctx_sql  = "target_context" if "target_context" in link_cols else "NULL"
            select = ("SELECT source, target, link_type, weight, rationale, "
                      f"created_turn, last_active_turn, inactive_turns, {coact_sql}, "
                      f"{tctx_sql} FROM links")
            if has_context:
                link_rows = conn.execute(select + " WHERE context=? ORDER BY id", (context,))
            else:
                link_rows = conn.execute(select + " ORDER BY id")
            for row in link_rows:
                if domain_filter and row[0] not in node_kws and row[1] not in node_kws:
                    continue
                _last_active = row[6] or 0
                # A1 (Piano 05): the persisted inactive_turns can be stale by
                # design (inactive links are no longer re-upserted every turn).
                # Derive it from the invariant turn_count - last_active_turn,
                # clamped >= stored value for safety (shared stores mix writers
                # with different per-user turn_counts; legacy rows may have
                # last_active_turn=0).
                _derived = max(0, self.turn_count - _last_active) if _last_active > 0 \
                    else (row[7] or 0)
                link = Link(
                    source=row[0], target=row[1], link_type=row[2], weight=row[3],
                    rationale=row[4] or "",
                    created_turn=row[5] or 0,
                    last_active_turn=_last_active,
                    inactive_turns=max(_derived, row[7] or 0) if _last_active > 0 else _derived,
                    co_activation_count=row[8] or 0,
                    target_context=row[9],
                )
                self.links.append(link)

            # Episodes (T56) — tolerate legacy/seed stores without the table.
            self.episodes.clear()
            try:
                if has_context:
                    erows = conn.execute(
                        "SELECT keyword, turn, text FROM episodes WHERE context=? "
                        "ORDER BY keyword, turn", (context,))
                else:
                    erows = conn.execute(
                        "SELECT keyword, turn, text FROM episodes ORDER BY keyword, turn")
                for kw, t, tx in erows:
                    self.episodes.setdefault(kw, []).append({"turn": t, "text": tx})
            except Exception as e:
                log.debug("episode load skipped (table may be absent): %s", e)
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
