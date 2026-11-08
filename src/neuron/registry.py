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

# The seed DB (base_knowledge.db, Neuron's own manual) is no longer shipped
# (6.9.0). Graphs warm-started from it before 6.6 may still carry its nodes, and
# the load-time cleanup needs to recognise them: these are its 29 nodes as last
# shipped, normalized keyword -> topic. A node goes only if it still matches
# exactly (see Graph.drop_seed_nodes).
_SHIPPED_SEED_TOPICS = {
    'auto': 'auto — fallback POST: estrai + salva in un colpo solo',
    'confirm': 'confirm — feedback sul contesto ricevuto',
    'consolidate': 'consolidate — pulizia del grafo: merge duplicati + archivia orfani',
    'contesti': 'Contesti — organizzazione gerarchica della memoria',
    'dedup': 'dedup — attiva/disattiva deduplicazione automatica',
    'dismiss': 'dismiss — feedback negativo sul contesto ricevuto',
    'export': 'export — esporta il grafo come JSON',
    'extract': 'extract — estrazione semantica da testo (euristico, 0 token)',
    'find_candidates': 'find_candidates — screening duplicati prima di salvare',
    'flash': 'flash — attiva/disattiva i flashback semantici',
    'forgotten': 'forgotten — trova concetti in decadimento',
    'get_context': 'get_context — recupero esplicito di nodi e link collegati',
    'help': 'help — indice rapido dei comandi Neuron',
    'introspect': 'introspect — il modello che Neuron ha di sé',
    'list_contexts': 'list_contexts — elenca tutti i contesti esistenti',
    'merge': 'merge — fusione manuale di nodi duplicati',
    'neuron': 'Neuron — semantic memory MCP server',
    'pre_turn': 'pre_turn — LOOP STEP 1: carica contesto prima di rispondere',
    'prune': 'prune — potatura link tangenziali inattivi',
    'recall': 'recall — riporta in vita un nodo archiviato',
    'reset': 'reset — azzera il grafo',
    'salienza': 'Salienza — peso dinamico dei nodi',
    'skill': 'skill — carica il playbook completo di Neuron',
    'spreading_activation': "Spreading activation — propagazione dell'attivazione nel grafo",
    'status': 'status — stato corrente del grafo',
    'store_turn': 'store_turn — LOOP STEP 2: persiste ciò che è nuovo',
    'summary': 'summary — riassunto testuale del grafo',
    'switch_context': 'switch_context — cambia il contesto attivo',
    'vector_search': 'vector_search — ricerca semantica tra keyword',
}


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
                try:                      # daily rolling backup, before we touch it
                    _db.snapshot(db)
                except Exception as e:    # noqa: BLE001 — a backup must never block a load
                    log.warning("backup of %s skipped: %s", db, e)
                g.load_sqlite(db, context=ctx)
            # An empty context stays empty. It used to be warm-started from the
            # seed — Neuron's own manual — and those 29 nodes then answered
            # every search in place of the user's concepts. Graphs that got them
            # back then are cleaned here, once, and saved at once: the vector
            # SQL tier reads the file, not this object.
            if g.nodes:
                try:
                    gone = g.drop_seed_nodes(_SHIPPED_SEED_TOPICS)
                    if gone:
                        g.save_sqlite(db, context=ctx)
                        log.info("context %r: removed %d seed nodes: %s",
                                 ctx, len(gone), ", ".join(gone))
                except Exception as e:   # noqa: BLE001 — cleanup must never block a load
                    log.warning("seed cleanup skipped for context %r: %s", ctx, e)
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

    def upgrade_all(self) -> list[str]:
        """Bring every graph from an older version to the current schema NOW,
        not on the day someone reopens that context: an unmigrated file has no
        facts table, and cross-context recall skipped it silently. Loading a
        context takes its daily backup and migrates it (episodes -> facts);
        saving writes it. Only files that need it; idempotent; returns them."""
        if _db.REMOTE_TURSO:
            return []                    # the shared store migrates on its own load
        done = []
        for ctx in self._contexts_on_disk():
            if ctx in self._graphs or not self._needs_upgrade(self._db_path(ctx)):
                continue
            try:
                # force: the schema needs the write even when no row changed
                self.get(ctx).save_sqlite(self._db_path(ctx), context=ctx, force=True)
                done.append(ctx)
            except Exception as e:      # noqa: BLE001 — one bad file never blocks the rest
                log.warning("upgrade of context %s skipped: %s", ctx, e)
        return done

    @staticmethod
    def _needs_upgrade(path: str) -> bool:
        """True when the file predates ADR-015 D8 (no facts.confirmed). Read
        with the store's own driver: stdlib sqlite3 does not see pyturso's WAL."""
        try:
            con = _db.connect_local(path)
            try:
                cols = {r[1] for r in con.execute("PRAGMA table_info(facts)")}
            finally:
                con.close()
        except Exception:               # noqa: BLE001 — unreadable here: the normal load deals with it
            return False
        return "confirmed" not in cols

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
            })
        return result

    def save_all(self) -> None:
        """Persist every dirty graph to disk.

        This used to skip contexts warm-started from the seed, under the heading
        "never writes to seed" — but it writes to `self._db_path(ctx)`, the
        context's own file, and never to the seed file. The guard protected
        nothing and excluded exactly the contexts most likely to be lost: `get()`
        then warm-started EVERY newly created context from the seed.

        Both durability nets run through here — the worker's periodic checkpoint
        and its shutdown handler (`gray_matter/_worker.py`, the one written so a
        dirty kill on Windows still keeps data). So a fresh context was invisible
        to both, and survived only if an explicit per-turn `save(ctx)` happened
        to fire. Observed live: `_active_context.txt` naming a context with no
        graph file, and three turns stored under it that reached no file at all.

        Dirtiness is the right gate and `save_sqlite` already applies it, so a
        context never modified still writes nothing — which is what the old
        guard was reaching for.
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
        # Scritto QUI, non solo al checkpoint. `store_turn` fa `save(ctx)` a
        # ogni turno, ma `save(ctx)` non tocca il trail: fino a `save_all()` la
        # riga viveva solo in memoria. Uno switch seguito da una morte sporca
        # del processo lasciava quindi i turni sul disco e il collegamento fra
        # le due meta' della sessione perso -- che e' esattamente il guasto per
        # cui il trail e' stato scritto, un giro piu' in la'.
        #
        # Costa poco: la dedup sopra fa uscire in anticipo i passaggi ripetuti,
        # quindi si scrive solo quando nasce davvero una riga nuova, cioe' a un
        # cambio di contesto mai visto prima. Best-effort come il chiamante: un
        # trail non salvato non puo' far fallire lo switch che lo ha generato.
        try:
            self._save_cross_links()
        except OSError as e:
            log.debug("could not persist the cross-context trail: %s", e)

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

    def _delete_context_rows(self, contexts: list[str] | None) -> None:
        """Delete a context's rows from the STORE, whatever the tier.
        ``contexts=None`` = every context in the store (full reset).

        On Turso Cloud the graph lives in the shared tables (connect ignores
        the path): before 2026-08-25 reset only unlinked stale local files and
        cleared memory — and the reload restarted IDENTICAL from the cloud,
        answering "Graph reset." for a wipe that never happened. NB: if several
        machines share the SAME DB via env, the full reset is shared too — that
        is the tool's contract."""
        conn = _db.connect(self._db_path(contexts[0] if contexts else "default"))
        try:
            marks = ",".join("?" * len(contexts)) if contexts else None
            for table in ("episodes", "refs", "_graveyard", "node_vectors",
                          "links", "nodes"):
                sql = (f"DELETE FROM {table} WHERE context IN ({marks})"
                       if contexts else f"DELETE FROM {table}")
                try:
                    conn.execute(sql, tuple(contexts) if contexts else ())
                except Exception as e:  # noqa: BLE001 — tabella assente: prosegui
                    log.debug("reset: skip %s: %s", table, e)
            conn.commit()
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def reset(self, context: str | None = None) -> None:
        if context:
            ctx = context.lower().strip("/")
            if _db.REMOTE_TURSO:
                self._delete_context_rows([ctx])
            db = self._db_path(ctx)
            if os.path.exists(db):
                self._unlink_db(db)
            self._graphs.pop(ctx, None)
        else:
            if _db.REMOTE_TURSO:
                # Full reset: on the cloud tier the store IS the user's memory,
                # sweep every context present, not just the loaded ones.
                self._delete_context_rows(None)
            for ctx in list(self._graphs):
                db = self._db_path(ctx)
                if os.path.exists(db):
                    self._unlink_db(db)
            self._graphs.clear()
            self._cross_links.clear()
            self._active = "default"
            self.get("default")
