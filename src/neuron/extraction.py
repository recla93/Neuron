"""Heuristic semantic extraction (T57 — moved verbatim out of server.py).

Zero-token, deterministic extractor: tokenization with accent folding, token
scoring, entity/bigram promotion, domain/intent/sentiment detection. No LLM,
no MCP, no embedding dependencies — pure stdlib, unit-testable anywhere.

server.py re-exports every public name below, so existing imports
(``from neuron.server import SemanticExtractor``) keep working (ADR-006).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from neuron.config import env_int as _env_int

KEYWORD_MAX_LENGTH = _env_int("NEURON_KEYWORD_MAX_LENGTH", 40)
TOPIC_MAX_LENGTH = _env_int("NEURON_TOPIC_MAX_LENGTH", 100)
KEYWORD_PATTERN = re.compile(r"^[a-zA-Z0-9\s\-_.:+/]+$")


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
    # Italian technical/action verbs + their common conjugations (esp. the "noi"
    # -iamo form that dominates collaborative dev talk). These were promoting
    # verbs like `usiamo`/`riduciamo`/`disegnare`/`adottiamo`/`passiamo` to nodes.
    "usare", "uso", "usi", "usa", "usiamo", "usate", "usano", "usato", "usando",
    "ridurre", "riduco", "riduci", "riduce", "riduciamo", "riducono", "ridotto",
    "disegnare", "disegno", "disegna", "disegniamo", "disegnano", "disegnato",
    "adottare", "adotto", "adotta", "adottiamo", "adottano", "adottato",
    "passare", "passo", "passi", "passa", "passiamo", "passano", "passato",
    "gestire", "gestisco", "gestisci", "gestisce", "gestiamo", "gestiscono", "gestito",
    "creare", "creo", "crea", "creiamo", "creano", "creato",
    "aggiungere", "aggiungo", "aggiunge", "aggiungiamo", "aggiungono", "aggiunto",
    "configurare", "configura", "configuriamo", "configurato",
    "implementare", "implementa", "implementiamo", "implementato",
    "migliorare", "migliora", "miglioriamo", "migliorato",
    "provare", "provo", "prova", "proviamo", "provano", "provato",
    "mettere", "metto", "mette", "mettiamo", "mettono", "messo",
    "prendere", "prendo", "prende", "prendiamo", "prendono", "preso",
    "trovare", "trovo", "trova", "troviamo", "trovano", "trovato",
    "pensare", "penso", "pensa", "pensiamo", "pensano", "pensato",
    "scrivere", "scrivo", "scrive", "scriviamo", "scritto",
    "leggere", "leggo", "legge", "leggiamo", "letto",
    "servire", "serve", "servono", "serva",
    "facciamo", "andiamo", "vediamo", "diciamo", "vogliamo", "dobbiamo", "possiamo",
    # English action verbs that likewise shouldn't become nodes
    "use", "using", "used", "add", "adding", "added", "create", "creating",
    "created", "make", "making", "made", "get", "getting", "set", "setting",
    "build", "building", "built", "run", "running", "reduce", "reducing",
    # Italian prepositions/connectors that otherwise leak as pseudo-keywords
    "via", "verso", "tramite", "mediante", "presso", "oltre", "circa",
    "inoltre", "infatti", "invece", "eppure", "ovvero", "dunque", "quindi",
    "mentre", "senza", "sotto", "sopra", "dentro", "fuori", "prima", "dopo",
    "stato", "stati", "stessa", "stesso", "stesse", "stessi", "mia", "mio",
    "miei", "mie", "tuo", "tuoi", "tua", "tue", "suo", "suoi", "sua", "sue",
    "nostro", "nostra", "nostri", "nostre", "vostro", "vostra", "vostri",
    "vostre", "loro", "cui", "non", "si", "ci", "vi", "mi", "ti", "lo",
    "la", "li", "le", "ne", "ho", "hai", "ha", "hanno", "ho", "hai", "ha",
    "abbiamo", "avete", "hanno", "era", "erano", "sono", "sei", "siamo",
    "siete", "e", "ed", "o", "ma", "se", "no", "grazie", "ok", "okay",
    # Accented connectors/adverbs in ASCII-folded form (tokens are folded before
    # matching, so "perché"→"perche", "così"→"cosi", "cioè"→"cioe", etc.).
    "perche", "poiche", "giacche", "benche", "sebbene", "affinche", "finche",
    "cosi", "cioe", "ne", "sara", "saranno", "puo", "piu", "gia", "pero",
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


def _fold_accents(text: str) -> str:
    """Strip diacritics, mapping accented Latin letters to their ASCII base
    (à→a, é→e, ù→u, ç→c). Keeps case. Used so tokenization and stopword matching
    treat "città"/"citta" and "più"/"piu" identically."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(c)
    )


class SemanticExtractor:
    """Heuristic semantic extractor from raw text.

    Uses lexical analysis, pattern matching, and known domains.
    Does not require LLM.
    """

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        # Fold accents to ASCII BEFORE matching. The token regex is ASCII-only, so
        # without this an accented word is truncated at the first accent —
        # "città"→"citt", "perché"→"perch", "così"→"cos", "università"→"universit" —
        # and those garbage stems leaked as keywords, while accent-stripped stopwords
        # ("piu"/"gia"/"puo") never matched "più"/"già"/"può". Folding makes both work:
        # "città"→"citta" (clean noun), "più"→"piu" (matches the stopword).
        text = _fold_accents(text.strip())
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
        # Bigram bonus: a token adjacent to another gets +0.15 per participating
        # pair whose bigram isn't itself a counted key. Precompute each token's
        # participating pairs ONCE (O(N)) instead of rescanning every adjacent
        # pair for every counted keyword (was O(keywords x tokens), P1 #7).
        token_pairs: dict[str, list[str]] = {}
        for j in range(len(tokens) - 1):
            lj, lj1 = tokens[j].lower(), tokens[j + 1].lower()
            bigram = f"{lj} {lj1}"
            token_pairs.setdefault(lj, []).append(bigram)
            if lj1 != lj:                       # one entry per pair, matching the
                token_pairs.setdefault(lj1, []).append(bigram)  # original per-j semantics
        for low in list(counts):
            for bigram in token_pairs.get(low, ()):
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


async def _auto_extract(text: str) -> ExtractionResult:
    """Extract semantic info via heuristic (0 token). LLM extraction is the calling LLM's
    responsibility — it provides params directly via store_turn."""
    return SemanticExtractor.extract(text)
