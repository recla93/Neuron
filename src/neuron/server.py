"""Neuron v3.3 — MCP Server with Turso (local pyturso engine or cloud) + native vector search.

Database: see neuron.db — local pyturso engine by default, or real Turso cloud
(libsql-client) when TURSO_DATABASE_URL/TURSO_AUTH_TOKEN are set.
Embedding: 384-dim semantic (fastembed, mandatory).
Search: Turso SQL (vector_distance_cos) or Python fallback.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import weakref
from typing import Any

# Logs go to stderr (never stdout — that carries the MCP JSON-RPC stream).
log = logging.getLogger("neuron.server")

__all__ = [
    "cli", "main", "call_tool", "list_tools", "list_resources", "read_resource",
    "validate_turn_input", "GRAPHS_DIR", "INTENT_SALIENCE", "RANK_WEIGHTS",
    "CONSOLIDATE_EVERY", "TOPIC_SHIFT_THRESHOLD", "STIMULUS_MIN_ACTIVATION",
    "STIMULUS_MAX_CHARS", "NS_EMBED_MODEL",
]

import sqlite3

from fastembed import TextEmbedding

from neuron import __version__, db as _db
from neuron import curation as _cur   # T54 gate (stdlib-only module)
# T57: extraction moved verbatim to its own module; every public name is
# re-imported here so existing imports/tests via neuron.server keep working.
from neuron.extraction import (
    DOMAIN_ALIASES, DOMAIN_KEYWORDS, ENTITY_EXCLUDE, ExtractionResult,
    INTENT_PATTERNS, KEYWORD_MAX_LENGTH, KEYWORD_PATTERN, STOP_WORDS,
    SENTIMENT_NEGATIVE, SENTIMENT_POSITIVE, SENTIMENT_URGENT,
    SemanticExtractor, TOPIC_MAX_LENGTH, _auto_extract, _fold_accents,
)
TURSO_ENGINE = _db.LOCAL_TURSO_ENGINE

from mcp.server import Server
from mcp.server.lowlevel import NotificationOptions
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, ServerCapabilities, ToolsCapability, Resource

# ---------------------------------------------------------------------------
# Imports from models (breaks circular import with registry.py)
# ---------------------------------------------------------------------------

from neuron.models import (
    Node, Link, Graph, compute_health,
    Weight, LinkType, Domain, Sentiment, Intent,
    WEIGHT_ORDER, TANGENTIAL_EXPIRY_TURNS,
    SALIENCE_DECAY_THRESHOLD, SALIENCE_DECAY_AMOUNT,
    VECTOR_DIM, pack_vector, unpack_vector, register_embed_fn,
)

# ---------------------------------------------------------------------------
# Server-level constants
# ---------------------------------------------------------------------------

INTENT_SALIENCE = {"exploration": 3, "task": 3, "clarification": 2, "question": 1, "feedback": 0}

# Path/slug resolution lives in neuron.config (single source of truth, P0 #3).
# Thin module-level aliases keep the historical names importable/monkeypatchable.
from neuron.config import resolve_slug as _resolve_slug
from neuron.config import default_graphs_dir as _default_graphs_dir
from neuron import config as _config


GRAPHS_DIR = _config.graphs_dir()

_g: "GraphRegistry" = None  # initialized after GraphRegistry import

# KEYWORD_MAX_LENGTH / TOPIC_MAX_LENGTH / KEYWORD_PATTERN live in
# neuron.extraction (T57) and are re-imported above.
RATIONALE_MAX_LENGTH = 200



def validate_turn_input(keywords: list[str], topic: str, links: list[dict],
                        entities: list[str] | None = None,
                        tags: list[str] | None = None,
                        references: list[dict] | None = None) -> str | None:
    if not keywords or len(keywords) > 8:
        return "keywords: da 1 a 8"
    if not topic or len(topic) > TOPIC_MAX_LENGTH:
        return f"topic: max {TOPIC_MAX_LENGTH} caratteri"
    for i, kw in enumerate(keywords):
        if not kw or len(kw) > KEYWORD_MAX_LENGTH:
            return f"keywords[{i}]: max {KEYWORD_MAX_LENGTH} caratteri"
        if not KEYWORD_PATTERN.match(kw):
            return f"keywords[{i}]: caratteri non consentiti (usa lettere, numeri, spazi, -_.:+)"
    if entities and len(entities) > 15:
        return "entities: max 15"
    if tags and len(tags) > 10:
        return "tags: max 10"
    if references and len(references) > 20:
        return "references: max 20"
    for j, ld in enumerate(links):
        src, tgt = ld.get("source", ""), ld.get("target", "")
        if not src or len(src) > KEYWORD_MAX_LENGTH or not KEYWORD_PATTERN.match(src):
            return f"links[{j}].source: non valida"
        if not tgt or len(tgt) > KEYWORD_MAX_LENGTH or not KEYWORD_PATTERN.match(tgt):
            return f"links[{j}].target: non valida"
        rat = ld.get("rationale", "")
        if len(rat) > RATIONALE_MAX_LENGTH:
            return f"links[{j}].rationale: max {RATIONALE_MAX_LENGTH} caratteri"
    return None


# ---------------------------------------------------------------------------
# Automatic semantic extraction — MOVED to neuron.extraction (T57, ADR-006).
# All public names (STOP_WORDS, SemanticExtractor, ExtractionResult, ...) are
# re-imported at the top of this file, so `from neuron.server import X` and
# `_srv.X` in the test-suite keep working unchanged.
# ---------------------------------------------------------------------------






# ---------------------------------------------------------------------------
# Topic shift detection and auto-linking
# ---------------------------------------------------------------------------

TOPIC_SHIFT_THRESHOLD = _config.env_float("NEURON_TOPIC_SHIFT_THRESHOLD", 0.3)

# The stimulus-engine functions live in neuron.stimulus (T57): topic shift,
# auto-linking, context window with semantic flashes, piggyback stimulus.
# Re-exported here so `from neuron.server import X` and `_srv.X` monkeypatches
# keep working; config/state (thresholds, flash_enabled, _g) stays on this
# module and is resolved by neuron.stimulus at call time.
from neuron.stimulus import (  # noqa: E402
    _auto_link, _build_context_window, _detect_topic_shift, _keyword_overlap,
    _stimulus_block,
)


# (_build_context_window and _stimulus_block moved to neuron.stimulus — T57;
#  re-imported above with the rest of the stimulus engine.)


# ---------------------------------------------------------------------------
# Vector embedding — lazy-loaded fastembed.
# ---------------------------------------------------------------------------
# Model is configurable via NS_EMBED_MODEL. Default: the 384-dim multilingual
# paraphrase-multilingual-MiniLM-L12-v2 (ADR-001) — covers EN+IT in one space
# (bench: IT recall 0.89→1.00 vs the English all-MiniLM-L6-v2, same 384-dim).
# For an English-only workload the lighter all-MiniLM-L6-v2 is still selectable:
# NS_EMBED_MODEL=sentence-transformers/all-MiniLM-L6-v2. Vectors from different
# models are NOT comparable, so changing the model requires a full re-embed
# (scripts/reembed.py); the dimension is validated on first use against VECTOR_DIM.

NS_EMBED_MODEL = os.environ.get(
    "NS_EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
).strip()

_embedder: TextEmbedding | None = None
_embed_dim_checked = False

# Per-process embedding cache. Within a single turn the SAME keywords get
# embedded again and again — _refine_domain, _auto_link (one query per keyword),
# the cross-domain loop, and _build_context_window all re-embed the current
# keywords. Caching collapses those to one model call per distinct text. The key
# includes the embedder identity so swapping the model (tests, re-embed) misses
# stale vectors. Bounded with cheap FIFO-ish eviction to keep memory flat.
_EMBED_CACHE_MAX = 4096
_embed_cache: "dict[tuple[int, str], list[float]]" = {}


# ---------------------------------------------------------------------------
# Hybrid vector search — LOGIC moved to neuron.search (T57, ADR-006).
# The STATE stays here (the suite and runtime toggles patch it on this module:
# _embedder, _embed_cache, _seed_conn_cache, _turn_search_cache, TURSO_ENGINE);
# the moved functions resolve it through this namespace at call time.
# ---------------------------------------------------------------------------

# Read-only connection cache for the immutable seed DB. base_knowledge.db is
# never written at runtime, so reopening it on every search call is pure waste.
# The ACTIVE graph DB is written on save, so it is deliberately NOT cached:
# open-per-call avoids stale reads after a save within the same turn.
_seed_conn_cache: "dict[str, Any]" = {}


# A2 (Piano 05): memo of _search_embeddings results, valid for the duration of
# a single tool call (cleared by the call_tool wrapper). Within one store_turn
# the chain auto_link → _build_context_window re-runs the same searches; within
# one pre_turn _resolve_context does too. The graph can't change mid-call in a
# way that invalidates results (mutations happen before the searches or after).
# Each entry stores a weakref to the graph it was computed for: id() alone is
# NOT a stable identity (CPython reuses ids after GC — two short-lived Graph()
# objects can collide, found by test_seed_connection_reused_active_not_cached),
# so a hit counts only if the cached ref still points at the SAME live object.
_turn_search_cache: dict[tuple, tuple["weakref.ref", list[tuple[str, float]]]] = {}

# The moved search/embedding functions, re-exported so `from neuron.server
# import X` and every `_srv.X` monkeypatch keep working unchanged (ADR-006).
from neuron.search import (  # noqa: E402
    _drop_seed_connection, _embed_one, _get_embedder, _get_embedding,
    _normalize_domain, _refine_domain, _search_embeddings, _seed_connection,
    _seed_usable,
)


from neuron.registry import GraphRegistry

# ---------------------------------------------------------------------------
# Global instance
# ---------------------------------------------------------------------------

_g = GraphRegistry(GRAPHS_DIR)
register_embed_fn(_get_embedding)  # allow models.py to call embedder


def _load_domain_signal() -> None:
    """Restore hysteresis counter from the active graph's meta table (survives restart)."""
    try:
        import sqlite3 as _sq
        db = _active_db_path()
        if not os.path.exists(db):
            return
        conn = _sq.connect(db)
        domain = conn.execute("SELECT value FROM meta WHERE key='signal_domain'").fetchone()
        count  = conn.execute("SELECT value FROM meta WHERE key='signal_count'").fetchone()
        conn.close()
        if domain and count:
            _domain_signal["domain"] = domain[0] or None
            _domain_signal["count"]  = int(count[0])
    except Exception as e:
        log.debug("could not load domain signal: %s", e)


