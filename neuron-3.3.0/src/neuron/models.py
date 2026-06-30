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
VECTOR_DIM               = 384
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

def pack_vector(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def unpack_vector(data: bytes) -> list[float]:
    return list(struct.unpack(f"{len(data) // 4}f", data))


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
            return

        if node.vector is None:
            node.vector = _get_vector(node.keyword)

        # cap: evict lowest-salience nodes when over MAX_NODES
        if len(self.nodes) >= MAX_NODES:
            self.nodes.sort(key=lambda n: n.salience)
            evict = self.nodes[:max(1, len(self.nodes) - MAX_NODES + 1)]
            evict_kws = {n.keyword for n in evict}
            self.nodes = [n for n in self.nodes if n.keyword not in evict_kws]
            # also drop links that reference evicted nodes
            self.links = [lk for lk in self.links
                          if lk.source not in evict_kws and lk.target not in evict_kws]
            self._rebuild_node_map()

        self.nodes.append(node)
        self._node_map[node.keyword] = node
        self._dirty = True

    # ------------------------------------------------------------------
    # Mutation — link
    # ------------------------------------------------------------------

    def add_link(self, link: Link) -> None:
        # Normalize source/target to match node map keys
        link.source = self._norm(link.source)
        link.target = self._norm(link.target)
        # dedup: skip if an equivalent link already exists in either direction
        for existing in self.links:
            if (existing.source == link.source and existing.target == link.target
                    and existing.link_type == link.link_type):
                # upgrade weight if new one is stronger
                if WEIGHT_ORDER.get(link.weight, 0) > WEIGHT_ORDER.get(existing.weight, 0):
                    existing.weight = link.weight
                    self._dirty = True
                return
            if (existing.source == link.target and existing.target == link.source
                    and existing.link_type == link.link_type):
                if WEIGHT_ORDER.get(link.weight, 0) > WEIGHT_ORDER.get(existing.weight, 0):
                    existing.weight = link.weight
                    self._dirty = True
                return
        self.links.append(link)
        self._dirty = True

    def remove_link(self, link: Link) -> None:
        self.links.remove(link)
        self._dirty = True

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

    # ------------------------------------------------------------------
    # Decay / pruning
    # ------------------------------------------------------------------

    def prune_tangential(self) -> int:
        before = len(self.links)
        self.links = [
            lk for lk in self.links
            if not (lk.weight == "tangential" and lk.inactive_turns > TANGENTIAL_EXPIRY_TURNS)
        ]
        removed = before - len(self.links)
        if removed:
            self.pruned_count += removed
            self._dirty = True
        return removed

    def increment_inactivity(self, active_sources: set[str]) -> None:
        for lk in self.links:
            if lk.source in active_sources or lk.target in active_sources:
                lk.inactive_turns = 0
                lk.last_active_turn = self.turn_count
            else:
                lk.inactive_turns += 1
        for nd in self.nodes:
            if nd.keyword not in active_sources and nd.salience > 0:
                if (self.turn_count - nd.turn) > SALIENCE_DECAY_THRESHOLD:
                    nd.salience = max(0, nd.salience - SALIENCE_DECAY_AMOUNT)
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

    def save_sqlite(self, path: str, *, force: bool = False) -> None:
        """Persist to SQLite. Skips write if graph is not dirty (unless force=True)."""
        if not self._dirty and not force:
            return

        conn = _db.connect(path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS nodes (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    keyword TEXT, turn INTEGER, topic TEXT,
                    domain  TEXT, sentiment TEXT, salience INTEGER,
                    entities TEXT DEFAULT '[]',
                    tags     TEXT DEFAULT '[]',
                    refs     TEXT DEFAULT '[]');
                CREATE UNIQUE INDEX IF NOT EXISTS idx_nodes_keyword ON nodes(keyword);
                CREATE TABLE IF NOT EXISTS node_vectors (
                    keyword   TEXT PRIMARY KEY,
                    embedding BLOB NOT NULL,
                    dim       INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS links (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    source           TEXT, target TEXT, link_type TEXT, weight TEXT,
                    rationale        TEXT, created_turn INTEGER,
                    last_active_turn INTEGER, inactive_turns INTEGER);
                CREATE INDEX IF NOT EXISTS idx_links_source ON links(source);
                CREATE INDEX IF NOT EXISTS idx_links_target ON links(target);
                CREATE INDEX IF NOT EXISTS idx_links_turn   ON links(created_turn);
            """)
            # Migration: add columns if missing
            for col in ("entities", "tags", "refs"):
                try:
                    conn.execute(f"ALTER TABLE nodes ADD COLUMN {col} TEXT DEFAULT '[]'")
                except Exception:
                    pass

            # Meta
            meta_rows = [
                ("session_id",   self.session_id),
                ("turn_count",   str(self.turn_count)),
                ("last_sentiment", self.last_sentiment),
                ("last_topic",   self.last_topic),
                ("last_keywords", json.dumps(self.last_keywords)),
            ]
            conn.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", meta_rows)

            # Nodes — upsert by keyword
            conn.execute("DELETE FROM nodes")
            conn.execute("DELETE FROM node_vectors")
            conn.executemany(
                "INSERT INTO nodes (keyword, turn, topic, domain, sentiment, salience, entities, tags, refs) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    (nd.keyword, nd.turn, nd.topic, nd.domain, nd.sentiment, nd.salience,
                     json.dumps(nd.entities or []), json.dumps(nd.tags or []),
                     json.dumps(nd.references or []))
                    for nd in self.nodes
                ],
            )
            vec_rows = []
            for nd in self.nodes:
                vec = nd.vector if nd.vector is not None else _get_vector(nd.keyword)
                vec_rows.append((nd.keyword, pack_vector(vec), VECTOR_DIM))
            conn.executemany(
                "INSERT OR REPLACE INTO node_vectors (keyword, embedding, dim) VALUES (?,?,?)",
                vec_rows,
            )

            # Links
            conn.execute("DELETE FROM links")
            conn.executemany(
                "INSERT INTO links (source, target, link_type, weight, rationale, "
                "created_turn, last_active_turn, inactive_turns) VALUES (?,?,?,?,?,?,?,?)",
                [
                    (lk.source, lk.target, lk.link_type, lk.weight, lk.rationale,
                     lk.created_turn, lk.last_active_turn, lk.inactive_turns)
                    for lk in self.links
                ],
            )
            conn.commit()
            self._dirty = False
        finally:
            conn.close()

    def load_sqlite(self, path: str, domain_filter: str | None = None) -> None:
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

            cols     = [c[1] for c in conn.execute("PRAGMA table_info(nodes)").fetchall()]
            has_extra = "entities" in cols
            base_sql  = ("SELECT keyword, turn, topic, domain, sentiment, salience, "
                         "entities, tags, refs FROM nodes")
            if domain_filter:
                rows = conn.execute(f"{base_sql} WHERE domain=? ORDER BY id", (domain_filter,)).fetchall()
            else:
                rows = conn.execute(f"{base_sql} ORDER BY id").fetchall()

            self.nodes.clear()
            for row in rows:
                nd = Node(keyword=row[0], turn=row[1], topic=row[2],
                          domain=row[3], sentiment=row[4], salience=row[5])
                if has_extra and row[6]:
                    nd.entities   = json.loads(row[6]) if isinstance(row[6], str) else row[6]
                    nd.tags       = json.loads(row[7]) if isinstance(row[7], str) else row[7]
                    nd.references = json.loads(row[8]) if isinstance(row[8], str) else row[8]
                self.nodes.append(nd)

            # Load vectors
            vec_map: dict[str, list[float]] = {}
            try:
                for row in conn.execute("SELECT keyword, embedding FROM node_vectors"):
                    vec_map[row[0]] = unpack_vector(row[1])
            except Exception:
                pass
            for nd in self.nodes:
                nd.vector = vec_map.get(nd.keyword) or _get_vector(nd.keyword)
            self._rebuild_node_map()

            # Load links
            self.links.clear()
            node_kws = {nd.keyword for nd in self.nodes}
            for row in conn.execute(
                "SELECT source, target, link_type, weight, rationale, "
                "created_turn, last_active_turn, inactive_turns FROM links ORDER BY id"
            ):
                if domain_filter and row[0] not in node_kws and row[1] not in node_kws:
                    continue
                link = Link(
                    source=row[0], target=row[1], link_type=row[2], weight=row[3],
                    rationale=row[4] or "",
                    created_turn=row[5] or 0,
                    last_active_turn=row[6] or 0,
                    inactive_turns=row[7] or 0,
                )
                self.links.append(link)
        finally:
            conn.close()
        self._rebuild_node_map()
        self._dirty = False
