"""Multi-context graph registry.

Manages separate graphs per topic context (e.g. java/spring, python/django).
Contexts form a tree — child contexts inherit from parents.
Cross-context links connect nodes across different graphs.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from neuron.models import Graph


@dataclass
class CrossLink:
    source_context: str
    source_keyword: str
    target_context: str
    target_keyword: str
    link_type: str
    weight: str
    rationale: str


class GraphRegistry:
    """Manages multiple Graph instances keyed by context path.

    Context paths use slash notation: 'java', 'java/spring', 'python/django'.
    A context inherits from its parent chain up to 'default'.
    """

    def __init__(self, graphs_dir: str):
        self._graphs_dir = graphs_dir
        os.makedirs(graphs_dir, exist_ok=True)
        self._graphs: dict[str, Graph] = {}
        self._cross_links: list[CrossLink] = []
        self._active: str = "default"
        self._cross_db = os.path.join(graphs_dir, "_cross_links.json")
        self._load_cross_links()
        # seed path: prefer the DB bundled inside the installed package
        # (src/neuron/data/base_knowledge.db, shipped in the wheel). Fall back
        # to the legacy repo-relative location (knowledge/base_knowledge.db,
        # one level above graphs/) for source checkouts / dev runs.
        self._seed_path = self._resolve_seed_path(graphs_dir)
        # track which contexts were loaded from seed (immutable source)
        self._seed_loaded: set[str] = set()

    @staticmethod
    def _resolve_seed_path(graphs_dir: str) -> str:
        """Locate the seed knowledge DB, packaged location first.

        1. ``neuron/data/base_knowledge.db`` via ``importlib.resources`` — this
           is what ships in the installed wheel.
        2. ``<repo>/knowledge/base_knowledge.db`` (legacy, repo-relative) — used
           when running from a source checkout where the package data isn't
           populated. Returned even if absent so existing "missing seed"
           handling downstream is unchanged.
        """
        try:
            from importlib.resources import files
            packaged = files("neuron").joinpath("data", "base_knowledge.db")
            if packaged.is_file():
                return str(packaged)
        except (ImportError, ModuleNotFoundError, FileNotFoundError, AttributeError):
            pass
        parent = os.path.dirname(os.path.normpath(graphs_dir))
        return os.path.join(parent, "knowledge", "base_knowledge.db")

    def _seed_is_loadable(self) -> bool:
        """True only if the seed path looks like a real SQLite/Turso database.

        Guards against a missing file or the shipped placeholder (a tiny text
        stub used before the real base_knowledge.db is generated). A valid
        SQLite file is >= 512 bytes and starts with the "SQLite format 3\\000"
        magic header.
        """
        p = self._seed_path
        try:
            if not os.path.isfile(p) or os.path.getsize(p) < 512:
                return False
            with open(p, "rb") as f:
                return f.read(16) == b"SQLite format 3\x00"
        except OSError:
            return False

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _safe_name(self, context: str) -> str:
        return context.replace("/", "__") if context != "default" else "default"

    def _db_path(self, context: str) -> str:
        return os.path.join(self._graphs_dir, f"graph_{self._safe_name(context)}.db")

    # ------------------------------------------------------------------
    # Graph access
    # ------------------------------------------------------------------

    def get(self, context: str | None = None) -> Graph:
        """Get (or create) the graph for a context."""
        ctx = (context or self._active).lower().strip("/")
        if not ctx:
            ctx = "default"
        if ctx not in self._graphs:
            g = Graph()
            db = self._db_path(ctx)
            if os.path.exists(db) and os.path.getsize(db) > 0:
                g.load_sqlite(db)
            if len(g.nodes) == 0 and self._seed_is_loadable():
                # A missing, empty, placeholder, or corrupt seed must not crash
                # the server — degrade to an empty graph. The seed is only a
                # warm-start convenience; a fresh checkout ships a placeholder
                # until base_knowledge.db is regenerated (scripts/import_vault.py).
                try:
                    if ctx == "default":
                        g.load_sqlite(self._seed_path)
                        self._seed_loaded.add("default")
                    else:
                        g.load_sqlite(self._seed_path, domain_filter=ctx)
                        self._seed_loaded.add(ctx)
                except Exception:
                    pass
            self._graphs[ctx] = g
        return self._graphs[ctx]

    def resolve_chain(self, context: str | None = None) -> list[Graph]:
        """Return [current, parent, grandparent, ..., default] for inheritance lookup."""
        ctx = (context or self._active).lower().strip("/")
        parts = ctx.split("/")
        chain: list[Graph] = []
        seen: set[str] = set()
        for i in range(len(parts), 0, -1):
            parent = "/".join(parts[:i])
            if parent not in seen:
                chain.append(self.get(parent))
                seen.add(parent)
        if "default" not in seen:
            chain.append(self.get("default"))
        return chain

    # ------------------------------------------------------------------
    # Context lifecycle
    # ------------------------------------------------------------------

    @property
    def active(self) -> str:
        return self._active

    def switch(self, context: str) -> str:
        """Switch active context, creating it if needed.
        Deduplicates: if a context with the same normalized name exists, reuse it."""
        ctx = context.lower().strip("/")
        if not ctx:
            ctx = "default"
        normalized = ctx.replace("-", "").replace("_", "").replace(" ", "")
        for existing in list(self._graphs.keys()):
            en = existing.lower().replace("-", "").replace("_", "").replace(" ", "")
            if normalized == en and existing != ctx:
                ctx = existing
                break
        self.get(ctx)
        self._active = ctx
        return self._active

    def list_contexts(self, parent: str | None = None) -> list[dict[str, Any]]:
        result = []
        prefix = (parent or "").lower().strip("/")
        for ctx in sorted(self._graphs):
            if prefix and not ctx.startswith(prefix):
                continue
            g = self._graphs[ctx]
            result.append({
                "context": ctx,
                "nodes":   len(g.nodes),
                "links":   len(g.links),
                "turns":   g.turn_count,
                "active":  ctx == self._active,
                "seed":    ctx in self._seed_loaded,
            })
        return result

    def save_all(self) -> None:
        """Persist all dirty graphs to disk (never writes to seed)."""
        for ctx, g in self._graphs.items():
            if ctx not in self._seed_loaded:
                g.save_sqlite(self._db_path(ctx))
        self._save_cross_links()

    def save(self, context: str | None = None) -> None:
        """Persist a single context graph (never writes to seed)."""
        ctx = context or self._active
        g   = self.get(ctx)
        db  = self._db_path(ctx)
        g.save_sqlite(db)
        self._seed_loaded.discard(ctx)

    def context_tree(self) -> dict[str, Any]:
        root: dict[str, Any] = {"name": "default", "children": []}
        for ctx in sorted(self._graphs):
            if ctx == "default":
                continue
            parts = ctx.split("/")
            node  = root
            for i, part in enumerate(parts):
                path     = "/".join(parts[:i + 1])
                children = node.setdefault("children", [])
                existing = next((c for c in children if c["name"] == part), None)
                if not existing:
                    g = self._graphs.get(path)
                    existing = {
                        "name":     part,
                        "path":     path,
                        "nodes":    len(g.nodes) if g else 0,
                        "links":    len(g.links) if g else 0,
                        "children": [],
                    }
                    children.append(existing)
                node = existing
        return root

    # ------------------------------------------------------------------
    # Cross-context links
    # ------------------------------------------------------------------

    def add_cross_link(
        self,
        source_context: str, source_keyword: str,
        target_context: str, target_keyword: str,
        link_type: str = "analogy", weight: str = "medium", rationale: str = "",
    ) -> None:
        self._cross_links.append(CrossLink(
            source_context=source_context, source_keyword=source_keyword,
            target_context=target_context, target_keyword=target_keyword,
            link_type=link_type, weight=weight, rationale=rationale,
        ))

    def get_cross_links(self, context: str) -> list[CrossLink]:
        ctx    = context.lower().strip("/")
        result = []
        for cl in self._cross_links:
            if cl.source_context == ctx or cl.target_context == ctx:
                result.append(cl)
            elif ctx.startswith(cl.source_context) or ctx.startswith(cl.target_context):
                result.append(cl)
        return result

    def _load_cross_links(self) -> None:
        if os.path.exists(self._cross_db):
            try:
                with open(self._cross_db, encoding="utf-8") as f:
                    for item in json.load(f):
                        self._cross_links.append(CrossLink(**item))
            except (json.JSONDecodeError, KeyError, TypeError):
                self._cross_links = []

    def _save_cross_links(self) -> None:
        with open(self._cross_db, "w", encoding="utf-8") as f:
            json.dump([cl.__dict__ for cl in self._cross_links], f, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def reset(self, context: str | None = None) -> None:
        if context:
            ctx = context.lower().strip("/")
            db  = self._db_path(ctx)
            if os.path.exists(db):
                os.remove(db)
            self._graphs.pop(ctx, None)
            self._seed_loaded.discard(ctx)
        else:
            for ctx in list(self._graphs):
                db = self._db_path(ctx)
                if os.path.exists(db):
                    os.remove(db)
            self._graphs.clear()
            self._cross_links.clear()
            self._active = "default"
            self._seed_loaded.clear()
            self.get("default")