def _save_domain_signal() -> None:
    """Persist hysteresis counter to the active graph's meta table."""
    try:
        import sqlite3 as _sq
        db = _active_db_path()
        if not os.path.exists(db):
            return
        conn = _sq.connect(db)
        conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", ("signal_domain", _domain_signal.get("domain") or ""))
        conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", ("signal_count",  str(_domain_signal.get("count", 0))))
        conn.commit()
        conn.close()
    except Exception as e:
        log.debug("could not persist domain signal: %s", e)

def _bootstrap_domain_keywords() -> None:
    """Populate DOMAIN_KEYWORDS with clean keywords from seed data.

    Filters applied (each must pass):
    - domain is a known domain
    - length 4–20 chars
    - max 2 words (no long phrases)
    - matches KEYWORD_PATTERN (no parens, braces, etc.)
    - not a stop word

    This prevents JS function names and Obsidian config noise from poisoning
    the heuristic domain detector.
    """
    g = _g.get("default")
    for nd in (g.nodes or []):
        kw = nd.keyword.lower()
        if nd.domain not in DOMAIN_KEYWORDS:
            continue
        if not (3 < len(kw) <= 20):
            continue
        if len(kw.split()) > 2:
            continue
        if not KEYWORD_PATTERN.match(nd.keyword):
            continue
        if kw in STOP_WORDS:
            continue
        DOMAIN_KEYWORDS[nd.domain].add(kw)

_bootstrap_domain_keywords()
_load_domain_signal()


def _signal_domain_switch(domain: str, intent: str) -> tuple[bool, "str | None", int]:
    """Domain-hysteresis auto-switch — SHARED by `auto` and `store_turn` (T65).

    Historically this lived only inside `auto`; once the curated loop made
    store_turn the recommended path, the signal was never fed and contexts
    never separated — everything piled up in 'default'. Semantics unchanged:
    switch only after CONTEXT_SWITCH_THRESHOLD consecutive turns signalling
    the same non-general domain; feedback/clarification turns don't count.
    Returns (switched, pending_domain, pending_count)."""
    if domain == "general" or domain == _g.active:
        if domain == _g.active and _domain_signal["domain"]:
            _domain_signal["domain"] = None
            _domain_signal["count"] = 0
            _save_domain_signal()
        return False, None, 0
    if intent not in ("feedback", "clarification"):
        if _domain_signal["domain"] == domain:
            _domain_signal["count"] += 1
        else:
            _domain_signal["domain"] = domain
            _domain_signal["count"] = 1
    if _domain_signal["count"] >= CONTEXT_SWITCH_THRESHOLD:
        _g.switch(domain)
        _domain_signal["domain"] = None
        _domain_signal["count"] = 0
        _save_domain_signal()
        return True, None, 0
    _save_domain_signal()
    return False, _domain_signal["domain"], _domain_signal["count"]

def _active_db_path() -> str:
    ctx = _g.active.replace("/", "__") if _g.active != "default" else "default"
    return os.path.join(GRAPHS_DIR, f"graph_{ctx}.db")

dedup_enabled = True
flash_enabled = True
# Auto-consolidation: se attivo (NS_CONSOLIDATE_AUTO), lo store viene consolidato
# (merge near-duplicati + drop orfani) ogni CONSOLIDATE_EVERY turni. (E1.4)
consolidate_auto = os.environ.get("NS_CONSOLIDATE_AUTO", "").strip().lower() in ("1","true","yes","on")
CONSOLIDATE_EVERY = _config.env_int("NEURON_CONSOLIDATE_EVERY", 20)
# Composite retrieval ranking (ADR-003 #3, E2.2): a node's rank blends semantic
# similarity to the query, its salience (what matters), and recency — "retrieve
# what matters, not only what matches". Weights are tunable and sum to 1.0.
RANK_WEIGHTS = {"sim": 0.5, "salience": 0.3, "recency": 0.2}
# Now that salience means "what matters" (E2.2), auto-consolidation protects the
# most-salient nodes from being merged away. Threshold mirrors the Hebbian "strong"
# bar: a node this reinforced is worth keeping intact. Tunable on real data (ADR-003).
CONSOLIDATE_PROTECT_SALIENCE = _config.env_int("NEURON_CONSOLIDATE_PROTECT_SALIENCE", 8)
# Piggyback stimulus (E2.5): a compact associative nudge appended to tool responses
# that don't already carry the full flash block (store_turn, pre_turn). Emitted only
# above the activation floor (no noise) and hard-capped to stay within ~40 tokens.
STIMULUS_MIN_ACTIVATION = _config.env_float("NEURON_STIMULUS_MIN_ACTIVATION", 0.15)
STIMULUS_MAX_CHARS = _config.env_int("NEURON_STIMULUS_MAX_CHARS", 200)   # ~40 tokens

# ---------------------------------------------------------------------------
# Context switch hysteresis
# ---------------------------------------------------------------------------
# The brain doesn't hard-reset context every time a topic is mentioned once.
# We only switch the active graph after CONTEXT_SWITCH_THRESHOLD consecutive
# turns that all signal the same non-active domain.
# A "feedback" or "clarification" turn resets the counter (not a real signal).
CONTEXT_SWITCH_THRESHOLD: int = 2

_domain_signal: dict = {
    "domain": None,   # domain being signaled
    "count": 0,       # consecutive turns signaling that domain
}

# ---------------------------------------------------------------------------
# Operating contract served automatically to every client
# ---------------------------------------------------------------------------
# Neuron's skills only help if the model actually follows them. Instead of hoping
# each client is configured to load skill files, the server delivers the guidance
# itself on two near-zero-cost channels:
#   1. INSTRUCTIONS — a short signpost injected ONCE at the MCP handshake (below):
#      always present, states the per-turn loop and points at the full playbook.
#   2. RESOURCES — the four skill files at stable neuron://skill/... URIs, read on
#      demand (zero standing token cost until a client opens one).
# The tool outputs themselves also re-teach the loop (see pre_turn/store_turn), so
# a model that skipped the manual is still funnelled onto the right path.

# SIGNPOST_BASE, _SKILLS, _SKILL_NAMES and _read_skill moved to neuron.funnel
# (T57) and re-imported below; _build_signpost stays here (needs the registry).
from neuron.funnel import (  # noqa: E402
    HELP_TEXT, SIGNPOST_BASE, _SKILLS, _SKILL_NAMES, _read_skill,
)


