"""Neuron — Standalone interactive-CLI engine (NOT the production MCP path).

ROLE / SCOPE
------------
This module is the engine behind ``scripts/run_interactive.py`` *only* — a
terminal "playground" that drives a real LLM provider (Ollama, OpenAI,
Anthropic, Gemini, Azure, OpenAI-compatible) for local experimentation and
manual testing of the semantic-memory behaviour.

It is **not** the production code path. The real MCP server that ships to
clients is ``neuron.server`` (``src/neuron/server.py``). The two were written
separately and intentionally **do not** share an implementation: concept
extraction, semantic linking and the "flash" mechanism are reimplemented here
in a self-contained way (this file even defines its own ``Node`` dataclass
instead of reusing ``neuron.models``).

Consequences — read before editing:
  * Do **not** expect functional parity with ``server.py``. A fix or feature
    added to one side is *not* automatically reflected in the other.
  * Behavioural guarantees for "Neuron as an MCP server" refer to
    ``server.py``, not to this engine.
  * This file is versioned independently (historically "v3.1") and may lag the
    server (currently v3.3). The version skew is expected, not a bug.

If you need to change the production behaviour, edit ``server.py``. Touch this
file only for the interactive CLI.

Original design note: every message leaves semantic traces that connect with
each other over time, like the associative memory of the human brain;
connections enrich each response as invisible cognitive substrate.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal, Protocol

from neuron import db as _db


# ---------------------------------------------------------------------------
# Types and data structures
# ---------------------------------------------------------------------------

Intent = Literal["question", "task", "exploration", "clarification", "feedback"]
Sentiment = Literal["neutral", "positive", "critical", "urgent"]
Domain = Literal["AI", "backend", "frontend", "gaming", "architecture", "general"]
LinkType = Literal["cause-effect", "analogy", "evolution", "contrast", "deepening", "instance-of"]
Weight = Literal["strong", "medium", "tangential"]

TANGENTIAL_EXPIRY_TURNS: int = 5
WEIGHT_ORDER: dict[Weight, int] = {"strong": 3, "medium": 2, "tangential": 1}


@dataclass
class Node:
    """Semantic node extracted from a message."""

    keyword: str
    turn: int
    topic: str
    domain: Domain
    sentiment: Sentiment
    salience: int = 0
    entities: list[str] | None = None
    tags: list[str] | None = None
    references: list[dict] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize the node to a dictionary."""
        return {
            "keyword": self.keyword,
            "turn": self.turn,
            "topic": self.topic,
            "domain": self.domain,
            "sentiment": self.sentiment,
            "salience": self.salience,
            "entities": self.entities or [],
            "tags": self.tags or [],
            "references": self.references or [],
        }


