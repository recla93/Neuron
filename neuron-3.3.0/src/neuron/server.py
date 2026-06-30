"""Neuron v3.3 — MCP Server with Turso (local pyturso engine or cloud) + native vector search.

Database: see neuron.db — local pyturso engine by default, or real Turso cloud
(libsql-client) when TURSO_DATABASE_URL/TURSO_AUTH_TOKEN are set.
Embedding: 384-dim semantic (fastembed, mandatory).
Search: Turso SQL (vector_distance_cos) or Python fallback.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from typing import Any

import sqlite3

from fastembed import TextEmbedding

from neuron import db as _db
TURSO_ENGINE = _db.LOCAL_TURSO_ENGINE

from mcp.server import Server
from mcp.server.lowlevel import NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, ServerCapabilities, ToolsCapability

# ---------------------------------------------------------------------------
# Imports from models (breaks circular import with registry.py)
# ---------------------------------------------------------------------------

from neuron.models import (
    Node, Link, Graph,
    Weight, LinkType, Domain, Sentiment, Intent,
    WEIGHT_ORDER, TANGENTIAL_EXPIRY_TURNS,
    SALIENCE_DECAY_THRESHOLD, SALIENCE_DECAY_AMOUNT,
    VECTOR_DIM, pack_vector, unpack_vector, register_embed_fn,
)

# ---------------------------------------------------------------------------
# Server-level constants
# ---------------------------------------------------------------------------

INTENT_SALIENCE = {"exploration": 3, "task": 3, "clarification": 2, "question": 1, "feedback": 0}
GRAPHS_DIR = os.environ.get(
    "NS_GRAPHS_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "graphs"),
)

_g: "GraphRegistry" = None  # initialized after GraphRegistry import

KEYWORD_MAX_LENGTH = 40
TOPIC_MAX_LENGTH = 100
RATIONALE_MAX_LENGTH = 200
KEYWORD_PATTERN = re.compile(r"^[a-zA-Z0-9\s\-_.:+/]+$")



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
# Automatic semantic extraction from text
# ---------------------------------------------------------------------------


STOP_WORDS: set[str] = {
    # Italian
    "il", "lo", "la", "i", "gli", "le", "un", "uno", "una", "del", "dello",
    "della", "dei", "degli", "delle", "al", "allo", "alla", "ai", "agli",
    "alle", "dal", "dallo", "dalla", "dai", "dagli", "dalle", "nel", "nello",
    "nella", "nei", "negli", "nelle", "con", "su", "per", "tra", "fra",
    "che", "chi", "come", "dove", "quando", "quanto", "quale", "quali",
    "questo", "questa", "questi", "queste", "quello", "quella", "quelli",
    "quelle", "cosa", "cose", "fare", "fatto", "detto", "detta", "detto",
    "piu", "meno", "molto", "troppo", "tanto", "poco", "alcuni", "alcune",
    "ogni", "tutti", "tutte", "ente", "essere", "avere", "venire", "andare",
    "volere", "potere", "dovere", "sapere", "vedere", "dire", "parlare",
    "stato", "stati", "stessa", "stesso", "stesse", "stessi", "mia", "mio",
    "miei", "mie", "tuo", "tuoi", "tua", "tue", "suo", "suoi", "sua", "sue",
    "nostro", "nostra", "nostri", "nostre", "vostro", "vostra", "vostri",
    "vostre", "loro", "cui", "non", "si", "ci", "vi", "mi", "ti", "lo",
    "la", "li", "le", "ne", "ho", "hai", "ha", "hanno", "ho", "hai", "ha",
    "abbiamo", "avete", "hanno", "era", "erano", "sono", "sei", "siamo",
    "siete", "e", "ed", "o", "ma", "se", "no", "grazie", "ok", "okay",
    "si", "no", "forse", "anche", "ancora", "gia", "gia", "solo", "sempre",
    "mai", "qui", "qua", "li", "la", "ora", "adesso", "poi", "dopo", "prima",
    "allora", "mentre", "intanto", "fino", "oltre", "sopra", "sotto",
    "sto", "stai", "sta", "stiamo", "state", "stanno",
    "devo", "devi", "deve", "dobbiamo", "dovete", "devono",
    "posso", "puoi", "puo", "possiamo", "potete", "possono",
    "voglio", "vuoi", "vuole", "vogliamo", "volete", "vogliono",
    "faccio", "fai", "fa", "facciamo", "fate", "fanno",
    # English
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "by", "with", "from", "as", "is", "it", "its", "it", "be", "was",
    "are", "were", "been", "being", "have", "has", "had", "do", "does",
    "did", "will", "would", "could", "should", "may", "might", "can",
    "shall", "about", "into", "through", "during", "before", "after",
    "above", "below", "between", "such", "each", "all", "both", "few",
    "more", "most", "some", "any", "no", "not", "only", "own", "same",
    "so", "than", "too", "very", "just", "because", "if", "then", "else",
    "when", "where", "why", "how", "which", "who", "whom", "what", "this",
    "that", "these", "those", "there", "here", "please", "yes", "no",
    "also", "already", "still", "yet", "now", "then", "well", "rather",
    "quite", "really", "actually", "basically", "essentially",
    "thanks", "thank", "hello", "hi", "hey", "ok", "okay",
    "can", "cant", "could", "would", "should", "must", "shall",
    "want", "need", "like", "make", "made", "use", "used", "using",
    "get", "got", "gets", "getting", "see", "seen", "know", "known",
}


DOMAIN_KEYWORDS: dict[str, set[str]] = {
    "AI": {"artificiale", "machine", "learning", "deep", "neural",
           "network", "model", "training", "inference", "gpt",
           "transformer", "attention", "llm", "rag", "vector",
           "embedding", "token", "dataset", "classification",
           "regressione", "clustering", "vision", "nlp", "processing",
           "language", "natural", "prediction", "predictive", "intelligence"},
    "backend": {"server", "api", "rest", "database", "sql", "nosql", "query",
                 "orm", "java", "spring", "boot", "django", "flask", "fastapi",
                 "microservices", "endpoint", "middleware", "cache", "redis",
                 "postgresql", "mysql", "mongodb", "auth", "authentication",
                 "authorization", "jwt", "oauth", "crud", "service",
                 "repository", "controller", "dto", "entity", "deploy",
                 "produced", "bug", "log", "debug", "deploy"},
    "frontend": {"angular", "react", "vue", "svelte", "component", "ui",
                  "ux", "css", "html", "javascript", "typescript", "dom",
                  "page", "web", "browser", "responsive", "mobile",
                  "interface", "user", "frontend", "redux", "router",
                  "template", "binding", "render"},
    "gaming": {"game", "unity", "unreal", "godot", "3d", "2d",
                "sprite", "asset", "physics", "collision",
                "animation", "shader", "mesh", "texture", "gameplay",
                "level", "npc", "player", "spawn",
                "score"},
    "architecture": {"architecture", "design", "pattern", "solid", "clean",
                      "domain", "driven", "ddd", "microservices", "monolith",
                      "hexagonal", "onion", "cdc", "component", "module",
                      "dependency", "injection", "coupling", "cohesion",
                      "scalability", "maintainability", "refactoring",
                      "abstraction", "interface", "event", "cqrs"},
}

DOMAIN_ALIASES: dict[str, str] = {
    "be": "backend",
    "back-end": "backend",
    "backends": "backend",
    "fe": "frontend",
    "front-end": "frontend",
    "frontends": "frontend",
    "ml": "AI",
    "ai/ml": "AI",
    "deep-learning": "AI",
    "gamedev": "gaming",
    "game-dev": "gaming",
    "game-development": "gaming",
    "arch": "architecture",
    "sw-arch": "architecture",
    "software-arch": "architecture",
    "infra": "backend",
    "infrastructure": "backend",
    "devops": "backend",
    "data-science": "AI",
    "ds": "AI",
    "mobile": "frontend",
    "web": "frontend",
    "web-dev": "frontend",
}


INTENT_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\?+\s*$", re.IGNORECASE), "question"),
    (re.compile(r"^(what|how|why|when|where|who|which)", re.IGNORECASE), "question"),
    (re.compile(r"(explain|describe|illustrate|define|clarify)", re.IGNORECASE), "question"),
    (re.compile(r"(make|create|i want|i need|can you|could you)", re.IGNORECASE), "task"),
    (re.compile(r"(create|write|modify|add|remove|delete|implement)", re.IGNORECASE), "task"),
    (re.compile(r"(test|verify|check|validate|build|deploy)", re.IGNORECASE), "task"),
    (re.compile(r"(ok|thanks|thank you|perfect|clear|understood)", re.IGNORECASE), "feedback"),
    (re.compile(r"(feedback|opinion|advice|suggestion)", re.IGNORECASE), "feedback"),
    (re.compile(r"(what is|tell me|learn|understand|know about)", re.IGNORECASE), "exploration"),
    (re.compile(r"(explore|deep dive|analyze|compare|investigate)", re.IGNORECASE), "exploration"),
]

SENTIMENT_POSITIVE: set[str] = {"ok", "okay", "yes", "great",
    "excellent", "amazing", "good", "nice", "cool", "perfect", "thanks",
    "works", "solved", "useful", "clear", "optimal", "satisfied",
    "interesting", "promising", "awesome", "wonderful", "fantastic",
    "beautiful"}

SENTIMENT_NEGATIVE: set[str] = {
    "critical", "unclear", "confusing", "wrong", "bad", "terrible", "broken",
    "useless", "ambiguous", "not working", "unsatisfied", "slow",
    "complicated", "incorrect", "defective", "bug", "error", "failed"}

SENTIMENT_URGENT: set[str] = {"urgent", "critical", "stalled", "crash",
    "help", "immediate", "emergency", "blocking", "down", "production",
    "deadline", "asap", "hotfix"}


@dataclass
class ExtractionResult:
    topic: str
    keywords: list[str]
    entities: list[str]
    domain: str
    intent: str
    sentiment: str
    tags: list[str]


ENTITY_EXCLUDE: set[str] = {
    "the", "this", "that", "these", "those", "what",
    "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would",
    "can", "could", "shall", "should", "may", "might", "must",
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her",
    "us", "them", "my", "your", "his", "its", "our", "their",
    "in", "on", "at", "to", "for", "with", "by", "from", "of", "about",
    "into", "through", "during", "before", "after", "between",
    "and", "but", "or", "nor", "not", "if", "then", "else",
    "very", "too", "also", "just", "only", "here", "there",
}


class SemanticExtractor:
    """Heuristic semantic extractor from raw text.
    
    Uses lexical analysis, pattern matching, and known domains.
    Does not require LLM.
    """

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        text = text.strip()
        tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9]*(?:[_.:+#/-][a-zA-Z0-9]+)*", text)
        return tokens

    @staticmethod
    def _score_tokens(tokens: list[str]) -> list[tuple[str, float]]:
        counts: dict[str, float] = {}
        for i, t in enumerate(tokens):
            low = t.lower()
            if len(t) <= 2 or low in STOP_WORDS:
                continue
            score = 1.0
            if t[0].isupper():
                score += 0.5
            if any(c.isdigit() for c in t):
                score += 0.3
            if "_" in t or "-" in t or ":" in t:
                score += 0.3
            if len(t) >= 8:
                score += 0.2
            position_boost = 1.0 - (i / max(len(tokens), 1)) * 0.3
            score *= position_boost
            counts[low] = counts.get(low, 0) + score
        for low in list(counts):
            for j in range(len(tokens) - 1):
                if tokens[j].lower() == low or tokens[j + 1].lower() == low:
                    bigram = f"{tokens[j].lower()} {tokens[j + 1].lower()}"
                    if bigram not in counts:
                        counts[low] += 0.15
        ranked = sorted(counts.items(), key=lambda x: -x[1])
        return ranked

    @staticmethod
    def _extract_entities(tokens: list[str], scored: list[tuple[str, float]]) -> list[str]:
        entities: list[str] = []
        added = set()
        case_map: dict[str, str] = {}
        for t in tokens:
            low = t.lower()
            if low not in case_map or (t[0].isupper() and not case_map[low][0].isupper()):
                case_map[low] = t
        for low, _ in scored[:8]:
            if low in added or low in STOP_WORDS or low in ENTITY_EXCLUDE or len(low) < 3:
                continue
            orig = case_map.get(low, low)
            if orig[0].isupper() and len(orig) > 2:
                entities.append(orig)
                added.add(low)
        for i in range(len(tokens) - 1):
            a, b = tokens[i], tokens[i + 1]
            if len(a) > 1 and len(b) > 1 and a[0].isupper() and b[0].isupper():
                low_a, low_b = a.lower(), b.lower()
                if low_a in STOP_WORDS or low_b in STOP_WORDS:
                    continue
                if low_a in ENTITY_EXCLUDE or low_b in ENTITY_EXCLUDE:
                    continue
                bigram = f"{a} {b}"
                low_bg = bigram.lower()
                if low_bg not in added:
                    entities.append(bigram)
                    added.add(low_bg)
        return entities[:8]

    @staticmethod
    def _detect_domain(tokens: list[str], scored: list[tuple[str, float]]) -> str:
        domain_scores: dict[str, float] = {d: 0.0 for d in DOMAIN_KEYWORDS}
        for t in tokens:
            lower = t.lower()
            for domain, kws in DOMAIN_KEYWORDS.items():
                if lower in kws:
                    domain_scores[domain] += 2.0
        for t, score in scored[:5]:
            lower = t.lower()
            for domain, kws in DOMAIN_KEYWORDS.items():
                if lower in kws:
                    domain_scores[domain] += score * 2
        best = max(domain_scores, key=domain_scores.get)
        return best if domain_scores[best] > 0 else "general"

    @staticmethod
    def _detect_intent(text: str) -> str:
        text_lower = text.lower().strip()
        for pattern, intent in INTENT_PATTERNS:
            if pattern.search(text_lower):
                return intent
        return "question"

    @staticmethod
    def _detect_sentiment(text: str, tokens: list[str]) -> str:
        text_lower = text.lower()
        if any(w in text_lower for w in SENTIMENT_URGENT):
            return "urgent"
        tokens_lower = [t.lower() for t in tokens]
        pos_score = sum(1 for t in tokens_lower if t in SENTIMENT_POSITIVE)
        neg_score = sum(1 for t in tokens_lower if t in SENTIMENT_NEGATIVE)
        if pos_score > neg_score:
            return "positive"
        if neg_score > pos_score:
            return "critical"
        return "neutral"

    @staticmethod
    def _build_topic(scored: list[tuple[str, float]]) -> str:
        top = [t for t, _ in scored[:5]]
        if not top:
            return "conversazione"
        topic = " ".join(top[:4])
        return topic[:TOPIC_MAX_LENGTH]

    @staticmethod
    def extract(text: str) -> ExtractionResult:
        tokens = SemanticExtractor._tokenize(text)
        scored = SemanticExtractor._score_tokens(tokens)
        keywords = [t for t, _ in scored[:6]]
        if not keywords:
            keywords = [text[:KEYWORD_MAX_LENGTH].strip() or "conversazione"]
        entities = SemanticExtractor._extract_entities(tokens, scored)
        # ponytail: fold compound entities (bigrams) into keywords so "Kotlin Flow" becomes a node
        for ent in entities[:4]:
            if " " in ent:
                low = ent.lower()
                if low not in keywords:
                    keywords.append(low)
                    if len(keywords) >= 8:
                        break
        # Promote entity bigrams to keywords (fix fragmentation: "Kotlin Flow" stays whole)
        kw_set = set(keywords)
        for ent in entities:
            ent_low = ent.lower()
            if (len(ent.split()) > 1             # bigram or longer
                    and ent_low not in kw_set
                    and len(ent) <= KEYWORD_MAX_LENGTH
                    and KEYWORD_PATTERN.match(ent)):
                keywords.append(ent_low)
                kw_set.add(ent_low)
                if len(keywords) >= 8:
                    break
        domain = SemanticExtractor._detect_domain(tokens, scored)
        intent = SemanticExtractor._detect_intent(text)
        sentiment = SemanticExtractor._detect_sentiment(text, tokens)
        topic = SemanticExtractor._build_topic(scored)
        tags = [domain]
        if intent:
            tags.append(intent)
        return ExtractionResult(
            topic=topic,
            keywords=keywords,
            entities=entities,
            domain=domain,
            intent=intent,
            sentiment=sentiment,
            tags=tags,
        )


# ---------------------------------------------------------------------------
# LLM extraction (Ollama/OpenAI-compatible)
# ---------------------------------------------------------------------------

EXTRACTION_SYSTEM = """You are a semantic analyzer. Extract from the user message a JSON with this EXACT structure (no markdown, pure JSON only):
{
  "topic": "main topic in 3-5 words",
  "entities": ["list of relevant entities"],
  "intent": "question|task|exploration|clarification|feedback",
  "sentiment": "neutral|positive|critical|urgent",
  "domain": "AI|backend|frontend|gaming|architecture|general",
  "keywords": ["kw1","kw2","kw3","kw4","kw5"],
  "tags": ["free labels beyond the domain"]
}
Keywords must be abstract, generalizable, and in English. Capture the weight and importance of concepts in context."""

NS_LLM_ENDPOINT = os.environ.get("NS_LLM_ENDPOINT", "http://localhost:11434/api/generate")
NS_LLM_MODEL = os.environ.get("NS_LLM_MODEL", "qwen2.5:3b")
NS_LLM_API_KEY = os.environ.get("NS_LLM_API_KEY", "")


def _llm_extract(text: str) -> dict | None:
    """Extract semantic JSON via Ollama/OpenAI-compatible API."""
    if not text.strip():
        return None
    try:
        import urllib.request
        import urllib.parse

        payload = json.dumps({
            "model": NS_LLM_MODEL,
            "system": EXTRACTION_SYSTEM,
            "prompt": text,
            "stream": False,
            "format": "json",
        }).encode("utf-8")

        headers = {"Content-Type": "application/json"}
        if NS_LLM_API_KEY:
            headers["Authorization"] = f"Bearer {NS_LLM_API_KEY}"

        req = urllib.request.Request(
            NS_LLM_ENDPOINT,
            data=payload,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))

        raw = body.get("response", "")
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw.strip())
        data = json.loads(raw)
        # Minimum field validation
        if not data.get("keywords"):
            return None
        return {
            "topic": str(data.get("topic", ""))[:TOPIC_MAX_LENGTH],
            "keywords": [str(k)[:KEYWORD_MAX_LENGTH] for k in data.get("keywords", [])][:8],
            "entities": [str(e) for e in data.get("entities", [])][:15],
            "domain": str(data.get("domain", "general")),
            "intent": str(data.get("intent", "question")),
            "sentiment": str(data.get("sentiment", "neutral")),
            "tags": [str(t) for t in data.get("tags", [])][:10],
        }
    except Exception:
        return None


async def _auto_extract(text: str, use_llm: bool = False) -> ExtractionResult:
    """Extract: heuristic (0 token) by default, LLM only if requested.

    `_llm_extract` does a *synchronous* HTTP request, so it is offloaded to a
    worker thread via `asyncio.to_thread` to avoid blocking the MCP server's
    single event loop while the model responds.
    """
    if use_llm:
        llm_result = await asyncio.to_thread(_llm_extract, text)
        if llm_result:
            return ExtractionResult(
                topic=llm_result["topic"],
                keywords=llm_result["keywords"],
                entities=llm_result["entities"],
                domain=llm_result["domain"],
                intent=llm_result["intent"],
                sentiment=llm_result["sentiment"],
                tags=llm_result["tags"],
            )
    return SemanticExtractor.extract(text)


# ---------------------------------------------------------------------------
# Topic shift detection and auto-linking
# ---------------------------------------------------------------------------

TOPIC_SHIFT_THRESHOLD = 0.3


def _keyword_overlap(a: list[str], b: list[str]) -> float:
    if not a or not b:
        return 0.0
    set_a, set_b = set(a), set(b)
    inter = set_a & set_b
    return len(inter) / max(len(set_a | set_b), 1)


def _detect_topic_shift(new_kw: list[str], graph: Graph | None = None) -> tuple[bool, float]:
    g = graph or _g.get()
    if not g.last_keywords:
        return False, 0.0
    overlap = _keyword_overlap(new_kw, g.last_keywords)
    return overlap < TOPIC_SHIFT_THRESHOLD, overlap


def _auto_link(new_kw: list[str], turn: int, graph: Graph | None = None) -> list[Link]:
    """Create automatic links between new keywords and existing keywords in the graph."""
    g = graph or _g.get()
    if not g.nodes:
        return []
    links: list[Link] = []
    added_pairs: set[tuple[str, str]] = set()
    MAX_AUTO_LINKS = 8

    for kw in new_kw:
        candidates = _search_embeddings([kw], top_n=10, graph=g)
        for candidate_kw, sim in candidates:
            if candidate_kw == kw or candidate_kw in new_kw:
                continue
            if sim < 0.30:          # raised from 0.15 — cuts tangential noise
                continue
            pair = (kw, candidate_kw)
            rev_pair = (candidate_kw, kw)
            if pair in added_pairs or rev_pair in added_pairs:
                continue
            # also skip if link already exists in the graph (cross-call dedup)
            if any((lk.source == kw and lk.target == candidate_kw) or
                   (lk.source == candidate_kw and lk.target == kw)
                   for lk in g.links):
                continue
            weight = "strong" if sim > 0.65 else "medium" if sim > 0.45 else "tangential"
            links.append(Link(
                source=kw, target=candidate_kw,
                link_type="analogy",
                weight=weight,
                rationale=f"similarità vettoriale {sim:.2f}",
                created_turn=turn, last_active_turn=turn,
            ))
            added_pairs.add(pair)
        if len(links) >= MAX_AUTO_LINKS:
            break

    return links


def _build_context_window(extraction: ExtractionResult, turn: int, graph: Graph | None = None) -> str:
    """Build the optimal context window: active links + salient nodes + semantic flashes.

    Flash semantici (3 types, only when flash_enabled and turn > 3):
      1. Dormant pulse — high-salience node not mentioned in ≥ TANGENTIAL_EXPIRY_TURNS turns,
         semantically close to current keywords. Surfaces forgotten knowledge.
      2. Cross-domain spark — semantically similar node from a *different* loaded context graph.
         Bridges separate knowledge domains.
      3. Creative leap — a node reachable in exactly 2 hops from current keywords whose domain
         differs from the active domain. The most unexpected association.
    """
    g = graph or _g.get()
    parts: list[str] = []
    active_links = g.get_active_links()
    if active_links:
        top = sorted(
            active_links,
            key=lambda lk: (WEIGHT_ORDER[lk.weight], -lk.inactive_turns),
            reverse=True,
        )[:6]
        parts.append("Active links:")
        for lk in top:
            parts.append(f"  {lk.source} ->({lk.link_type})-> {lk.target} [{lk.weight}]")

    top_nodes = sorted(g.nodes, key=lambda nd: -nd.salience)[:8]
    if top_nodes:
        parts.append(f"\nSalient nodes (topic: {extraction.topic}):")
        for nd in top_nodes:
            parts.append(f"  {nd.keyword} (salience={nd.salience}, domain={nd.domain})")

    if turn > 1:
        overlap = _keyword_overlap(extraction.keywords, g.last_keywords)
        parts.append(f"\nContinuità col turno precedente: {overlap:.0%}")

    # --- Semantic flashes ---
    if flash_enabled and turn > 3:
        flashes: list[str] = []
        active_kws = set(extraction.keywords)

        # 1. Dormant pulse: salient node silent for ≥ TANGENTIAL_EXPIRY_TURNS turns
        sleep_threshold = max(TANGENTIAL_EXPIRY_TURNS, 4)
        dormant = [
            nd for nd in g.nodes
            if (turn - nd.turn) >= sleep_threshold
            and nd.salience >= 2
            and nd.keyword not in active_kws
        ]
        if dormant:
            try:
                candidates = _search_embeddings(extraction.keywords, top_n=8, graph=g)
            except Exception:
                # Fallback: pick most salient dormant node directly without vector search
                candidates = [(nd.keyword, 0.5) for nd in sorted(dormant, key=lambda n: -n.salience)]
            dormant_set = {nd.keyword for nd in dormant}
            for kw, sim in candidates:
                if kw in dormant_set and sim > 0.38:
                    nd = g.get_node(kw)
                    dormant_since = turn - nd.turn if nd else "?"
                    flashes.append(
                        f"💤 Dormant pulse: '{kw}' (sim={sim:.2f}, "
                        f"silent {dormant_since} turns, salience={nd.salience if nd else '?'})"
                    )
                    break  # one dormant flash is enough

        # 2. Cross-domain spark: semantically close node from a different context graph
        if hasattr(_g, "_graphs"):
            for other_ctx, other_g in list(_g._graphs.items()):
                if other_ctx == _g.active or not other_g.nodes:
                    continue
                cross = _search_embeddings(extraction.keywords, top_n=2, graph=other_g)
                for kw, sim in cross:
                    if sim > 0.48 and kw not in active_kws:
                        nd = other_g.get_node(kw)
                        dom = nd.domain if nd else other_ctx
                        flashes.append(
                            f"🔗 Cross-domain spark [{other_ctx}]: '{kw}' "
                            f"(sim={sim:.2f}, domain={dom})"
                        )
                        break  # one spark per other context

        # 3. Creative leap: 2-hop path from active keywords to a node in a different domain
        # Build adjacency: keyword → set of directly linked keywords
        adjacency: dict[str, set[str]] = {}
        for lk in g.links:
            if lk.weight in ("strong", "medium"):
                adjacency.setdefault(lk.source, set()).add(lk.target)
                adjacency.setdefault(lk.target, set()).add(lk.source)

        seen_leaps: set[str] = set()
        for kw in active_kws:
            for mid in adjacency.get(kw, set()):
                if mid in active_kws:
                    continue
                for far in adjacency.get(mid, set()):
                    if far in active_kws or far in seen_leaps or far == kw:
                        continue
                    nd = g.get_node(far)
                    if nd and nd.domain != extraction.domain:
                        flashes.append(
                            f"⚡ Creative leap: '{kw}' → '{mid}' → '{far}' "
                            f"[{nd.domain}]"
                        )
                        seen_leaps.add(far)
                        break
                if seen_leaps:
                    break
            if seen_leaps:
                break

        if flashes:
            parts.append("\nFlash semantici:")
            for fl in flashes[:3]:  # cap at 3 total flashes per turn
                parts.append(f"  {fl}")

    return "\n".join(parts) if parts else ""


# ---------------------------------------------------------------------------
# Vector embedding — lazy-loaded fastembed (384-dim)
# ---------------------------------------------------------------------------

_embedder: TextEmbedding | None = None


def _get_embedder() -> TextEmbedding:
    """Lazy-load the embedding model on first use (avoids slow startup)."""
    global _embedder
    if _embedder is None:
        _embedder = TextEmbedding("sentence-transformers/all-MiniLM-L6-v2")
    return _embedder


def _get_embedding(text: str) -> list[float]:
    """384-dim semantic embedding via fastembed (all-MiniLM-L6-v2)."""
    return list(_get_embedder().embed([text]))[0]




# ---------------------------------------------------------------------------
# Hybrid vector search: Turso SQL (vector_distance_cos) or Python fallback
# ---------------------------------------------------------------------------


def _search_embeddings(
    query_keywords: list[str],
    top_n: int = 8,
    graph: Graph | None = None,
) -> list[tuple[str, float]]:
    g = graph or _g.get()
    query_vec = _get_embedding(" ".join(query_keywords))
    query_blob = pack_vector(query_vec)

    SIM_THRESHOLD = 0.3

    if TURSO_ENGINE:
        seed_path = getattr(_g, '_seed_path', None)
        db_paths = [p for p in [seed_path, _active_db_path()] if p and os.path.exists(p)]
        for db in db_paths:
            try:
                conn = _db.connect_local(db)
                rows = conn.execute(
                    "SELECT keyword, sim FROM ("
                    "  SELECT keyword, 1.0 - vector_distance_cos(f32blob(embedding), f32blob(?)) AS sim "
                    "  FROM node_vectors"
                    ") WHERE sim > ? ORDER BY sim DESC LIMIT ?",
                    (query_blob, SIM_THRESHOLD, top_n),
                ).fetchall()
                conn.close()
                results = [(row[0], round(row[1], 4)) for row in rows]
                if results:
                    return results
            except sqlite3.DatabaseError:
                pass

    scores: list[tuple[str, float]] = []
    for nd in g.nodes:
        v = nd.vector if nd.vector is not None else _get_embedding(nd.keyword)
        sim = sum(qi * vi for qi, vi in zip(query_vec, v))
        if sim > 0:
            scores.append((nd.keyword, round(sim, 4)))
    scores.sort(key=lambda x: -x[1])
    return scores[:top_n]


def _normalize_domain(domain: str) -> str:
    """Normalize domain name: lowercase, alias mapping, strip noise."""
    cleaned = domain.lower().strip().replace("-", "").replace(" ", "")
    return DOMAIN_ALIASES.get(cleaned, DOMAIN_ALIASES.get(domain.lower(), domain.lower()))


def _refine_domain(keywords: list[str]) -> tuple[str | None, list[str]]:
    """Vector search via Turso vector_distance_cos against seed node_vectors.

    Returns (best_domain, alternative_domains) where best_domain is the highest-scoring
    specific domain (non-general) above threshold (0.35), or None if nothing matches.
    alternative_domains contains all other domains within the tie margin (0.05) for multi-domain tagging."""
    query_vec = _get_embedding(" ".join(keywords))
    query_blob = _pack_vector(query_vec)

    rows: list[tuple[str, float]] = []

    if TURSO_ENGINE:
        seed_path = getattr(_g, '_seed_path', None)
        if seed_path and os.path.exists(seed_path):
            try:
                conn = _db.connect_local(seed_path)
                rows = conn.execute("""
                    SELECT n.domain, 1.0 - vector_distance_cos(f32blob(nv.embedding), f32blob(?)) AS sim
                    FROM node_vectors nv
                    JOIN nodes n ON n.keyword = nv.keyword
                    WHERE n.domain != 'general'
                    ORDER BY sim DESC LIMIT 30
                """, (query_blob,)).fetchall()
                conn.close()
            except sqlite3.DatabaseError:
                pass

    # Fallback: Python loop over loaded graphs (non-Turso or Turso query failed)
    if not rows:
        for ctx_g in list(getattr(_g, '_graphs', {}).values()):
            for nd in (ctx_g.nodes or []):
                v = nd.vector
                if v is None:
                    continue
                sim = sum(qi * vi for qi, vi in zip(query_vec, v))
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

    alt = [d for d, s in ranked[1:] if best_score - s < TIE_MARGIN and d != "general"]
    return (best, alt)


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
    except Exception:
        pass


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
    except Exception:
        pass

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

def _active_db_path() -> str:
    ctx = _g.active.replace("/", "__") if _g.active != "default" else "default"
    return os.path.join(GRAPHS_DIR, f"graph_{ctx}.db")

dedup_enabled = True
flash_enabled = True

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

app = Server("neuron", version="3.2")


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------

@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="status",
            description="Current graph state: nodes, links, health, configuration",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="store_turn",
            description="Save a conversation turn: keyword, topic, domain, intent, sentiment, entities, tags, references, and links",
            inputSchema={
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Topic of the turn (3-5 words)"},
                    "keywords": {"type": "array", "items": {"type": "string"}, "description": "Abstract keywords (3-5)"},
                    "domain": {"type": "string", "enum": ["AI", "backend", "frontend", "gaming", "architecture", "general"]},
                    "intent": {"type": "string", "enum": ["question", "task", "exploration", "clarification", "feedback"]},
                    "sentiment": {"type": "string", "enum": ["neutral", "positive", "critical", "urgent"]},
                    "context": {"type": "string", "description": "Context path (e.g. java/spring). Defaults to active context.", "default": ""},
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
            description="Given a topic or keyword, returns related links and nodes from the graph",
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
            description="Semantic vector search. Find similar keywords via Turso vector_distance_cos or Python fallback (256-dim feature hashing).",
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
            description="Automatic semantic extraction from text: keyword, topic, domain, intent, sentiment, entities. Uses LLM (if configured) or heuristic.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to analyze (user message)",
                    },
                    "use_llm": {
                        "type": "boolean",
                        "description": "Force LLM extraction (Ollama). Default: heuristic 0 token.",
                        "default": False,
                    },
                    "context": {"type": "string", "description": "Context path (e.g. java/spring). Defaults to active context.", "default": ""},
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="auto",
            description="Complete auto-pipeline: extract, detect topic shift, auto-link, save turn, return context. No parameters required.",
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
                "Call at the START of each turn to load context in one shot. "
                "Equivalent to status + get_context(format='compact'). Returns active "
                "context summary and knowledge for the given topic. Ideal for providers "
                "without automatic injection hooks."
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

    # Rank nodes
    node_scores: dict[str, float] = {}
    for nd_kw in related_nodes:
        nd = g.get_node(nd_kw)
        if nd is None:
            continue
        base = float(nd.salience)
        recency = 2.0 if (g.turn_count - nd.turn) <= 5 else 0.0
        link_score = sum(
            WEIGHT_ORDER.get(lk.weight, 0)
            for lk in related_links_sorted
            if lk.source == nd_kw or lk.target == nd_kw
        )
        node_scores[nd_kw] = base + recency + link_score * 0.5
    top_nodes = sorted(node_scores.items(), key=lambda x: -x[1])

    return related_links_sorted, top_nodes, used_fallback, inherited_ctx, g


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    global dedup_enabled, flash_enabled, CONTEXT_SWITCH_THRESHOLD

    ctx = arguments.get("context", "")
    g = _g.get(ctx) if ctx else _g.get()

    if name == "status":
        ctx_label = ctx or _g.active
        total = len(g.links)
        active = len(g.get_active_links())
        strong = sum(1 for lk in g.links if lk.weight == "strong")
        medium = sum(1 for lk in g.links if lk.weight == "medium")
        sr = ((strong + medium) / total * 100) if total else 0
        types = len({lk.link_type for lk in g.links})
        pr = (g.pruned_count / (total + g.pruned_count) * 100) if (total + g.pruned_count) else 0
        npt = len(g.nodes) / max(g.turn_count, 1)
        engine = _db.ENGINE_NAME
        return [TextContent(type="text", text=(
            f"Context: {ctx_label}\n"
            f"Turn {g.turn_count} | Nodes: {len(g.nodes)} | Links: {total} (active {active})\n"
            f"Strong+medium: {sr:.0f}% | Types: {types} | Pruned: {pr:.0f}% | Nodes/turn: {npt:.1f}\n"
            f"Dedup: {'ON' if dedup_enabled else 'OFF'} | Flash: {'ON' if flash_enabled else 'OFF'} | "
            f"SwitchThreshold: {CONTEXT_SWITCH_THRESHOLD} turns"
            + (f" | Pending→{_domain_signal['domain']} ({_domain_signal['count']}/{CONTEXT_SWITCH_THRESHOLD})"
               if _domain_signal.get("domain") else "") + "\n"
            f"Engine: {engine} | Embedding: {VECTOR_DIM}dim"
        ))]

    if name == "store_turn":
        g.turn_count += 1
        turn = g.turn_count
        topic = arguments["topic"]
        keywords = arguments["keywords"]
        domain = arguments["domain"]
        intent = arguments["intent"]
        sentiment = arguments["sentiment"]
        entities = arguments.get("entities", [])
        tags = arguments.get("tags", [])
        references = arguments.get("references", [])
        new_links_data = arguments.get("links", [])

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
            else:
                g.add_node(Node(keyword=kw, turn=turn, topic=topic,
                                domain=domain, sentiment=sentiment,
                                entities=entities, tags=tags,
                                references=references))

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
            if tgt:
                tgt.salience += WEIGHT_ORDER[lk.weight]

        g.last_sentiment = sentiment
        g.last_topic = topic
        g.last_keywords = keywords
        g.increment_inactivity(set(keywords))
        removed = g.prune_tangential()
        _g.save(ctx or None)

        return [TextContent(type="text", text=(
            f"Turn {turn} saved. Nodes: {len(g.nodes)}, Links: {len(g.links)}"
            + (f", pruned: {removed}" if removed else "")
        ))]

    if name == "get_context":
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
                lines.append(
                    f"  [{lk.weight:10s}] {lk.source} ->({lk.link_type})-> {lk.target}"
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

    if name == "find_candidates":
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

    if name == "vector_search":
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

    if name == "summary":
        ctx_info = f"Context: {_g.active}"
        total = len(g.links)
        active = len(g.get_active_links())
        strong = sum(1 for lk in g.links if lk.weight == "strong")
        medium = sum(1 for lk in g.links if lk.weight == "medium")
        types = len({lk.link_type for lk in g.links})
        lines = [
            ctx_info,
            f"Turns: {g.turn_count}  |  Nodes: {len(g.nodes)}  |  Links: {total} (active {active})",
            f"Strong: {strong}  |  Medium: {medium}  |  Tangential: {total - strong - medium}",
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

    if name == "forgotten":
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

    if name == "prune":
        removed = g.prune_tangential()
        _g.save(ctx or None)
        return [TextContent(type="text", text=f"Pruned {removed} tangential links.")]

    if name == "dedup":
        dedup_enabled = not dedup_enabled
        state = "ON" if dedup_enabled else "OFF"
        return [TextContent(type="text", text=f"Keyword deduplication: {state}")]

    if name == "flash":
        flash_enabled = not flash_enabled
        state = "ON" if flash_enabled else "OFF"
        return [TextContent(type="text", text=f"Semantic flash: {state}")]

    if name == "reset":
        _g.reset(ctx or None)
        return [TextContent(type="text", text="Graph reset.")]

    if name == "export":
        return [TextContent(type="text", text=json.dumps(g.export(), ensure_ascii=False, indent=2))]

    if name == "extract":
        text = arguments["text"]
        use_llm = arguments.get("use_llm", False)
        result = await _auto_extract(text, use_llm=use_llm)
        return [TextContent(type="text", text=json.dumps({
            "topic": result.topic,
            "keywords": result.keywords,
            "entities": result.entities,
            "domain": result.domain,
            "intent": result.intent,
            "sentiment": result.sentiment,
            "tags": result.tags,
        }, ensure_ascii=False, indent=2))]

    if name == "auto":
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
            if tgt:
                tgt.salience += WEIGHT_ORDER[lk.weight]

        g.last_sentiment = extraction.sentiment
        g.last_topic = extraction.topic
        g.last_keywords = extraction.keywords
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

    if name == "switch_context":
        _g.switch(arguments["context"])
        return [TextContent(type="text", text=f"Switched to context: {_g.active}")]

    if name == "list_contexts":
        contexts = _g.list_contexts(arguments.get("parent"))
        lines = [f"  {c['context']:30s} nodes={c['nodes']} links={c['links']} turns={c['turns']}{' <- active' if c['active'] else ''}" for c in contexts]
        return [TextContent(type="text", text="\n".join(lines) or "No contexts found.")]

    if name == "pre_turn":
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
        if fallback_pt:
            parts_pt.append("(vector fallback)")
        if inh_pt:
            parts_pt.append(f"(from:{inh_pt})")
        ctx_text_pt = " | ".join(parts_pt) if parts_pt else "no context"
        out_pt = f"{status_line}\n{ctx_text_pt}"
        return [TextContent(type="text", text=out_pt[:char_budget_pt])]

    if name == "confirm":
        keywords = [str(k) for k in arguments.get("keywords", [])]
        boost    = min(int(arguments.get("boost", 2)), 5)
        confirmed: list[str] = []
        skipped:   list[str] = []
        for kw in keywords:
            nd = g.get_node(kw)
            if nd:
                nd.salience += boost
                confirmed.append(kw)
            else:
                skipped.append(kw)
        if confirmed:
            g._dirty = True
            _g.save(ctx or None)
        return [TextContent(type="text", text=json.dumps({
            "confirmed": confirmed,
            "boost": boost,
            "skipped": skipped,
        }, ensure_ascii=False))]

    if name == "merge":
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
            g._dirty = True
            _g.save(ctx or None)

        return [TextContent(type="text", text=json.dumps({
            "canonical": canonical,
            "merged": merged,
            "missing": missing,
            "canonical_salience": canon_nd.salience,
            "links_total": len(g.links),
        }, ensure_ascii=False))]

    return [TextContent(type="text", text=f"Unknown command: {name}")]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    from neuron import __version__
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="neuron",
                server_version=__version__,
                capabilities=app.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


def cli() -> None:
    """Synch