def _build_signpost() -> str:
    """SIGNPOST_BASE + a live one-liner on what's already in memory.

    Evaluated at handshake time (once per session start, since each stdio
    session is a fresh process), so the "are we connected" status is baked
    into the MCP `instructions` field itself — no extra tool call on clients
    that surface `instructions`. A registry hiccup never breaks the handshake:
    falls back to the static signpost alone.
    """
    try:
        g = _g.get()  # touches/loads the active context (seed-warm-starts if empty)
        nodes, links = len(g.nodes), len(g.links)
        if nodes:
            status_line = (
                f"\nConnected: '{_g.active}' already holds {nodes} nodes / "
                f"{links} links — pre_turn will surface what's relevant."
            )
        else:
            status_line = "\nConnected: memory is empty — this looks like a fresh start."
    except Exception as e:
        log.debug("could not build signpost status line: %s", e)
        status_line = ""
    return SIGNPOST_BASE + status_line

app = Server("neuron5", version=__version__)   # v5 "Synapse" identity (side-by-side with v4); version from neuron/__init__.py


# ---------------------------------------------------------------------------
# MCP resources — the "door": skills reachable at stable URIs, read on demand
# ---------------------------------------------------------------------------

@app.list_resources()
async def list_resources() -> list[Resource]:
    """Expose the skill files so any MCP client can navigate to the guidance."""
    return [
        Resource(
            uri=uri,                       # AnyUrl coerces the str (host_required=False)
            name=meta["name"],
            description=meta["description"],
            mimeType="text/markdown",
        )
        for uri, meta in _SKILLS.items()
    ]


@app.read_resource()
async def read_resource(uri) -> list[ReadResourceContents]:
    meta = _SKILLS.get(str(uri).rstrip("/"))
    if meta is None:
        raise ValueError(f"Unknown Neuron resource: {uri}")
    return [ReadResourceContents(content=_read_skill(meta["parts"]), mime_type="text/markdown")]


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------

@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="status",
            description=(
                "Current graph state: nodes, links, health, configuration. Safe first "
                "call to see if the memory holds anything. New to Neuron? The core "
                "workflow is a 2-step loop each substantive turn: pre_turn (before) then "
                "store_turn (after); call `help` or skill(name='auto-context') for the "
                "full playbook."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="store_turn",
            description=(
                "MEMORY LOOP — STEP 2 (after replying). Call this AFTER you answer a "
                "substantive turn, to persist what is new into long-term memory. "
                "Curate for a clean graph: topic = 3-5 words; keywords = 3-5 CONCEPT "
                "nouns / entities / tech (never verbs or filler like 'use', 'make'); "
                "links = typed edges between keywords (never link a keyword to itself). "
                "This is the preferred way to save — cleaner than auto(). Skip on trivial "
                "turns (greetings, acknowledgements, yes/no)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Topic of the turn (3-5 words)"},
                    "keywords": {"type": "array", "items": {"type": "string"}, "description": "Abstract keywords (3-5)"},
                    "domain": {"type": "string", "description": "Free-form topic label. Common values: AI, backend, frontend, gaming, architecture, general — but ANY label works (e.g. biology, finance, music, devops). Use 'general' if unsure."},
                    "intent": {"type": "string", "enum": ["question", "task", "exploration", "clarification", "feedback"]},
                    "sentiment": {"type": "string", "enum": ["neutral", "positive", "critical", "urgent"]},
                    "context": {"type": "string", "description": "Context path (e.g. java/spring). Defaults to active context.", "default": ""},
                    "episode": {
                        "type": "string",
                        "description": ("ONE compact fact sentence for this turn (max ~200 chars), "
                                        "e.g. 'chose https over wss because Turso rejects the ws "
                                        "handshake'. Attached to the first keyword; pre_turn will "
                                        "surface it later as a fact, not just a theme."),
                    },
                    "entities": {
                        "type": "array", "items": {"type": "string"},
                        "description": "Explicit entities (people, technologies, concepts, places)",
                    },
                    "tags": {
                        "type": "array", "items": {"type": "string"},
                        "description": "Free labels beyond domain",
                    },
                    "references": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string", "enum": ["file", "url", "commit"]},
                                "path": {"type": "string"},
                                "description": {"type": "string"},
                            },
                        },
                        "description": "References to files, URLs or commits",
                    },
                    "links": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "source": {"type": "string"},
                                "target": {"type": "string"},
                                "link_type": {"type": "string", "enum": ["cause-effect", "analogy", "evolution", "contrast", "deepening", "instance-of"]},
                                "weight": {"type": "string", "enum": ["strong", "medium", "tangential"]},
                                "rationale": {"type": "string"},
                            },
                        },
                        "description": "Links between current keywords and previous keywords",
                    },
                },
                "required": ["topic", "keywords", "domain", "intent", "sentiment"],
            },
        ),
        Tool(
            name="get_context",
            description=(
                "Retrieve related nodes and links for a topic/keyword — what the memory "
                "already knows. Call BEFORE answering when a question may have prior "
                "context worth recalling. For the normal start-of-turn load, prefer "
                "pre_turn (one shot: status + compact context)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Main keyword to search context for",
                    },
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional keywords to broaden the context search",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Search depth (1-3, default 1)",
                        "default": 1,
                    },
                    "max_tokens": {
                        "type": "integer",
                        "description": "Max output size in approx tokens (default 400, use 150 for compact injection).",
                        "default": 400,
                    },
                    "format": {
                        "type": "string",
                        "enum": ["full", "compact"],
                        "description": "'full' multi-line (default) or 'compact' single-line for system prompt injection.",
                        "default": "full",
                    },
                    "context": {"type": "string", "description": "Context path (e.g. java/spring). Defaults to active context.", "default": ""},
                },
                "required": ["topic"],
            },
        ),
        Tool(
            name="confirm",
            description=(
                "Feedback signal: confirm that context retrieved from the graph was useful. "
                "Boosts salience of specified keywords so they surface more prominently in "
                "future get_context calls. Call this when retrieved context directly influenced "
                "your response. Skipping is safe — it only affects future retrieval quality."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Keywords from the graph that were actually useful in this exchange",
                    },
                    "boost": {
                        "type": "integer",
                        "description": "Salience boost amount (default 2, max 5)",
                        "default": 2,
                    },
                    "context": {"type": "string", "description": "Context path. Defaults to active context.", "default": ""},
                },
                "required": ["keywords"],
            },
        ),
        Tool(
            name="find_candidates",
            description="Screening: find existing similar keywords (vector search). Call BEFORE store_turn.",
            inputSchema={
                "type": "object",
                "properties": {
                    "keywords": {
                        "type": "array", "items": {"type": "string"},
                        "description": "Keywords from current turn to find similar candidates for",
                    },
                    "top_n": {
                        "type": "integer", "description": "Number of candidates (default 8)", "default": 8,
                    },
                    "context": {"type": "string", "description": "Context path (e.g. java/spring). Defaults to active context.", "default": ""},
                },
                "required": ["keywords"],
            },
        ),
        Tool(
            name="vector_search",
            description="Semantic vector search. Find similar keywords via Turso vector_distance_cos or a Python cosine fallback (384-dim fastembed embeddings, NS_EMBED_MODEL).",
            inputSchema={
                "type": "object",
                "properties": {
                    "keywords": {
                        "type": "array", "items": {"type": "string"},
                        "description": "Query keywords for vector search",
                    },
                    "top_n": {
                        "type": "integer", "description": "Number of results (default 8)", "default": 8,
                    },
                    "context": {"type": "string", "description": "Context path (e.g. java/spring). Defaults to active context.", "default": ""},
                },
                "required": ["keywords"],
            },
        ),
        Tool(
            name="summary",
            description="Textual graph summary: top keywords, recent links, health, forgotten concepts",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="forgotten",
            description="Find keywords not touched in N turns (decaying salience). Useful for rediscovering lost concepts.",
            inputSchema={
                "type": "object",
                "properties": {
                    "threshold": {
                        "type": "integer", "description": "Inactivity turns threshold (default 5)", "default": 5,
                    },
                    "top_n": {
                        "type": "integer", "description": "How many to show (default 10)", "default": 10,
                    },
                    "context": {"type": "string", "description": "Context path (e.g. java/spring). Defaults to active context.", "default": ""},
                },
            },
        ),
        Tool(
            name="prune",
            description="Force prune inactive tangential links",
            inputSchema={
                "type": "object",
                "properties": {
                    "context": {"type": "string", "description": "Context path (e.g. java/spring). Defaults to active context.", "default": ""},
                },
            },
        ),
        Tool(
            name="consolidate",
            description="Consolidate the graph: merge near-duplicate concepts (cosine) and archive low-salience orphans to a recoverable _graveyard. Keeps the memory clean; safe to run periodically.",
            inputSchema={
                "type": "object",
                "properties": {
                    "context": {"type": "string", "description": "Context path. Defaults to active context.", "default": ""},
                    "merge": {"type": "boolean", "description": "Merge near-duplicate nodes (default true).", "default": True},
                    "drop_orphans": {"type": "boolean", "description": "Archive low-salience orphan nodes (default true).", "default": True},
                    "sim_threshold": {"type": "number", "description": "Cosine threshold for merging (default 0.85).", "default": 0.85},
                },
            },
        ),
        Tool(
            name="dedup",
            description="Toggle keyword deduplication",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="flash",
            description="Toggle semantic flashbacks",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="reset",
            description="Reset the graph and start over",
            inputSchema={
                "type": "object",
                "properties": {
                    "context": {"type": "string", "description": "Context path (e.g. java/spring). Defaults to active context.", "default": ""},
                },
            },
        ),
        Tool(
            name="extract",
            description="Automatic semantic extraction from text: keyword, topic, domain, intent, sentiment, entities. Heuristic (0 token) — no LLM extraction.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to analyze (user message)",
                    },
                    "context": {"type": "string", "description": "Context path (e.g. java/spring). Defaults to active context.", "default": ""},
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="auto",
            description="POST fallback (0-token): one-shot extract + topic-shift + auto-link + save. Prefer a curated store_turn when you can pick the concepts yourself; use auto only for throwaway turns.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "User message to analyze and archive",
                    },
                    "context": {"type": "string", "description": "Context path (e.g. java/spring). Defaults to active context.", "default": ""},
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="export",
            description="Export the complete graph as JSON",
            inputSchema={
                "type": "object",
                "properties": {
                    "context": {"type": "string", "description": "Context path (e.g. java/spring). Defaults to active context.", "default": ""},
                },
            },
        ),
        Tool(
            name="merge",
            description=(
                "Merge duplicate or near-duplicate nodes. "
                "Moves all links from `aliases` into `canonical`, sums salience, then deletes the aliases. "
                "Use after find_candidates reveals near-duplicates (e.g. 'spring boot' / 'Spring Boot' / 'Spring Boot 3.2')."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "canonical": {
                        "type": "string",
                        "description": "The keyword to keep as the single authoritative node",
                    },
                    "aliases": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Keywords to absorb into canonical and then delete",
                    },
                    "context": {"type": "string", "description": "Context path. Defaults to active context.", "default": ""},
                },
                "required": ["canonical", "aliases"],
            },
        ),
        Tool(
            name="switch_context",
            description="Switch active context (creates if new). E.g. 'java/spring', 'python/django'.",
            inputSchema={
                "type": "object",
                "properties": {
                    "context": {
                        "type": "string",
                        "description": "Context path to switch to",
                    },
                },
                "required": ["context"],
            },
        ),
        Tool(
            name="list_contexts",
            description="List all available contexts with metadata.",
            inputSchema={
                "type": "object",
                "properties": {
                    "parent": {
                        "type": "string",
                        "description": "Optional parent filter",
                    },
                },
            },
        ),
        Tool(
            name="pre_turn",
            description=(
                "MEMORY LOOP — STEP 1 (before replying). Call this FIRST on any "
                "substantive turn to load relevant past context in one shot "
                "(status + get_context in compact form). Fold what it returns silently "
                "into your answer; do not announce it. Then reply, then call store_turn "
                "(step 2). Skip only on trivial turns or when the graph is empty. Ideal "
                "for clients without automatic context-injection hooks."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Current topic or question to fetch context for",
                    },
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional keywords to broaden context search",
                    },
                    "max_tokens": {
                        "type": "integer",
                        "description": "Max tokens for context output (default 200)",
                        "default": 200,
                    },
                },
                "required": ["topic"],
            },
        ),
        Tool(
            name="help",
            description="Show every Neuron command (one line each) plus how to use Neuron well. Call once at the start if unsure; full playbook: call skill(name='auto-context').",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="skill",
            description="Return the FULL text of a Neuron skill/playbook on demand — token-cheap, fetch it only when you need the details. Use after the compact opener to load the complete workflow or curation rules.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "enum": _SKILL_NAMES,
                        "description": "Which skill: auto-context (PRE/POST workflow, recommended), curated (clean-graph patterns), base/full (references).",
                        "default": "auto-context",
                    },
                },
            },
        ),
    ]