@dataclass
class Link:
    """Semantic link between two keywords."""

    source: str
    target: str
    link_type: LinkType
    weight: Weight
    rationale: str
    created_turn: int
    last_active_turn: int
    inactive_turns: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize the link to a dictionary."""
        return {
            "source": self.source,
            "target": self.target,
            "link_type": self.link_type,
            "weight": self.weight,
            "rationale": self.rationale,
            "created_turn": self.created_turn,
            "last_active_turn": self.last_active_turn,
            "inactive_turns": self.inactive_turns,
        }


@dataclass
class Extraction:
    """Result of semantic extraction from the current message."""

    topic: str
    entities: list[str]
    intent: Intent
    sentiment: Sentiment
    domain: Domain
    keywords: list[str]
    tags: list[str] = field(default_factory=list)
    references: list[dict] = field(default_factory=list)


@dataclass
class SemanticNetwork:
    """Semantic network accumulated during the conversation."""

    nodes: list[Node] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    turn_count: int = 0
    session_id: str = ""
    last_sentiment: Sentiment = "neutral"
    last_summary_turn: int = 0
    compressed_summary: str = ""
    pruned_count: int = 0

    def get_active_links(self) -> list[Link]:
        """Return only strong and medium links."""
        return [lk for lk in self.links if lk.weight in ("strong", "medium")]

    def get_nodes_by_domain(self, domain: Domain) -> list[Node]:
        """Return nodes of the specified domain."""
        return [nd for nd in self.nodes if nd.domain == domain]

    def prune_tangential(self) -> int:
        """Remove expired tangential links. Returns the number removed."""
        before = len(self.links)
        self.links = [
            lk for lk in self.links
            if not (lk.weight == "tangential" and lk.inactive_turns > TANGENTIAL_EXPIRY_TURNS)
        ]
        removed = before - len(self.links)
        self.pruned_count += removed
        return removed

    def increment_inactivity(self, active_sources: set[str]) -> None:
        """Increment inactivity for links not touched this turn."""
        for lk in self.links:
            if lk.source in active_sources or lk.target in active_sources:
                lk.inactive_turns = 0
                lk.last_active_turn = self.turn_count
            else:
                lk.inactive_turns += 1

    def top_links_by_salience(self, n: int = 5) -> list[Link]:
        """Return the most salient links sorted by weight and activity."""
        active = self.get_active_links()
        return sorted(
            active,
            key=lambda lk: (WEIGHT_ORDER[lk.weight], -lk.inactive_turns),
            reverse=True,
        )[:n]

    def export(self) -> dict[str, Any]:
        """Export the complete network in JSON-serializable format."""
        return {
            "session_id": self.session_id,
            "turn_count": self.turn_count,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "nodes": [nd.to_dict() for nd in self.nodes],
            "links": [lk.to_dict() for lk in self.links],
        }


# ---------------------------------------------------------------------------
# LLM client protocol
# ---------------------------------------------------------------------------

class LLMClient(Protocol):
    """Common interface for any LLM provider."""

    def complete(self, system: str, user: str, fast: bool = False) -> str:
        """Send a completion request.

        Args:
            system: System prompt.
            user: User message.
            fast: If True, use the lightweight model (M5 Dual Model).

        Returns:
            Response text.
        """
        ...


# ---------------------------------------------------------------------------
# LLM client implementations
# ---------------------------------------------------------------------------

class OllamaClient:
    """Client for local Ollama with Dual Model support."""

    def __init__(self, model: str = "qwen2.5:14b", fast_model: str = "qwen2.5:3b") -> None:
        """Initialize the Ollama client.

        Args:
            model: Main model for responses.
            fast_model: Lightweight model for extraction/linking (M5).
        """
        try:
            import ollama as _ollama
            self._ollama = _ollama
        except ImportError as exc:
            raise ImportError("Installa il pacchetto: pip install ollama") from exc
        self.model = model
        self.fast_model = fast_model

    def complete(self, system: str, user: str, fast: bool = False) -> str:
        """Send request to Ollama."""
        chosen = self.fast_model if fast else self.model
        response = self._ollama.chat(
            model=chosen,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response["message"]["content"]


class OpenAIClient:
    """Client for OpenAI with Dual Model support."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        fast_model: str = "gpt-4o-mini",
        base_url: str | None = None,
    ) -> None:
        """Initialize the OpenAI client.

        Args:
            api_key: OpenAI API key.
            model: Main model.
            fast_model: Lightweight model for extraction (M5).
            base_url: Optional base URL for OpenAI-compatible endpoints.
        """
        try:
            from openai import OpenAI
            self._client = OpenAI(api_key=api_key, base_url=base_url)
        except ImportError as exc:
            raise ImportError("Installa il pacchetto: pip install openai") from exc
        self.model = model
        self.fast_model = fast_model

    def complete(self, system: str, user: str, fast: bool = False) -> str:
        """Send request to OpenAI."""
        chosen = self.fast_model if fast else self.model
        response = self._client.chat.completions.create(
            model=chosen,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content or ""


class AzureOpenAIClient:
    """Client for Azure OpenAI with Dual Model support."""

    def __init__(
        self,
        api_key: str,
        azure_endpoint: str,
        api_version: str = "2024-10-21",
        model: str = "gpt-4o",
        fast_model: str = "gpt-4o-mini",
    ) -> None:
        """Initialize the Azure OpenAI client.

        Args:
            api_key: Azure OpenAI API key.
            azure_endpoint: Azure endpoint (e.g. "https://your-resource.openai.azure.com").
            api_version: Azure API version (default: 2024-10-21).
            model: Deployment name of the main model.
            fast_model: Deployment name of the lightweight extraction model (M5).
        """
        try:
            from openai import AzureOpenAI
            self._client = AzureOpenAI(
                api_key=api_key,
                azure_endpoint=azure_endpoint,
                api_version=api_version,
            )
        except ImportError as exc:
            raise ImportError("Installa il pacchetto: pip install openai") from exc
        self.model = model
        self.fast_model = fast_model

    def complete(self, system: str, user: str, fast: bool = False) -> str:
        """Send request to Azure OpenAI."""
        chosen = self.fast_model if fast else self.model
        response = self._client.chat.completions.create(
            model=chosen,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content or ""


class AnthropicClient:
    """Client for Anthropic Claude with Dual Model support."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-5",
        fast_model: str = "claude-haiku-3-5-20241022",
    ) -> None:
        """Initialize the Anthropic client.

        Args:
            api_key: Anthropic API key.
            model: Main model.
            fast_model: Lightweight model for extraction (M5).
        """
        try:
            import anthropic
            self._client = anthropic.Anthropic(api_key=api_key)
        except ImportError as exc:
            raise ImportError("Installa il pacchetto: pip install anthropic") from exc
        self.model = model
        self.fast_model = fast_model

    def complete(self, system: str, user: str, fast: bool = False) -> str:
        """Send request to Anthropic."""
        chosen = self.fast_model if fast else self.model
        response = self._client.messages.create(
            model=chosen,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return response.content[0].text


class GeminiClient:
    """Client for Google Gemini with Dual Model support."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-pro",
        fast_model: str = "gemini-2.0-flash-lite",
    ) -> None:
        """Initialize the Gemini client.

        Args:
            api_key: Google API key.
            model: Main model.
            fast_model: Lightweight model for extraction (M5).
        """
        try:
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            self._genai = genai
        except ImportError as exc:
            raise ImportError("Installa il pacchetto: pip install google-generativeai") from exc
        self.model = model
        self.fast_model = fast_model

    def complete(self, system: str, user: str, fast: bool = False) -> str:
        """Send request to Gemini."""
        chosen = self.fast_model if fast else self.model
        model_obj = self._genai.GenerativeModel(
            model_name=chosen,
            system_instruction=system,
        )
        response = model_obj.generate_content(user)
        return response.text


# ---------------------------------------------------------------------------
# Neuron engine
# ---------------------------------------------------------------------------

EXTRACTION_SYSTEM = """You are a semantic analyzer. Extract from the user message a JSON with this EXACT structure (no markdown, pure JSON only):
{
  "topic": "main topic in 3-5 words",
  "entities": ["list of relevant entities"],
  "intent": "question|task|exploration|clarification|feedback",
  "sentiment": "neutral|positive|critical|urgent",
  "domain": "AI|backend|frontend|gaming|architecture|general",
  "keywords": ["kw1","kw2","kw3","kw4","kw5"],
  "tags": ["free labels beyond domain"],
  "references": [{"type": "file|url|commit", "path": "path", "description": "notes"}]
}
Keywords must be abstract and generalizable."""

LINKING_SYSTEM = """You are a semantic connection analyzer. Given the current keywords set and those from previous turns,
return ONLY a JSON array of links (can be empty []):
[
  {
    "source": "current_keyword",
    "target": "previous_keyword",
    "link_type": "cause-effect|analogy|evolution|contrast|deepening|instance-of",
    "weight": "strong|medium|tangential",
    "rationale": "explanation in 10-15 words"
  }
]
Create only meaningful links. Prefer quality over quantity."""


class Neuron:
    """Main engine for cumulative cognitive stimulation.

    Manages the full cycle: extraction → linking → injection → response → update.
    """

    def __init__(
        self,
        llm: LLMClient,
        *,
        enable_sentiment: bool = True,
        enable_domain_boost: bool = True,
        periodic_summary_every: int = 0,
        enable_flash_semantics: bool = True,
        deduplicate_keywords: bool = True,
        db_path: str | None = None,
        session_id: str = "",
    ) -> None:
        """Initialize Neuron.

        Args:
            llm: LLM client to use for extraction and response.
            enable_sentiment: Enable M1 (tone tracking).
            enable_domain_boost: Enable M2 (same-domain link promotion).
            periodic_summary_every: Compress the network every N turns (0 = off).
            enable_flash_semantics: Enable M4 (semantic flashes).
            deduplicate_keywords: If True, reuse existing nodes instead of creating new ones.
            db_path: SQLite file path for automatic persistence.
            session_id: Optional session identifier.
        """
        self._llm = llm
        self._enable_sentiment = enable_sentiment
        self._enable_domain_boost = enable_domain_boost
        self._periodic_summary_every = periodic_summary_every
        self._enable_flash = enable_flash_semantics
        self._dedup_keywords = deduplicate_keywords
        self._db_path = db_path
        self._net = SemanticNetwork(
            session_id=session_id or datetime.now().strftime("%Y%m%d%H%M%S")
        )
        self._history: list[dict[str, str]] = []
        if db_path and os.path.exists(db_path):
            self._load_from_sqlite()

    # ------------------------------------------------------------------
    # Interfaccia pubblica
    # ------------------------------------------------------------------

    def chat(self, message: str) -> str:
        """Process a message and return a semantically enriched response.

        Args:
            message: User message.

        Returns:
            Model response with link summary appended.
        """
        message = message.strip()

        # Command handling
        if message.startswith("/neuron "):
            return self._handle_command(message[8:].strip())

        self._net.turn_count += 1
        turn = self._net.turn_count

        # Phase 1: extraction
        extraction = self._extract(message)

        # Phase 2: linking
        new_links = self._link(extraction, turn)
        self._apply_links(new_links, extraction, turn)

        # M3: periodic summary
        if self._periodic_summary_every > 0 and (turn - self._net.last_summary_turn) >= self._periodic_summary_every:
            self._generate_periodic_summary()

        # Phase 3: build thread
        thread = self._build_thread(extraction, turn)

        # Phase 4: response
        response = self._respond(message, thread, extraction)

        # Phase 5: update registry
        self._update_registry(extraction, new_links, turn)
        self._history.append({"role": "user", "content": message})
        self._history.append({"role": "assistant", "content": response})

        # Append link summary to response
        link_summary = self._format_link_summary()
        response = f"{response}\n\n{link_summary}" if link_summary else response

        # Auto persistence
        if self._db_path:
            self._save_to_sqlite()

        return response

    def status(self) -> str:
        """Return the status of the semantic network."""
        nodes_info = "\n".join(
            f"  [{nd.turn}] `{nd.keyword}` — {nd.domain} / {nd.sentiment} (salience: {nd.salience})"
            for nd in self._net.nodes[-20:]
        )
        links_info = "\n".join(
            f"  {'⬤' if lk.weight == 'strong' else '◉' if lk.weight == 'medium' else '○'} "
            f"`{lk.source}` →({lk.link_type})→ `{lk.target}` [{lk.weight}] — {lk.rationale}"
            for lk in self._net.top_links_by_salience(10)
        )

        # Health indicators
        total_active = len(self._net.get_active_links())
        strong_count = sum(1 for lk in self._net.links if lk.weight == "strong")
        medium_count = sum(1 for lk in self._net.links if lk.weight == "medium")
        strong_ratio = ((strong_count + medium_count) / len(self._net.links) * 100) if self._net.links else 0
        type_count = len({lk.link_type for lk in self._net.links})
        pruned_ratio = (self._net.pruned_count / (len(self._net.links) + self._net.pruned_count) * 100) if (len(self._net.links) + self._net.pruned_count) > 0 else 0
        nodes_per_turn = len(self._net.nodes) / max(self._net.turn_count, 1)

        health = (
            f"**Graph health:**\n"
            f"  strong+medium ratio: {strong_ratio:.0f}% {'✅' if strong_ratio > 40 else '⚠️' if strong_ratio > 20 else '❌'}\n"
            f"  Link types: {type_count} {'✅' if type_count >= 3 else '⚠️' if type_count == 2 else '❌'}\n"
            f"  Pruned ratio: {pruned_ratio:.0f}% {'✅' if pruned_ratio < 30 else '⚠️' if pruned_ratio < 50 else '❌'}\n"
            f"  Nodes/turn: {nodes_per_turn:.1f} {'✅' if nodes_per_turn <= 5 else '⚠️' if nodes_per_turn <= 8 else '❌'}"
        )

        return (
            f"**🧠 Neuron Status — turn {self._net.turn_count}**\n\n"
            f"**Active nodes ({len(self._net.nodes)}):**\n{nodes_info or '  (none)'}\n\n"
            f"**Active links ({total_active}):**\n{links_info or '  (none)'}\n\n"
            f"{health}"
        )

    def reset(self) -> str:
        """Reset the network and history."""
        self._net = SemanticNetwork(
            session_id=datetime.now().strftime("%Y%m%d%H%M%S")
        )
        self._history.clear()
        return "Graph and history reset. New session started."

    def prune(self) -> str:
        """Force immediate pruning of tangential links."""
        removed = self._net.prune_tangential()
        return f"Pruning complete. {removed} tangential links removed."

    def summary(self) -> str:
        """Generate a textual summary of the current network."""
        if not self._net.nodes:
            return "The network is still empty."
        top_links = self._net.top_links_by_salience(5)
        lines = [f"**🧠 Network summary — {self._net.turn_count} turns**\n"]
        domains = list({nd.domain for nd in self._net.nodes})
        lines.append(f"Domains touched: {', '.join(domains)}")
        lines.append(f"Total nodes: {len(self._net.nodes)}")
        lines.append(f"Total links: {len(self._net.links)} (active: {len(self._net.get_active_links())})")
        if top_links:
            lines.append("\nMost salient connections:")
            for lk in top_links:
                lines.append(f"  - `{lk.source}` →({lk.link_type})→ `{lk.target}` [{lk.weight}]")
        # Health
        strong_count = sum(1 for lk in self._net.links if lk.weight == "strong")
        medium_count = sum(1 for lk in self._net.links if lk.weight == "medium")
        strong_ratio = ((strong_count + medium_count) / len(self._net.links) * 100) if self._net.links else 0
        type_count = len({lk.link_type for lk in self._net.links})
        pruned_ratio = (self._net.pruned_count / (len(self._net.links) + self._net.pruned_count) * 100) if (len(self._net.links) + self._net.pruned_count) > 0 else 0
        nodes_per_turn = len(self._net.nodes) / max(self._net.turn_count, 1)
        lines.append(f"\n**Health:** strong+medium {strong_ratio:.0f}% | types {type_count} | pruned {pruned_ratio:.0f}% | nodes/turn {nodes_per_turn:.1f}")
        return "\n".join(lines)

    def export_json(self) -> str:
        """Export the complete network as JSON."""
        return json.dumps(self._net.export(), ensure_ascii=False, indent=2)

    def toggle_flash(self) -> str:
        """Toggle Semantic Flashes (M4)."""
        self._enable_flash = not self._enable_flash
        state = "enabled" if self._enable_flash else "disabled"
        return f"Semantic flash {state}."

    def toggle_dedup(self) -> str:
        """Toggle keyword deduplication (M7)."""
        self._dedup_keywords = not self._dedup_keywords
        state = "enabled" if self._dedup_keywords else "disabled"
        return f"Keyword deduplication {state}."

    def save_to_sqlite(self, db_path: str | None = None) -> str:
        """Save the network to SQLite database."""
        path = db_path or self._db_path
        if not path:
            return "No db_path configured."
        self._save_to_sqlite(path)
        return f"✅ Grafo salvato su {path} ({len(self._net.nodes)} nodi, {len(self._net.links)} link)."

# ------------------------------------------------------------------
# SQLite persistence
# ------------------------------------------------------------------

    def _save_to_sqlite(self, db_path: str | None = None) -> None:
        """Save current state to SQLite (create table if not exists)."""
        path = db_path or self._db_path
        if not path:
            return
        conn = _db.connect(path)
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY, value TEXT
                );
                CREATE TABLE IF NOT EXISTS nodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    keyword TEXT, turn INTEGER, topic TEXT,
                    domain TEXT, sentiment TEXT, salience INTEGER,
                    entities TEXT DEFAULT '[]',
                    tags TEXT DEFAULT '[]',
                    refs TEXT DEFAULT '[]'
                );
                CREATE TABLE IF NOT EXISTS links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT, target TEXT, link_type TEXT, weight TEXT,
                    rationale TEXT, created_turn INTEGER,
                    last_active_turn INTEGER, inactive_turns INTEGER
                );
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT, content TEXT, turn INTEGER
                );
            """)
            # Column migration for v3.0 upgrade
            try:
                conn.execute("ALTER TABLE nodes ADD COLUMN entities TEXT DEFAULT '[]'")
            except Exception:
                pass
            try:
                conn.execute("ALTER TABLE nodes ADD COLUMN tags TEXT DEFAULT '[]'")
            except Exception:
                pass
            try:
                conn.execute("ALTER TABLE nodes ADD COLUMN refs TEXT DEFAULT '[]'")
            except Exception:
                pass
            # Session: clear and rewrite
            conn.execute("DELETE FROM meta")
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                ("session_id", self._net.session_id))
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                ("turn_count", str(self._net.turn_count)))
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                ("last_sentiment", self._net.last_sentiment))
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                ("compressed_summary", self._net.compressed_summary or ""))
            # Nodes
            conn.execute("DELETE FROM nodes")
            for nd in self._net.nodes:
                conn.execute(
                    "INSERT INTO nodes (keyword, turn, topic, domain, sentiment, salience, entities, tags, refs) VALUES (?,?,?,?,?,?,?,?,?)",
                    (nd.keyword, nd.turn, nd.topic, nd.domain, nd.sentiment, nd.salience,
                     json.dumps(getattr(nd, 'entities', []) or []),
                     json.dumps(getattr(nd, 'tags', []) or []),
                     json.dumps(getattr(nd, 'references', []) or [])))
            # Links
            conn.execute("DELETE FROM links")
            for lk in self._net.links:
                conn.execute(
                    "INSERT INTO links (source, target, link_type, weight, rationale, created_turn, last_active_turn, inactive_turns) VALUES (?,?,?,?,?,?,?,?)",
                    (lk.source, lk.target, lk.link_type, lk.weight, lk.rationale,
                     lk.created_turn, lk.last_active_turn, lk.inactive_turns))
            # History
            conn.execute("DELETE FROM history")
            for i, m in enumerate(self._history, 1):
                conn.execute("INSERT INTO history (role, content, turn) VALUES (?,?,?)",
                    (m["role"], m["content"], i))
            conn.commit()
        finally:
            conn.close()

    def _load_from_sqlite(self, db_path: str | None = None) -> None:
        """Load state from SQLite."""
        path = db_path or self._db_path
        if not path or not os.path.exists(path):
            return
        conn = _db.connect(path)
        try:
            # Meta
            cursor = conn.execute("SELECT key, value FROM meta")
            meta = dict(cursor.fetchall())
            self._net.session_id = meta.get("session_id", self._net.session_id)
            self._net.turn_count = int(meta.get("turn_count", "0"))
            self._net.last_sentiment = meta.get("last_sentiment", "neutral")  # type: ignore
            self._net.compressed_summary = meta.get("compressed_summary", "")
            # Nodes
            self._net.nodes.clear()
            cols = [c[1] for c in conn.execute("PRAGMA table_info(nodes)").fetchall()]
            has_extra = "entities" in cols
            for row in conn.execute("SELECT keyword, turn, topic, domain, sentiment, salience, entities, tags, refs FROM nodes ORDER BY id"):
                nd = Node(keyword=row[0], turn=row[1], topic=row[2],
                          domain=row[3], sentiment=row[4], salience=row[5])
                if has_extra and row[6]:
                    nd.entities = json.loads(row[6]) if isinstance(row[6], str) else row[6]
                    nd.tags = json.loads(row[7]) if isinstance(row[7], str) else row[7]
                    nd.references = json.loads(row[8]) if isinstance(row[8], str) else row[8]
                self._net.nodes.append(nd)
            # Links
            self._net.links.clear()
            for row in conn.execute(
                "SELECT source, target, link_type, weight, rationale, created_turn, last_active_turn, inactive_turns FROM links ORDER BY id"):
                lk = Link(source=row[0], target=row[1], link_type=row[2],
                          weight=row[3], rationale=row[4],
                          created_turn=row[5], last_active_turn=row[6],
                          inactive_turns=row[7])
                self._net.links.append(lk)
            # History
            self._history.clear()
            for row in conn.execute("SELECT role, content FROM history ORDER BY id"):
                self._history.append({"role": row[0], "content": row[1]})
        finally:
            conn.close()

# ------------------------------------------------------------------
# Internal phases
# ------------------------------------------------------------------

    def _extract(self, message: str) -> Extraction:
        """Phase 1: semantic extraction from the current message."""
        raw = self._llm.complete(EXTRACTION_SYSTEM, message, fast=True)
        try:
            data = json.loads(self._clean_json(raw))
            return Extraction(
                topic=data.get("topic", "sconosciuto"),
                entities=data.get("entities", []),
                intent=data.get("intent", "question"),
                sentiment=data.get("sentiment", "neutral"),
                domain=data.get("domain", "general"),
                keywords=data.get("keywords", []),
                tags=data.get("tags", []),
                references=data.get("references", []),
            )
        except (json.JSONDecodeError, KeyError):
            return Extraction(
                topic="sconosciuto",
                entities=[],
                intent="question",
                sentiment="neutral",
                domain="general",
                keywords=[message[:30]],
            )

    def _link(self, extraction: Extraction, turn: int) -> list[Link]:
        """Phase 2: linking with previous turns."""
        if not self._net.nodes:
            return []

        prev_keywords = [
            f"turn {nd.turn}: {nd.keyword} (domain: {nd.domain}, topic: {nd.topic})"
            for nd in self._net.nodes[-30:]
        ]
        user_msg = (
            f"Current keywords (turn {turn}): {extraction.keywords}\n\n"
            f"Previous keywords:\n" + "\n".join(prev_keywords)
        )
        raw = self._llm.complete(LINKING_SYSTEM, user_msg, fast=True)
        try:
            items = json.loads(self._clean_json(raw))
            if not isinstance(items, list):
                return []
            links = []
            for item in items:
                lk = Link(
                    source=item["source"],
                    target=item["target"],
                    link_type=item["link_type"],
                    weight=item["weight"],
                    rationale=item["rationale"],
                    created_turn=turn,
                    last_active_turn=turn,
                )
                # M2: Domain Boost
                if self._enable_domain_boost:
                    src_node = next((nd for nd in self._net.nodes if nd.keyword == lk.source), None)
                    tgt_node = next((nd for nd in self._net.nodes if nd.keyword == lk.target), None)
                    if (
                        src_node
                        and tgt_node
                        and src_node.domain == tgt_node.domain
                        and lk.weight == "tangential"
                    ):
                        lk.weight = "medium"
                links.append(lk)
            return links
        except (json.JSONDecodeError, KeyError):
            return []

    def _apply_links(self, new_links: list[Link], extraction: Extraction, turn: int) -> None:
        """Apply new links to the network and update salience."""
        # Link diversity: no type >50% of new links
        if new_links:
            type_counts: dict[str, int] = {}
            for lk in new_links:
                type_counts[lk.link_type] = type_counts.get(lk.link_type, 0) + 1
            threshold = len(new_links) // 2
            for lk in new_links[:]:  # copy per iterare
                if type_counts.get(lk.link_type, 0) > threshold:
                    # Demote excess instance links
                    if lk.link_type == "instance-of":
                        lk.link_type = "deepening"
                        type_counts["instance-of"] = type_counts.get("instance-of", 0) - 1
                        type_counts["deepening"] = type_counts.get("deepening", 0) + 1
                    # Skip if still above threshold
                    if type_counts.get(lk.link_type, 0) > threshold:
                        new_links.remove(lk)
                        type_counts[lk.link_type] -= 1

        active_sources = set(extraction.keywords)
        for lk in new_links:
            self._net.links.append(lk)
            # Update salience of involved nodes
            for nd in self._net.nodes:
                if nd.keyword in (lk.source, lk.target):
                    nd.salience += WEIGHT_ORDER[lk.weight]
        self._net.increment_inactivity(active_sources)

    def _build_thread(self, extraction: Extraction, turn: int) -> str:
        """Phase 3: build the invisible thread."""
        top = self._net.top_links_by_salience(5)
        lines = [f"Current topic is '{extraction.topic}' (domain: {extraction.domain})."]

        if top:
            lines.append("Relevant active connections:")
            for lk in top:
                lines.append(f"  - '{lk.source}' is a {lk.link_type} of '{lk.target}' (turn {lk.created_turn}): {lk.rationale}")

        # M4: Semantic Flashes
        if self._enable_flash:
            flash = [
                lk for lk in self._net.links
                if lk.weight == "strong" and (turn - lk.created_turn) > 3
            ]
            if flash:
                lk = flash[0]
                lines.append(
                    f"\nSemantic flash: this topic connects back to turn {lk.created_turn} "
                    f"regarding '{lk.target}'. Keep it in mind without forcing it."
                )

        # M1: Sentiment shift
        if self._enable_sentiment and extraction.sentiment != self._net.last_sentiment:
            if extraction.sentiment == "urgent":
                lines.append("\nThe tone became urgent: adapt the response register accordingly.")

        if self._net.compressed_summary:
            lines.append(f"\nCompressed context: {self._net.compressed_summary}")

        lines.append("\nTaking these connections into account, reasoning must be coherent with accumulated history.")
        return "\n".join(lines)

    def _respond(self, message: str, thread: str, extraction: Extraction) -> str:
        """Generate the response enriched by the thread."""
        system = (
            "You are an AI assistant with cumulative cognitive memory. "
            "The following cognitive substrate guides your reasoning (do not show to the user):\n\n"
            f"{thread}\n\n"
            "Respond naturally, coherent with conversation history. "
            "Reference connected concepts implicitly without forcing them."
        )
        history_ctx = "\n".join(
            f"{m['role'].upper()}: {m['content']}"
            for m in self._history[-8:]
        )
        user_ctx = f"{history_ctx}\nUSER: {message}" if history_ctx else message
        return self._llm.complete(system, user_ctx, fast=False)

    def _update_registry(self, extraction: Extraction, new_links: list[Link], turn: int) -> None:
        """Phase 5: update the registry with new nodes and prune."""
        existing_keywords = {nd.keyword for nd in self._net.nodes}
        for kw in extraction.keywords:
            if self._dedup_keywords and kw in existing_keywords:
                # Update salience of existing node
                for nd in self._net.nodes:
                    if nd.keyword == kw:
                        nd.salience += 1
                        nd.turn = turn
                        break
            else:
                self._net.nodes.append(Node(
                    keyword=kw,
                    turn=turn,
                    topic=extraction.topic,
                    domain=extraction.domain,
                    sentiment=extraction.sentiment,
                    entities=extraction.entities,
                    tags=extraction.tags,
                    references=extraction.references,
                ))
                existing_keywords.add(kw)
        self._net.last_sentiment = extraction.sentiment
        self._net.prune_tangential()

    def _generate_periodic_summary(self) -> None:
        """M3: generate and compress a network summary every N turns."""
        top_links = self._net.top_links_by_salience(8)
        if not top_links:
            return
        summary_input = "Summarize in 2-3 sentences the thread of this conversation based on the links:\n"
        for lk in top_links:
            summary_input += f"- '{lk.source}' →({lk.link_type})→ '{lk.target}': {lk.rationale}\n"
        self._net.compressed_summary = self._llm.complete(
            "Sei un sintetizzatore di contesto. Sii conciso e preciso.",
            summary_input,
            fast=True,
        )
        self._net.last_summary_turn = self._net.turn_count

    def _format_link_summary(self) -> str:
        """Format the link summary to append at the end of the response."""
        top = self._net.top_links_by_salience(4)
        if not top:
            return ""
        parts = []
        for lk in top:
            icon = "⬤" if lk.weight == "strong" else "◉"
            parts.append(f"{icon} `{lk.source}` →({lk.link_type})→ `{lk.target}` [{lk.weight}]")
        return "> 🧠 Link: " + " | ".join(parts)

    def _handle_command(self, cmd: str) -> str:
        """Handle /ns commands."""
        dispatch: dict[str, Any] = {
            "status": self.status,
            "reset": self.reset,
            "prune": self.prune,
            "summary": self.summary,
            "export": self.export_json,
            "flash": self.toggle_flash,
            "dedup": self.toggle_dedup,
            "save": lambda: self.save_to_sqlite(),
        }
        handler = dispatch.get(cmd)
        if handler:
            return handler()
        return f"❌ Comando sconosciuto: `/neuron {cmd}`. Comandi disponibili: {', '.join(dispatch.keys())}"

    @staticmethod
    def _clean_json(raw: str) -> str:
        """Remove markdown code fence around JSON if present."""
        raw = raw.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        return raw.strip()


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------

def create_local(
    model: str = "qwen2.5:14b",
    fast_model: str = "qwen2.5:3b",
    **kwargs: Any,
) -> Neuron:
    """Create a Neuron instance with local Ollama.

    Args:
        model: Main Ollama model.
        fast_model: Lightweight model for extraction (M5).
        **kwargs: Extra arguments passed to Neuron.
    """
    return Neuron(OllamaClient(model=model, fast_model=fast_model), **kwargs)


def create_openai(
    api_key: str,
    model: str = "gpt-4o",
    fast_model: str = "gpt-4o-mini",
    **kwargs: Any,
) -> Neuron:
    """Create a Neuron instance with OpenAI.

    Args:
        api_key: OpenAI API key.
        model: Main model.
        fast_model: Lightweight model for extraction (M5).
        **kwargs: Extra arguments passed to Neuron.
    """
    return Neuron(OpenAIClient(api_key=api_key, model=model, fast_model=fast_model), **kwargs)


def create_anthropic(
    api_key: str,
    model: str = "claude-sonnet-4-5",
    fast_model: str = "claude-haiku-3-5-20241022",
    **kwargs: Any,
) -> Neuron:
    """Create a Neuron instance with Anthropic.

    Args:
        api_key: Anthropic API key.
        model: Main model.
        fast_model: Lightweight model for extraction (M5).
        **kwargs: Extra arguments passed to Neuron.
    """
    return Neuron(AnthropicClient(api_key=api_key, model=model, fast_model=fast_model), **kwargs)


def create_gemini(
    api_key: str,
    model: str = "gemini-2.5-pro",
    fast_model: str = "gemini-2.0-flash-lite",
    **kwargs: Any,
) -> Neuron:
    """Create a Neuron instance with Google Gemini.

    Args:
        api_key: Google API key.
        model: Main model.
        fast_model: Lightweight model for extraction (M5).
        **kwargs: Extra arguments passed to Neuron.
    """
    return Neuron(GeminiClient(api_key=api_key, model=model, fast_model=fast_model), **kwargs)


def create_compatible(
    base_url: str,
    model: str,
    api_key: str = "not-needed",
    fast_model: str | None = None,
    **kwargs: Any,
) -> Neuron:
    """Create a Neuron instance with any OpenAI-compatible endpoint.

    Works with LM Studio, Groq, vLLM, LiteLLM, Perplexity, etc.

    Args:
        base_url: Base URL of the endpoint (e.g. "http://localhost:1234/v1").
        model: Main model.
        api_key: API key (often not required for local endpoints).
        fast_model: Lightweight model for extraction. If None, uses the same model.
        **kwargs: Extra arguments passed to Neuron.
    """
    return Neuron(
        OpenAIClient(
            api_key=api_key,
            model=model,
            fast_model=fast_model or model,
            base_url=base_url,
        ),
        **kwargs,
    )


def create_azure(
    api_key: str,
    azure_endpoint: str,
    api_version: str = "2024-10-21",
    model: str = "gpt-4o",
    fast_model: str = "gpt-4o-mini",
    **kwargs: Any,
) -> Neuron:
    """Create a Neuron instance with Azure OpenAI.

    Args:
        api_key: Azure OpenAI API key.
        azure_endpoint: Azure endpoint (e.g. "https://your-resource.openai.azure.com").
        api_version: Azure API version (default: 2024-10-21).
        model: Deployment name of the main model.
        fast_model: Deployment name of the lightweight extraction model (M5).
        **kwargs: Extra arguments passed to Neuron.
    """
    return Neuron(
        AzureOpenAIClient(
            api_key=api_key,
            azure_endpoint=azure_endpoint,
            api_version=api_version,
            model=model,
            fast_model=fast_model,
        ),
        **kwargs,
    )


# NOTE: factory helpers above (create_local/openai/anthropic/gemini/azure/compatible)
# are the public surface of this CLI-only engine, consumed by
# scripts/run_interactive.py. They have no equivalent in neuron.server (the MCP
# production path) and are intentionally not kept in parity with it.
