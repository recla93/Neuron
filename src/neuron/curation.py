"""Curation gate for store_turn keywords (T54, "quality at the door").

The shared graph's quality depends on what models write into it, and MCP
cannot force good curation — so the server enforces it AT WRITE TIME with a
SOFT gate: bad keywords are dropped or remapped (never the whole turn), and
the tool response carries a one-line corrective note so the model learns the
rule in-context, this very turn.

Stdlib-only and framework-free on purpose: unit-testable anywhere, and the
first brick of the server modularization (T57).
"""

from __future__ import annotations

import unicodedata

KEYWORD_MAX_WORDS = 4          # concept nouns, not sentences

# Filler/action verbs (infinitives + common English forms) that models use as
# keywords when they narrate actions instead of naming concepts. Exact
# whole-token match only — never substring (avoids "software"/"hardware").
_FILLER_VERBS = {
    # Italian infinitives (the usual offenders)
    "implementare", "sistemare", "fixare", "correggere", "aggiungere",
    "rimuovere", "creare", "usare", "utilizzare", "fare", "migliorare",
    "gestire", "aggiornare", "modificare", "cambiare", "verificare",
    "controllare", "testare", "installare", "configurare", "eseguire",
    "lanciare", "analizzare", "capire", "risolvere", "ottimizzare",
    "refactorare", "deployare", "scrivere", "leggere",
    # English
    "implement", "fix", "fixing", "add", "adding", "remove", "create",
    "creating", "use", "using", "make", "making", "improve", "improving",
    "manage", "update", "updating", "change", "changing", "check",
    "checking", "test", "testing", "install", "installing", "configure",
    "run", "running", "analyze", "understand", "solve", "solving",
    "optimize", "optimizing", "refactor", "deploy", "write", "writing",
    "read", "reading", "work", "working", "do", "doing", "get", "getting",
}

# Verbs that also work as noun adjuncts in compounds ("install manifest",
# "test suite", "check system"): NEVER salvage-strip these from a multi-word
# keyword — the compound is usually a legitimate concept.
_AMBIGUOUS_ADJUNCTS = {
    "install", "test", "check", "update", "run", "work", "read", "change",
    "deploy", "build", "use",
}

_PATH_MARKERS = ("/", "\\")
_CODE_SUFFIXES = (".py", ".js", ".ts", ".md", ".json", ".toml", ".yml",
                  ".yaml", ".ps1", ".bat", ".txt", ".db", ".exe")


def _fold(s: str) -> str:
    """casefold + strip accents, for duplicate detection ("città" == "citta")."""
    nk = unicodedata.normalize("NFKD", s.casefold())
    return "".join(c for c in nk if not unicodedata.combining(c)).strip()


def _singularize(word: str) -> str:
    """Very naive EN/IT plural folding, good enough for near-dup detection.

    Order matters: strip the English plural FIRST, then fold the final vowel,
    so "salience" and "saliences" land in the same bucket ("salienc?"), and
    Italian gender/number variants (concetto/concetti) do too."""
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]          # backoffs -> backoff, saliences -> salience
    if len(word) > 3 and word[-1] in "aeio":
        word = word[:-1] + "?"    # concetto/concetti -> concett?, salience -> salienc?
    return word


def _dup_key(kw: str) -> str:
    return " ".join(_singularize(w) for w in _fold(kw).split())


def vet_keywords(
    keywords: list[str],
    existing: dict[str, str] | None = None,
) -> tuple[list[str], list[str]]:
    """Gate a store_turn keyword list.

    ``existing`` maps dup-keys (see ``_dup_key``) of the CURRENT graph's node
    keywords to their canonical surface form; pass
    ``{_dup_key(n.keyword): n.keyword for n in g.nodes}``.

    Returns ``(accepted, notes)``:
    - accepted: cleaned keywords, near-duplicates remapped onto the existing
      canonical node (so salience reinforces ONE node instead of splitting
      across "db layer"/"DB Layer"/"db layers");
    - notes: one short corrective string per drop/remap, to append to the
      tool response (in-context teaching, ~10 tokens each).

    Never returns an empty list if at least one keyword is salvageable; the
    caller falls back to a validation error only when everything is dropped.
    """
    existing = existing or {}
    accepted: list[str] = []
    notes: list[str] = []
    seen_keys: set[str] = set()

    for kw in keywords:
        kw = kw.strip()
        if not kw:
            continue
        words = kw.split()
        low_first = _fold(words[0])
        if any(m in kw for m in _PATH_MARKERS) or kw.lower().endswith(_CODE_SUFFIXES):
            notes.append(f"dropped '{kw}': file paths aren't concepts — name the idea instead")
            continue
        if len(words) > KEYWORD_MAX_WORDS:
            notes.append(f"dropped '{kw}': looks like a phrase — use a {KEYWORD_MAX_WORDS}-word concept noun")
            continue
        if len(words) == 1 and low_first in _FILLER_VERBS:
            notes.append(f"dropped '{kw}': verbs aren't concepts — name the thing itself")
            continue
        if (len(words) > 1 and low_first in _FILLER_VERBS
                and low_first not in _AMBIGUOUS_ADJUNCTS):
            # "fix retry backoff" -> salvage the noun part; but leave noun
            # compounds like "install manifest"/"test suite" untouched.
            salvaged = " ".join(words[1:])
            notes.append(f"'{kw}' → '{salvaged}' (dropped the leading verb)")
            kw = salvaged

        key = _dup_key(kw)
        if key in seen_keys:
            continue   # intra-turn duplicate, silently collapse
        canonical = existing.get(key)
        if canonical is not None and canonical != kw:
            notes.append(f"'{kw}' → existing concept '{canonical}'")
            kw = canonical
        seen_keys.add(key)
        accepted.append(kw)

    return accepted, notes


def curation_note(notes: list[str], max_notes: int = 3) -> str:
    """Compact corrective block for the tool response (empty when clean)."""
    if not notes:
        return ""
    shown = notes[:max_notes]
    extra = len(notes) - len(shown)
    tail = f" (+{extra} more)" if extra > 0 else ""
    return "\ncuration: " + "; ".join(shown) + tail
