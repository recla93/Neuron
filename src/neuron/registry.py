"""Multi-context graph registry.

Manages separate graph instances per topic context (e.g. java/spring, python/django).
Contexts form a tree — child contexts inherit from parents.
Cross-context links connect nodes across different graphs.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from neuron import db as _db
from neuron.models import Graph

log = logging.getLogger("neuron.registry")


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
        self._active_file = os.path.join(graphs_dir, "_active_context.txt")
        # Restore last active context from disk
        try:
            if os.path.isfile(self._active_file):
                saved = open(self._active_file, encoding="utf-8").read().strip()
                # The pointer is a NAME, not a file handle. This used to restore
                # it only `if os.path.isfile(self._db_path(saved))`, which is
                # inconsistent with how contexts work everywhere else: `get()`
                # materialises them lazily, and an empty context legitimately
                # has no file yet — `save_sqlite` writes nothing when there is
                # nothing to write. So switching to a fresh context and
                # restarting before storing anything silently dropped you back
                # into "default", while the pointer kept naming the context you
                # had chosen. Seen on a live install: the file said "ai", no
                # `graph_ai.db` existed, and the running server said "default"
                # — permanently, with nothing reporting the disagreement.
                if saved:
                    self._active = saved
                    if not os.path.isfile(self._db_path(saved)):
                        log.debug("active context %r has no graph file yet "
                                  "(empty context, materialised on demand)", saved)
        except Exception:
            pass
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
            if not os.path.isfile(p) or os.path.getsize(p) < _db.SQLITE_MIN_VALID_SIZE:
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
            # On remote Turso the graph lives in the shared cloud tables (scoped
            # by the context column), not in a local file — so load regardless of
            # local file existence. Locally, gate on the file as before.
            if _db.REMOTE_TURSO or (os.path.exists(db) and os.path.getsize(db) > 0):
                g.load_sqlite(db, context=ctx)
            if len(g.nodes) == 0 and self._seed_is_loadable():
                # A missing, empty, placeholder, or corrupt seed must not crash
                # the server — degrade to an empty graph. The seed is only a
                # warm-start convenience; a fresh checkout ships a placeholder
                # until base_knowledge.db is regenerated (scripts/import_vault.py).
                try:
                    if ctx == "default":
                        g.load_sqlite(self._seed_path, context="default", warm_start=True)
                        self._seed_loaded.add("default")
                    else:
                        g.load_sqlite(self._seed_path, domain_filter=ctx, context=ctx, warm_start=True)
                        self._seed_loaded.add(ctx)
                except Exception as e:
                    log.warning("seed load failed for context %r (degrading to empty): %s", ctx, e)
            # E3.3/E3.4: if this graph was idle long enough, run sleep-mode on load
            # (pre-stage the top stimulus; consolidate only when NS_CONSOLIDATE_AUTO
            # is on). Never let it break graph loading.
            try:
                _sleep_consolidate = os.environ.get(
                    "NS_CONSOLIDATE_AUTO", "").strip().lower() in ("1", "true", "yes", "on")
                g.sleep_maybe(do_consolidate=_sleep_consolidate)
            except Exception as e:
                log.debug("sleep_maybe on load failed for context %r: %s", ctx, e)
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
        # Persist active context to disk
        try:
            with open(self._active_file, "w", encoding="utf-8") as f:
                f.write(ctx)
        except Exception:
            pass
        return self._active

    def _contexts_on_disk(self) -> list[str]:
        """Context names that have a graph file, whether or not it is loaded."""
        out = []
        try:
            for p in os.listdir(self._graphs_dir):
                if p.startswith("graph_") and p.endswith(".db"):
                    out.append(p[len("graph_"):-len(".db")].replace("__", "/"))
        except OSError:
            pass
        return out

    def list_contexts(self, parent: str | None = None) -> list[dict[str, Any]]:
        # This listed `self._graphs` — the contexts already touched IN THIS
        # PROCESS — while calling itself "all available contexts". On a freshly
        # started server it answered with an empty list; on a live install it
        # reported one context while four had files on disk (arredamento,
        # veicoli and frontend were invisible, so nobody could switch back to
        # something they had built). Disk is the source of truth for what
        # exists; memory only decides what is loaded right now.
        #
        # ponytail: `get()` loads each one to count its nodes, which is a read
        # of every graph file on an explicit user action, then cached. Swap in a
        # cheap SELECT COUNT if someone ends up with enough contexts to notice.
        for ctx in self._contexts_on_disk():
            self.get(ctx)
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
        """Persist every dirty graph to disk.

        This used to skip `ctx in self._seed_loaded`, under the heading "never
        writes to seed" — but it writes to `self._db_path(ctx)`, the context's
        own file, and never to `self._seed_path`. The guard protected nothing
        and excluded exactly the contexts most likely to be lost: `get()` marks
        EVERY newly created context as seed-loaded, because it warm-starts them
        from the seed.

        Both durability nets run through here — the worker's periodic checkpoint
        and its shutdown handler (`gray_matter/_worker.py`, the one written so a
        dirty kill on Windows still keeps data). So a fresh context was invisible
        to both, and survived only if an explicit per-turn `save(ctx)` happened
        to fire. Observed live: `_active_context.txt` naming a context with no
        graph file, and three turns stored under it that reached no file at all.

        Dirtiness is the right gate and `save_sqlite` already applies it, so a
        context warm-started from the seed and never modified still writes
        nothing — which is what the old guard was reaching for.
        """
        for ctx, g in self._graphs.items():
            g.save_sqlite(self._db_path(ctx), context=ctx)
        self._save_cross_links()

    def save(self, context: str | None = None) -> None:
        """Persist a single context graph (never writes to seed)."""
        ctx = context or self._active
        g   = self.get(ctx)
        db  = self._db_path(ctx)
        g.save_sqlite(db, context=ctx)
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
        """Record that a concept in one context led to a concept in another.

        Deduplicated on the two (context, keyword) endpoints: `_cross_links` is
        a flat list loaded whole at startup, and the auto-switch appends to it
        every time the subject moves. The same passage recurs across sessions —
        without this, months of use grow an unbounded list of identical rows,
        all of them re-read on every start.

        ponytail: the first entry wins and the repeat is dropped. Recording that
        a passage happened AGAIN would be the Hebbian thing to do (this codebase
        promotes link weight monotonically elsewhere), but nothing reads the
        count yet — add it when something does.
        """
        key = (source_context, source_keyword, target_context, target_keyword)
        for cl in self._cross_links:
            if (cl.source_context, cl.source_keyword,
                    cl.target_context, cl.target_keyword) == key:
                return
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

    @staticmethod
    def _unlink_db(db: str) -> None:
        """Remove a SQLite DB and its WAL sidecars (Windows-safe).

        Order matters on Windows: ``-shm`` and ``-wal`` may hold memory-mapped
        locks on the parent ``.db`` — remove them *before* the main file.

        On Windows, concurrent processes or WAL sidecars can briefly hold a
        sharing lock even after Python's ``close()``.  We retry a few times
        with a short backoff before giving up."""
        import time as _time
        for suffix in ("-shm", "-wal", ""):
            path = db + suffix
            for attempt in range(4):
                try:
                    if os.path.exists(path):
                        os.remove(path)
                    break
                except PermissionError:
                    if attempt < 3:
                        _time.sleep(0.05 * (attempt + 1))
                    # else: give up — caller can still proceed
                except FileNotFoundError:
                    break

    def reset(self, context: str | None = None) -> None:
        if context:
            ctx = context.lower().strip("/")
            db  = self._db_path(ctx)
            if os.path.exists(db):
                self._unlink_db(db)
            self._graphs.pop(ctx, None)
            self._seed_loaded.discard(ctx)
        else:
            for ctx in list(self._graphs):
                db = self._db_path(ctx)
                if os.path.exists(db):
                    self._unlink_db(db)
            self._graphs.clear()
            self._cross_links.clear()
            self._active = "default"
            self._seed_loaded.clear()
            self.get("default")