def _resolve_context(
    search_kws: set[str],
    depth: int,
    g: "Graph",
    ctx: str,
) -> tuple[list, list, bool, str | None, "Graph"]:
    """Core context-resolution logic shared by get_context and pre_turn.

    Returns (related_links_sorted, top_nodes, used_fallback, inherited_ctx, g).
    `g` may change when context inheritance kicks in.
    """
    # Normalize search keywords to match graph's lowercased node/link keys
    search_kws = {kw.strip().lower() for kw in search_kws if kw.strip()}
    related_nodes: set[str] = set()
    related_links: list = []
    current = search_kws.copy()
    for _ in range(depth):
        new_kws: set[str] = set()
        for lk in g.links:
            if lk.link_type == "drift":
                continue   # drift is cross-context: surfaced separately at depth>=3
            if lk.source in current and lk.target not in current:
                new_kws.add(lk.target)
                related_links.append(lk)
            elif lk.target in current and lk.source not in current:
                new_kws.add(lk.source)
                related_links.append(lk)
        current = new_kws
        related_nodes.update(current)
    related_nodes.update(search_kws)

    # Vector fallback
    used_fallback = False
    if not related_links:
        existing = {nd.keyword for nd in g.nodes}
        if not search_kws & existing:
            vec_results = _search_embeddings(list(search_kws), top_n=5, graph=g)
            if vec_results:
                vec_kws = {kw for kw, _ in vec_results}
                current = vec_kws.copy()
                for _ in range(depth):
                    new_kws = set()
                    for lk in g.links:
                        if lk.link_type == "drift":
                            continue
                        if lk.source in current and lk.target not in current:
                            new_kws.add(lk.target)
                            related_links.append(lk)
                        elif lk.target in current and lk.source not in current:
                            new_kws.add(lk.source)
                            related_links.append(lk)
                    current = new_kws
                    related_nodes.update(current)
                related_nodes.update(vec_kws)
                used_fallback = True

    # Context inheritance: walk parent chain if still empty
    inherited_ctx: str | None = None
    if not related_links:
        chain = _g.resolve_chain(ctx or None)
        for ancestor_g in chain[1:]:
            for lk in ancestor_g.links:
                if lk.link_type == "drift":
                    continue
                if lk.source in search_kws or lk.target in search_kws:
                    related_links.append(lk)
                    related_nodes.add(lk.source)
                    related_nodes.add(lk.target)
            if related_links:
                g = ancestor_g
                for cname, cg in _g._graphs.items():
                    if cg is ancestor_g:
                        inherited_ctx = cname
                        break
                break

    # E3.2: cross-context drift links surface only on deep queries (depth>=3) —
    # an opt-in cost. Add those anchored on a searched/related keyword so they get
    # ranked and rendered next to the normal links (their foreign target is NOT
    # added to related_nodes, so node ranking is untouched).
    if depth >= 3:
        anchor = related_nodes | search_kws
        for lk in g.drift_links():
            if lk.source in anchor:
                related_links.append(lk)

    # Rank links
    seen_pairs: set[tuple[str, str]] = set()
    deduped: list = []
    for lk in sorted(related_links,
                     key=lambda lk: (WEIGHT_ORDER.get(lk.weight, 0), lk.last_active_turn),
                     reverse=True):
        pair = (lk.source, lk.target)
        rev  = (lk.target, lk.source)
        if pair not in seen_pairs and rev not in seen_pairs:
            seen_pairs.add(pair)
            deduped.append(lk)
    related_links_sorted = deduped

    # Rank nodes — composite salience-aware score (ADR-003 #3, E2.2). Blend of
    # semantic similarity to the query, node salience (normalized), and recency,
    # so a highly-salient neighbour surfaces even without a direct vector match.
    sim_map = (
        {kw: s for kw, s in _search_embeddings(list(search_kws),
                                               top_n=max(len(g.nodes), 1), graph=g)}
        if search_kws else {}
    )
    max_sal = max((g.get_node(k).salience for k in related_nodes if g.get_node(k)),
                  default=0) or 1
    node_scores: dict[str, float] = {}
    for nd_kw in related_nodes:
        nd = g.get_node(nd_kw)
        if nd is None:
            continue
        sim      = sim_map.get(nd_kw, 0.0)
        salience = nd.salience / max_sal
        recency  = 1.0 / (max(0, g.turn_count - nd.turn) + 1)
        node_scores[nd_kw] = (RANK_WEIGHTS["sim"] * sim
                              + RANK_WEIGHTS["salience"] * salience
                              + RANK_WEIGHTS["recency"] * recency)
    top_nodes = sorted(node_scores.items(), key=lambda x: -x[1])

    return related_links_sorted, top_nodes, used_fallback, inherited_ctx, g


_LOOP_HINT = (
    "\n(Neuron loop: pre_turn before replying, store_turn after — `help` for "
    "the full playbook.)"
)
# A4 (Piano 05): the hint is a one-shot per process/session — after the model
# has seen it once, repeating it on every response only burns tokens.
_loop_hint_sent = False

# T55: per-session loop-compliance telemetry (process = one stdio session).
# Makes "is the model actually doing the loop?" a number instead of a feeling.
_loop_stats = {"pre_turn": 0, "store_turn": 0, "other": 0}


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Piggyback wrapper (E2.5b): reinforce the pre_turn/store_turn loop on ANY
    tool response that doesn't already carry a loop nudge, not just the two
    core-loop tools. Whichever tool the model reaches for first, the response
    text — the one channel every MCP host must feed back to the model — still
    points it at the right workflow. Skipped for pre_turn/store_turn (they
    already build a richer, context-aware stimulus block) and for skill/help
    (already the full manual)."""
    global _loop_hint_sent
    _turn_search_cache.clear()   # A2: the memo lives for ONE tool call
    _loop_stats[name if name in ("pre_turn", "store_turn") else "other"] += 1  # T55
    result = await _call_tool_impl(name, arguments)
    if (
        not _loop_hint_sent
        and name not in ("pre_turn", "store_turn", "skill", "help")
        and len(result) == 1
        and result[0].type == "text"
        and result[0].text.lstrip()[:1] not in ("{", "[")  # don't corrupt JSON outputs (export/consolidate/...)
        and "🧠" not in result[0].text
        and "pre_turn" not in result[0].text
    ):
        result = [TextContent(type="text", text=result[0].text + _LOOP_HINT)]
        _loop_hint_sent = True
    return result


async def _tool_help(arguments: dict, ctx: str, g) -> list[TextContent]:
    return [TextContent(type="text", text=HELP_TEXT)]


async def _tool_skill(arguments: dict, ctx: str, g) -> list[TextContent]:
    which = arguments.get("name", "auto-context")
    meta = _SKILLS.get(f"neuron://skill/{which}")
    if meta is None:
        valid = ", ".join(k.rsplit("/", 1)[1] for k in _SKILLS)
        return [TextContent(type="text", text=f"Unknown skill '{which}'. Available: {valid}.")]
    return [TextContent(type="text", text=_read_skill(meta["parts"]))]


async def _tool_status(arguments: dict, ctx: str, g) -> list[TextContent]:
    ctx_label = ctx or _g.active
    h = compute_health(g.nodes, g.links, g.pruned_count, g.turn_count)
    total = h["total"]
    active = len(g.get_active_links())
    sr = h["strong_medium_pct"]
    types = h["types"]
    pr = h["pruned_pct"]
    npt = h["nodes_per_turn"]
    engine = _db.ENGINE_NAME
    return [TextContent(type="text", text=(
        f"Context: {ctx_label}\n"
        f"Turn {g.turn_count} | Nodes: {len(g.nodes)} | Links: {total} (active {active})\n"
        f"Strong+medium: {sr:.0f}% | Types: {types} | Pruned: {pr:.0f}% | Nodes/turn: {npt:.1f}\n"
        f"Dedup: {'ON' if dedup_enabled else 'OFF'} | Flash: {'ON' if flash_enabled else 'OFF'} | "
        f"SwitchThreshold: {CONTEXT_SWITCH_THRESHOLD} turns"
        + (f" | Pending→{_domain_signal['domain']} ({_domain_signal['count']}/{CONTEXT_SWITCH_THRESHOLD})"
           if _domain_signal.get("domain") else "") + "\n"
        f"Engine: {engine} | Embedding: {VECTOR_DIM}dim\n"
        # T55: loop compliance this session. store_turn defines a "turn";
        # a healthy loop has pre_turn ≈ store_turn and both > 0.
        f"Loop (this session): pre_turn {_loop_stats['pre_turn']} | "
        f"store_turn {_loop_stats['store_turn']} | other tools {_loop_stats['other']}"
        + (" | ⚠ pre_turn missing before stores"
           if _loop_stats['store_turn'] > _loop_stats['pre_turn'] + 1 else "") + "\n"
        "Tip: run the 'help' tool (or ask \"/help\") to see all commands & features."
    ))]


async def _tool_store_turn(arguments: dict, ctx: str, g) -> list[TextContent]:
    topic = arguments["topic"]
    keywords = arguments["keywords"]
    domain = _normalize_domain(str(arguments["domain"]))
    intent = arguments["intent"]
    # T65: feed the domain-hysteresis switch from the CURATED path too —
    # unless the caller pinned an explicit context (explicit always wins).
    _ctx_switched, _pend_dom, _pend_n = False, None, 0
    if not ctx:
        _ctx_switched, _pend_dom, _pend_n = _signal_domain_switch(domain, intent)
        if _ctx_switched:
            g = _g.get()   # the freshly activated context graph
    g.turn_count += 1
    turn = g.turn_count
    # T54 curation gate: drop verb/phrase/path keywords, remap near-dups
    # onto existing nodes (case/accents/plural). Soft: the turn always goes
    # through if anything is salvageable; the notes teach the model inline.
    _existing_map = {_cur._dup_key(nd.keyword): nd.keyword for nd in g.nodes}
    keywords, _cur_notes = _cur.vet_keywords(list(keywords), _existing_map)
    if not keywords:
        g.turn_count -= 1   # nothing stored, don't burn the turn counter
        return [TextContent(type="text", text=(
            "Validation error: no usable keywords after curation."
            + _cur.curation_note(_cur_notes)
            + "\nUse 3-5 singular concept NOUNS (entities/tech/ideas)."))]
    sentiment = arguments["sentiment"]   # domain/intent already read above (T65)
    entities = arguments.get("entities", [])
    tags = arguments.get("tags", [])
    references = arguments.get("references", [])
    new_links_data = arguments.get("links", [])

    # T54: canonicalize link endpoints through the same dup-map and drop
    # links whose endpoints survived neither curation nor the graph —
    # otherwise a dropped verb-keyword would leave a dangling link behind.
    _final_keys = {_cur._dup_key(k): k for k in keywords}
    _canon = lambda e: _final_keys.get(_cur._dup_key(e)) or _existing_map.get(_cur._dup_key(e))
    _vetted_links = []
    for ld in new_links_data:
        cs, ct = _canon(ld.get("source", "")), _canon(ld.get("target", ""))
        if cs and ct and cs != ct:
            ld = dict(ld, source=cs, target=ct)
            _vetted_links.append(ld)
        else:
            _cur_notes.append(
                f"dropped link {ld.get('source','?')}→{ld.get('target','?')}: "
                "endpoints must be stored concepts (never a self-link)")
    new_links_data = _vetted_links

    err = validate_turn_input(keywords, topic, new_links_data,
                              entities=entities, tags=tags, references=references)
    if err:
        return [TextContent(type="text", text=f"Validation error: {err}")]

    for kw in keywords:
        existing = g.get_node(kw)
        if dedup_enabled and existing:
            existing.salience += 1
            existing.turn = turn
            existing.topic = topic
            existing.domain = domain
            existing.sentiment = sentiment
            g.mark_node_dirty(existing.keyword)   # in-place change: track it
        else:
            g.add_node(Node(keyword=kw, turn=turn, topic=topic,
                            domain=domain, sentiment=sentiment,
                            entities=entities, tags=tags,
                            references=references))

    # T56: one compact fact sentence for this turn, attached to the first
    # (most salient) keyword — nodes carry decisions, not just themes.
    _episode_txt = str(arguments.get("episode", "") or "").strip()
    if _episode_txt:
        g.add_episode(keywords[0], _episode_txt, turn)

    for ld in new_links_data:
        lk = Link(
            source=ld["source"], target=ld["target"],
            link_type=ld.get("link_type", "deepening"),
            weight=ld.get("weight", "medium"),
            rationale=ld.get("rationale", ""),
            created_turn=turn, last_active_turn=turn,
        )
        src = g.get_node(lk.source)
        tgt = g.get_node(lk.target)
        if src and tgt and src.domain == tgt.domain and lk.weight == "tangential":
            lk.weight = "medium"
        g.add_link(lk)
        if src:
            src.salience += WEIGHT_ORDER[lk.weight]
            g.mark_node_dirty(src.keyword)
        if tgt:
            tgt.salience += WEIGHT_ORDER[lk.weight]
            g.mark_node_dirty(tgt.keyword)

    g.last_sentiment = sentiment
    g.last_topic = topic
    g.last_keywords = keywords
    g.reinforce_coactivation(keywords)   # Hebbian: co-active links wire together (E2.1)
    g.increment_inactivity(set(keywords))
    removed = g.prune_tangential()
    _g.save(ctx or None)
    if consolidate_auto and g.turn_count % CONSOLIDATE_EVERY == 0:
        # E2.2: protect high-salience nodes from being merged away.
        g.consolidate(drop_orphans=True, protect_salience=CONSOLIDATE_PROTECT_SALIENCE)
        _g.save(ctx or None)

    return [TextContent(type="text", text=(
        f"Turn {turn} saved. Nodes: {len(g.nodes)}, Links: {len(g.links)}"
        + (f", pruned: {removed}" if removed else "")
        # T65: surface the context dynamics — committed switch or pending signal
        + (f"\n⇄ context switched → '{_g.active}'" if _ctx_switched else
           (f"\n(domain signal: {_pend_dom} {_pend_n}/{CONTEXT_SWITCH_THRESHOLD} — "
            f"context will switch if it persists)" if _pend_dom else ""))
        + _cur.curation_note(_cur_notes)   # T54: teach curation in-context
        + _stimulus_block(g, keywords)   # E2.5: piggyback the top stimulus
        + "\n→ if the loaded context helped this turn, call confirm(keywords); "
          "start the next turn with pre_turn."
    ))]


async def _tool_get_context(arguments: dict, ctx: str, g) -> list[TextContent]:
    topic = arguments.get("topic", "")
    extra_kws = arguments.get("keywords", [])
    search_kws: set[str] = set()
    if topic:
        search_kws.add(topic)
    if isinstance(extra_kws, list):
        search_kws.update(extra_kws)
    depth = min(arguments.get("depth", 1), 3)
    fmt        = arguments.get("format", "full")
    max_tokens = int(arguments.get("max_tokens", 400))
    char_budget = max_tokens * 4

    related_links_sorted, top_nodes, used_fallback, inherited_ctx, g = \
        _resolve_context(search_kws, depth, g, ctx)


    if fmt == "compact":
        # Single-line summary — ideal for system-prompt injection
        parts = []
        if related_links_sorted:
            link_strs = [
                f"{lk.source}-[{lk.weight[0]}]->{lk.target}"
                + (f"@{lk.target_context}" if lk.link_type == "drift" else "")
                for lk in related_links_sorted[:6]
            ]
            parts.append("links:" + "|".join(link_strs))
        if top_nodes:
            node_strs = [f"{kw}({sc:.0f})" for kw, sc in top_nodes[:5]]
            parts.append("nodes:" + ",".join(node_strs))
        if used_fallback:
            parts.append("(vector fallback)")
        if inherited_ctx:
            parts.append(f"(from:{inherited_ctx})")
        out = " | ".join(parts) if parts else "no context"
        return [TextContent(type="text", text=out[:char_budget])]

    # Full format (default)
    _ctx_suffix = ""
    if used_fallback:
        _ctx_suffix = " (vector fallback)"
    elif inherited_ctx:
        _ctx_suffix = f" (inherited from: {inherited_ctx})"
    lines = [f"Context{_ctx_suffix}:"]
    if related_links_sorted:
        lines.append("Links (by weight):")
        for lk in related_links_sorted[:10]:
            tgt = (f"{lk.target}@{lk.target_context}"
                   if lk.link_type == "drift" else lk.target)
            lines.append(
                f"  [{lk.weight:10s}] {lk.source} ->({lk.link_type})-> {tgt}"
                + (f"  # {lk.rationale}" if lk.rationale else "")
            )
    elif used_fallback:
        lines.append("  (similar nodes exist but no links yet)")
    else:
        lines.append("  (no related links found)")

    if top_nodes:
        lines.append(f"\nTop nodes (depth={depth}):")
        for nd_kw, score in top_nodes[:6]:
            nd  = g.get_node(nd_kw)
            dom = nd.domain if nd else "?"
            sal = nd.salience if nd else 0
            lines.append(f"  {nd_kw} [{dom}, sal={sal}, score={score:.0f}]")

    out = "\n".join(lines)
    return [TextContent(type="text", text=out[:char_budget])]


async def _tool_find_candidates(arguments: dict, ctx: str, g) -> list[TextContent]:
    keywords = arguments["keywords"]
    top_n = min(arguments.get("top_n", 8), 20)
    if not g.nodes:
        return [TextContent(type="text", text="No nodes in graph (empty).")]

    results = _search_embeddings(keywords, top_n, graph=g)
    if not results:
        return [TextContent(type="text", text="No candidates found.")]

    engine_tag = _db.ENGINE_NAME if TURSO_ENGINE else "Python"
    lines = [f"Candidates for {keywords} ({engine_tag} vector search):"]
    for kw, score in results:
        nd = g.get_node(kw)
        links_str = ""
        if nd:
            node_links = [
                lk for lk in g.links
                if lk.source == kw or lk.target == kw
            ][:4]
            links_str = ", ".join(f"{lk.source} -> {lk.target} [{lk.link_type}]" for lk in node_links) if node_links else "(no links)"
        lines.append(f"  {kw:20s}  sim={score:.4f}  links: {links_str}")
    lines.append(f"Total candidates: {len(results)}")
    return [TextContent(type="text", text="\n".join(lines))]


async def _tool_vector_search(arguments: dict, ctx: str, g) -> list[TextContent]:
    keywords = arguments["keywords"]
    top_n = min(arguments.get("top_n", 8), 20)
    if not g.nodes:
        return [TextContent(type="text", text="No nodes in graph.")]
    results = _search_embeddings(keywords, top_n, graph=g)
    if not results:
        return [TextContent(type="text", text="No results.")]
    engine_tag = _db.ENGINE_NAME if TURSO_ENGINE else "Python"
    lines = [f"Vector search for {keywords} ({VECTOR_DIM}dim, {engine_tag}):"]
    for kw, score in results:
        nd = g.get_node(kw)
        extra = ""
        if nd:
            extra = f"  salience={nd.salience}  turn={nd.turn}"
        lines.append(f"  {kw:20s}  cos={score:.4f}{extra}")
    return [TextContent(type="text", text="\n".join(lines))]


async def _tool_summary(arguments: dict, ctx: str, g) -> list[TextContent]:
    ctx_info = f"Context: {_g.active}"
    h = compute_health(g.nodes, g.links, g.pruned_count, g.turn_count)
    total = h["total"]
    active = len(g.get_active_links())
    strong = h["strong"]
    medium = h["medium"]
    types = h["types"]
    lines = [
        ctx_info,
        f"Turns: {g.turn_count}  |  Nodes: {len(g.nodes)}  |  Links: {total} (active {active})",
        f"Strong: {strong}  |  Medium: {medium}  |  Tangential: {h['tangential']}",
        f"Link types: {types}  |  Pruned: {g.pruned_count}",
        f"Engine: {_db.ENGINE_NAME}  |  Embedding: {VECTOR_DIM}dim",
    ]
    top_kw = sorted(g.nodes, key=lambda nd: -nd.salience)[:10]
    if top_kw:
        lines.append("Top keywords (salience):")
        for nd in top_kw[:10]:
            lines.append(f"  {nd.keyword:20s} salience={nd.salience:3d}  turn={nd.turn}")
    recent_links = sorted(g.links, key=lambda lk: -lk.created_turn)[:6]
    if recent_links:
        lines.append("Recent links:")
        for lk in recent_links:
            lines.append(f"  {lk.source} ->({lk.link_type})-> {lk.target} [{lk.weight}]  turn {lk.created_turn}")
    if g.compressed_summary:
        lines.append(f"Summary: {g.compressed_summary}")
    return [TextContent(type="text", text="\n".join(lines))]


async def _tool_forgotten(arguments: dict, ctx: str, g) -> list[TextContent]:
    threshold = max(arguments.get("threshold", 5), 1)
    top_n = min(arguments.get("top_n", 10), 30)
    now = g.turn_count
    forgotten = [
        nd for nd in g.nodes
        if now - nd.turn >= threshold and nd.salience > 0
    ]
    forgotten.sort(key=lambda nd: nd.turn)
    if not forgotten:
        return [TextContent(type="text", text=f"No forgotten concepts in {threshold} turns.")]
    lines = [f"Concepts not touched >= {threshold} turns (now={now}):"]
    for nd in forgotten[:top_n]:
        stale = now - nd.turn
        lines.append(f"  {nd.keyword:20s} last_turn={nd.turn}  ({stale} turns ago)  salience={nd.salience}")
    lines.append(f"Total: {len(forgotten)} forgotten concepts")
    return [TextContent(type="text", text="\n".join(lines))]


async def _tool_prune(arguments: dict, ctx: str, g) -> list[TextContent]:
    removed = g.prune_tangential()
    _g.save(ctx or None)
    return [TextContent(type="text", text=f"Pruned {removed} tangential links.")]


async def _tool_consolidate(arguments: dict, ctx: str, g) -> list[TextContent]:
    do_merge = arguments.get("merge", True)
    report = g.consolidate(
        sim_threshold=float(arguments.get("sim_threshold", 0.85)) if do_merge else 2.0,
        drop_orphans=arguments.get("drop_orphans", True),
    )
    _g.save(ctx or None)
    merged = [r for r in report if "kept" in r]
    dropped = [r for r in report if "dropped" in r]
    return [TextContent(type="text", text=json.dumps({
        "merged": merged, "dropped": [r["dropped"] for r in dropped],
        "nodes": len(g.nodes), "links": len(g.links),
    }))]


async def _tool_dedup(arguments: dict, ctx: str, g) -> list[TextContent]:
    global dedup_enabled
    dedup_enabled = not dedup_enabled
    state = "ON" if dedup_enabled else "OFF"
    return [TextContent(type="text", text=f"Keyword deduplication: {state}")]


async def _tool_flash(arguments: dict, ctx: str, g) -> list[TextContent]:
    global flash_enabled
    flash_enabled = not flash_enabled
    state = "ON" if flash_enabled else "OFF"
    return [TextContent(type="text", text=f"Semantic flash: {state}")]


async def _tool_reset(arguments: dict, ctx: str, g) -> list[TextContent]:
    _g.reset(ctx or None)
    return [TextContent(type="text", text="Graph reset.")]


async def _tool_export(arguments: dict, ctx: str, g) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(g.export(), ensure_ascii=False, indent=2))]


async def _tool_extract(arguments: dict, ctx: str, g) -> list[TextContent]:
    text = arguments["text"]
    result = await _auto_extract(text)
    return [TextContent(type="text", text=json.dumps({
        "topic": result.topic,
        "keywords": result.keywords,
        "entities": result.entities,
        "domain": result.domain,
        "intent": result.intent,
        "sentiment": result.sentiment,
        "tags": result.tags,
    }, ensure_ascii=False, indent=2))]


async def _tool_auto(arguments: dict, ctx: str, g) -> list[TextContent]:
    text = arguments["text"][:3000]  # truncate: embedding is effective up to ~3k chars
    extraction = await _auto_extract(text)
    shift_detected, overlap = _detect_topic_shift(extraction.keywords, graph=g)
    # normalize domain via aliases, refine if general
    domain = _normalize_domain(extraction.domain)
    alt_domains: list[str] = []
    if domain == "general":
        refined, alt = _refine_domain(extraction.keywords)
        if refined:
            domain = refined
            alt_domains = [_normalize_domain(a) for a in alt if _normalize_domain(a) != domain]
    elif domain in DOMAIN_ALIASES:
        domain = DOMAIN_ALIASES[domain]
    tags = list(extraction.tags) + alt_domains
    extraction = ExtractionResult(
        topic=extraction.topic, keywords=extraction.keywords,
        entities=extraction.entities, domain=domain,
        intent=extraction.intent, sentiment=extraction.sentiment,
        tags=tags,
    )
    # auto-switch context with hysteresis
    # Only switch after CONTEXT_SWITCH_THRESHOLD consecutive turns signaling
    # the same domain. Feedback/clarification turns don't count as signals.
    switched = False
    pending_domain: str | None = None
    pending_turns: int = 0

    if domain != "general" and domain != _g.active:
        # This turn signals a domain change — is it the same as before?
        if extraction.intent not in ("feedback", "clarification"):
            if _domain_signal["domain"] == domain:
                _domain_signal["count"] += 1
            else:
                # New domain signal — reset counter
                _domain_signal["domain"] = domain
                _domain_signal["count"] = 1

        pending_domain = _domain_signal["domain"]
        pending_turns = _domain_signal["count"]

        if _domain_signal["count"] >= CONTEXT_SWITCH_THRESHOLD:
            # Threshold reached — commit the switch
            _g.switch(domain)
            g = _g.get()
            ctx = domain
            switched = True
            _domain_signal["domain"] = None
            _domain_signal["count"] = 0
            pending_domain = None
            pending_turns = 0
    else:
        # We're already in the right context (or domain is general) — clear signal
        if domain == _g.active:
            _domain_signal["domain"] = None
            _domain_signal["count"] = 0

    err = validate_turn_input(extraction.keywords, extraction.topic, [], entities=extraction.entities, tags=extraction.tags)
    if err:
        return [TextContent(type="text", text=f"Validation error: {err}")]

    g.turn_count += 1
    turn = g.turn_count
    new_links = _auto_link(extraction.keywords, turn, graph=g)

    # cross-domain linking: also search alternative domain contexts for similar nodes
    for alt_dom in alt_domains:
        alt_g = _g.get(alt_dom)
        for kw in extraction.keywords:
            alt_candidates = _search_embeddings([kw], top_n=3, graph=alt_g)
            for ckw, sim in alt_candidates:
                # only link if the node exists in the alt context
                tgt = alt_g.get_node(ckw)
                if tgt and sim > 0.3:
                    existing = g.get_node(kw)
                    if existing:
                        g.add_link(Link(
                            source=kw, target=ckw, link_type="analogy",
                            weight="medium" if sim > 0.5 else "tangential",
                            rationale=f"cross-domain ({alt_dom}, sim={sim:.2f})",
                            created_turn=turn, last_active_turn=turn,
                        ))

    salience_boost = INTENT_SALIENCE.get(extraction.intent, 1)
    for kw in extraction.keywords:
        existing = g.get_node(kw)
        if dedup_enabled and existing:
            existing.salience += salience_boost
            existing.turn = turn
            existing.topic = extraction.topic
            existing.domain = extraction.domain
            existing.sentiment = extraction.sentiment
            g.mark_node_dirty(existing.keyword)   # in-place change: track it
        else:
            g.add_node(Node(
                keyword=kw, turn=turn, topic=extraction.topic,
                domain=extraction.domain, sentiment=extraction.sentiment,
                entities=extraction.entities, tags=extraction.tags,
            ))
            # cross-context dedup: link to identical keywords in other contexts
            for alt_name, alt_g in list(_g._graphs.items()):
                if alt_name == _g.active:
                    continue
                alt_nd = alt_g.get_node(kw)
                if alt_nd:
                    g.add_link(Link(
                        source=kw, target=kw, link_type="analogy",
                        weight="strong",
                        rationale=f"cross-context dedup ({_g.active} <-> {alt_name})",
                        created_turn=turn, last_active_turn=turn,
                    ))

    for lk in new_links:
        src = g.get_node(lk.source)
        tgt = g.get_node(lk.target)
        if src and tgt and src.domain == tgt.domain and lk.weight == "tangential":
            lk.weight = "medium"
        g.add_link(lk)
        if src:
            src.salience += WEIGHT_ORDER[lk.weight]
            g.mark_node_dirty(src.keyword)
        if tgt:
            tgt.salience += WEIGHT_ORDER[lk.weight]
            g.mark_node_dirty(tgt.keyword)

    g.last_sentiment = extraction.sentiment
    g.last_topic = extraction.topic
    g.last_keywords = extraction.keywords
    g.reinforce_coactivation(extraction.keywords)   # Hebbian (E2.1)
    g.increment_inactivity(set(extraction.keywords))
    removed = g.prune_tangential()
    _save_domain_signal()
    _g.save(ctx or None)

    context_window = _build_context_window(extraction, turn, graph=g)

    return [TextContent(type="text", text=json.dumps({
        "turn": turn,
        "topic_shift": shift_detected,
        "overlap": round(overlap, 2),
        "context_switched": switched,
        "active_context": _g.active,
        "pending_context": {
            "domain": pending_domain,
            "turns_signaled": pending_turns,
            "threshold": CONTEXT_SWITCH_THRESHOLD,
        } if pending_domain else None,
        "extraction": {
            "topic": extraction.topic,
            "keywords": extraction.keywords,
            "domain": extraction.domain,
            "intent": extraction.intent,
            "sentiment": extraction.sentiment,
            "entities": extraction.entities,
        },
        "links_created": len(new_links),
        "nodes_total": len(g.nodes),
        "links_total": len(g.links),
        "context_window": context_window,
    }, ensure_ascii=False, indent=2))]


async def _tool_switch_context(arguments: dict, ctx: str, g) -> list[TextContent]:
    _g.switch(arguments["context"])
    return [TextContent(type="text", text=f"Switched to context: {_g.active}")]


async def _tool_list_contexts(arguments: dict, ctx: str, g) -> list[TextContent]:
    contexts = _g.list_contexts(arguments.get("parent"))
    lines = [f"  {c['context']:30s} nodes={c['nodes']} links={c['links']} turns={c['turns']}{' <- active' if c['active'] else ''}" for c in contexts]
    return [TextContent(type="text", text="\n".join(lines) or "No contexts found.")]


async def _tool_pre_turn(arguments: dict, ctx: str, g) -> list[TextContent]:
    topic_pt = arguments.get("topic", "")
    extra_kws_pt = arguments.get("keywords", [])
    max_tokens_pt = int(arguments.get("max_tokens", 200))
    char_budget_pt = max_tokens_pt * 4
    # Status line
    g_pt = _g.get()
    ctx_label = _g.active
    total_pt  = len(g_pt.links)
    active_pt = len(g_pt.get_active_links())
    status_line = (f"[neuron] ctx={ctx_label} turn={g_pt.turn_count} "
                   f"nodes={len(g_pt.nodes)} links={total_pt}(active {active_pt})")
    # Compact context via shared helper (no recursive MCP call)
    search_kws_pt: set[str] = {topic_pt} if topic_pt else set()
    if isinstance(extra_kws_pt, list):
        search_kws_pt.update(extra_kws_pt)
    lks, nodes_pt, fallback_pt, inh_pt, _ = \
        _resolve_context(search_kws_pt, 1, g_pt, "")
    parts_pt: list[str] = []
    if lks:
        parts_pt.append("links:" + "|".join(
            f"{lk.source}-[{lk.weight[0]}]->{lk.target}" for lk in lks[:6]
        ))
    if nodes_pt:
        parts_pt.append("nodes:" + ",".join(
            f"{kw}({sc:.0f})" for kw, sc in nodes_pt[:5]
        ))
        # T56: surface the top node's stored FACTS (episodes), newest first —
        # "we decided X because Y", not just "we talked about X".
        _facts = g_pt.recent_episodes(nodes_pt[0][0], 2)
        if _facts:
            parts_pt.append("facts: " + " | ".join(_facts))
    if fallback_pt:
        parts_pt.append("(vector fallback)")
    if inh_pt:
        parts_pt.append(f"(from:{inh_pt})")
    ctx_text_pt = " | ".join(parts_pt) if parts_pt else "no context"
    out_pt = f"{status_line}\n{ctx_text_pt}"
    # Guard-rail: re-teach the loop in-context. Appended AFTER the token budget
    # so the hint is always present and never truncated away (~15 tokens).
    # E3.4: serve the pre-staged "while you were away" stimulus once, if fresh.
    staged = g_pt.take_staged_stimulus()
    staged_line = f"\n🧠 staged: {staged}" if staged else ""
    stim_line = _stimulus_block(g_pt, search_kws_pt)
    # A4: don't stack staged + live stimulus + a long guide line. When any
    # stimulus is present the short tail is enough; the long teaching
    # sentence is reserved for stimulus-free responses.
    if staged_line and stim_line:
        stim_line = ""   # staged is one-shot; the live stimulus returns next turn
    tail = ("\n→ then store_turn(topic, keywords, links)."
            if (staged_line or stim_line) else
            "\n→ next: fold this context into your reply silently, then call "
            "store_turn(topic, keywords, links) to persist the turn.")
    out_pt = out_pt[:char_budget_pt] + staged_line + stim_line + tail
    return [TextContent(type="text", text=out_pt)]


async def _tool_confirm(arguments: dict, ctx: str, g) -> list[TextContent]:
    keywords = [str(k) for k in arguments.get("keywords", [])]
    boost    = min(int(arguments.get("boost", 2)), 5)
    confirmed: list[str] = []
    skipped:   list[str] = []
    for kw in keywords:
        nd = g.get_node(kw)
        if nd:
            nd.salience += boost
            g.mark_node_dirty(nd.keyword)
            confirmed.append(kw)
        else:
            skipped.append(kw)
    if confirmed:
        _g.save(ctx or None)
    return [TextContent(type="text", text=json.dumps({
        "confirmed": confirmed,
        "boost": boost,
        "skipped": skipped,
    }, ensure_ascii=False))]


async def _tool_merge(arguments: dict, ctx: str, g) -> list[TextContent]:
    canonical = g._norm(arguments["canonical"])
    aliases   = [g._norm(a) for a in arguments.get("aliases", [])]
    canon_nd  = g.get_node(canonical)
    if not canon_nd:
        from neuron.models import Node as _Node
        canon_nd = _Node(keyword=canonical, turn=g.turn_count, domain="general",
                         topic="", sentiment="neutral")
        g.add_node(canon_nd)

    merged, missing = [], []
    for alias in aliases:
        alias_nd = g.get_node(alias)
        if not alias_nd:
            missing.append(alias)
            continue
        # Transfer salience
        canon_nd.salience += alias_nd.salience
        # Rewire all links that reference this alias
        for lk in g.links:
            if lk.source == alias:
                lk.source = canonical
            if lk.target == alias:
                lk.target = canonical
        # Remove self-loops
        g.links = [lk for lk in g.links if lk.source != lk.target]
        # Remove alias node
        g.nodes = [nd for nd in g.nodes if nd.keyword != alias]
        g._rebuild_node_map()
        merged.append(alias)

    # Re-dedup links after rewiring
    seen: set[tuple] = set()
    unique_links = []
    for lk in g.links:
        key = (lk.source, lk.target, lk.link_type)
        if key not in seen:
            seen.add(key)
            unique_links.append(lk)
    g.links = unique_links

    if merged:
        # merge rewrites node/link identity in bulk (rewired sources/targets,
        # dropped aliases & self-loops) — per-row key tracking is unreliable
        # here, so force a full reconcile (upsert-all + delete-stale-by-diff).
        g.mark_full_rewrite()
        _g.save(ctx or None)

    return [TextContent(type="text", text=json.dumps({
        "canonical": canonical,
        "merged": merged,
        "missing": missing,
        "canonical_salience": canon_nd.salience,
        "links_total": len(g.links),
    }, ensure_ascii=False))]


_HANDLERS = {
    "help": _tool_help,
    "skill": _tool_skill,
    "status": _tool_status,
    "store_turn": _tool_store_turn,
    "get_context": _tool_get_context,
    "find_candidates": _tool_find_candidates,
    "vector_search": _tool_vector_search,
    "summary": _tool_summary,
    "forgotten": _tool_forgotten,
    "prune": _tool_prune,
    "consolidate": _tool_consolidate,
    "dedup": _tool_dedup,
    "flash": _tool_flash,
    "reset": _tool_reset,
    "export": _tool_export,
    "extract": _tool_extract,
    "auto": _tool_auto,
    "switch_context": _tool_switch_context,
    "list_contexts": _tool_list_contexts,
    "pre_turn": _tool_pre_turn,
    "confirm": _tool_confirm,
    "merge": _tool_merge,
}


async def _call_tool_impl(name: str, arguments: dict) -> list[TextContent]:
    handler = _HANDLERS.get(name)
    if handler is None:
        return [TextContent(type="text", text=f"Unknown command: {name}")]
    ctx = arguments.get("context", "")
    g = _g.get(ctx) if ctx else _g.get()
    return await handler(arguments, ctx, g)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    from neuron import __version__
    # A3 (Piano 05): pre-warm the embedding model in a worker thread so the
    # FIRST pre_turn of the session doesn't pay the ~3s model load. Best-effort:
    # any failure is swallowed (the lazy path in _get_embedder still applies).
    async def _prewarm() -> None:
        try:
            await asyncio.to_thread(_get_embedder)
        except Exception:
            pass
    warm_task = asyncio.ensure_future(_prewarm())
    warm_task.add_done_callback(lambda t: t.exception())  # never "unretrieved"
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            InitializationOptions(
                # A6: the server identity must match the install slug (v5 =
                # "neuron5", v4 = "neuron") — was hardcoded "neuron".
                server_name=_resolve_slug(),
                server_version=__version__,
                # The signpost: injected once at the handshake, present for the
                # whole session on every client that surfaces server instructions.
                instructions=_build_signpost(),
                capabilities=app.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


def cli() -> None:
    """Synchronous entry point for the ``neuron-mcp`` console script.

    ``main()`` is a coroutine, so it cannot be used directly as a
    ``[project.scripts]`` target (the script would get an un-awaited
    coroutine). This wrapper runs the event loop.
    """
    asyncio.run(main())


if __name__ == "__main__":
    cli()
