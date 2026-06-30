"""Import an Obsidian vault into a local Neuron seed database.

This is a LOCAL, personal tool: point it at your own Obsidian vault and it
builds a `base_knowledge.db` graph (nodes = note concepts, links = wikilinks /
Graphify edges). The output is meant to stay on your machine — decide
deliberately if/when to ship it as the public seed (copy into
src/neuron/data/base_knowledge.db).

No paths are hardcoded. The vault root comes ONLY from the NEURON_VAULT
environment variable or the --vault flag.

Usage:
    set NEURON_VAULT=C:\\path\\to\\your\\vault     (Windows)
    export NEURON_VAULT=/path/to/your/vault       (Linux/macOS)
    python scripts/import_vault.py
    # or:
    python scripts/import_vault.py --vault /path/to/vault --out ./knowledge/base_knowledge.db

Embeddings: if `fastembed` is installed, 384-dim vectors are generated inline
(semantic search works immediately). If not, the graph is still built and a
note explains how to add vectors later with scripts/populate_vectors.py.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import struct
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Config / CLI
# ---------------------------------------------------------------------------

SKIP_DIRS = {"RAG-Nexus", "RAG-Nexus - Backup", ".obsidian", "Pics",
             "graphify-out", ".git", "node_modules"}

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "knowledge" / "base_knowledge.db"

_KW_PATTERN = re.compile(r"^[a-zA-Z0-9\s\-_.:+/]+$")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Import an Obsidian vault into a Neuron seed DB.")
    p.add_argument("--vault", default=os.environ.get("NEURON_VAULT"),
                   help="Obsidian vault root (or set NEURON_VAULT).")
    p.add_argument("--out", default=str(DEFAULT_OUT),
                   help=f"Output DB path (default: {DEFAULT_OUT}).")
    p.add_argument("--no-embed", action="store_true",
                   help="Skip inline embedding generation even if fastembed is available.")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Keyword quality filter
# ---------------------------------------------------------------------------

def is_valid_keyword(kw: str) -> bool:
    """Return True only for clean, usable keywords."""
    if not kw:
        return False
    if len(kw) < 3 or len(kw) > 40:
        return False
    if any(c in kw for c in "(){}[]<>\\"):
        return False
    if kw.startswith(("_", ".", "#")):
        return False
    if kw.count("/") > 1:               # allow "java/spring", not deep paths
        return False
    if not _KW_PATTERN.match(kw):
        return False
    if len(kw) > 20 and " " not in kw and kw[0].islower():
        return False
    return True


# ---------------------------------------------------------------------------
# Domain inference
# ---------------------------------------------------------------------------

DOMAIN_KEYWORDS: list[tuple[str, list[str]]] = [
    ("backend", ["java", "spring", "hibernate", "jpa", "sql", "database",
                 "python", "django", "fastapi", "csharp", ".net", "c#",
                 "php", "laravel", "symfony", "node", "express", "nestjs",
                 "mapstruct", "mongodb", "redis", "kafka", "rabbitmq",
                 "rest", "api", "graphql", "microservice", "backend",
                 "jdbc", "orm", "query", "endpoint", "dto", "entity",
                 "repository", "service", "controller"]),
    ("architecture", ["docker", "kubernetes", "infrastructure", "architettura",
                      "design pattern", "solid", "clean", "hexagonal",
                      "ddd", "domain driven", "event sourcing", "cqrs",
                      "monolith", "deployment", "ci/cd", "devops"]),
    ("AI", ["machine learning", "deep learning", "neural", "llm", "rag",
            "embedding", "vector", "nlp", "transformer", "dataset",
            "training", "inference", "classification", "ai"]),
    ("frontend", ["angular", "react", "vue", "svelte", "typescript",
                  "css", "html", "javascript", "dom", "ui", "ux",
                  "frontend", "browser", "responsive"]),
    ("gaming", ["unity", "unreal", "godot", "game", "shader", "sprite"]),
]
DEFAULT_DOMAIN = "general"


def infer_domain(source_file: str, content: str = "") -> str:
    lower_path = source_file.lower().replace("\\", "/")
    lower_content = content.lower()[:2000]
    scores: dict[str, int] = {}
    for domain, kws in DOMAIN_KEYWORDS:
        score = sum(1 for kw in kws if kw in lower_path)
        score += sum(1 for kw in kws if kw in lower_content) // 3
        if score:
            scores[domain] = scores.get(domain, 0) + score
    if not scores:
        return DEFAULT_DOMAIN
    return max(scores, key=scores.get)


# ---------------------------------------------------------------------------
# Load Graphify output (optional: <vault>/**/graphify-out/graph.json)
# ---------------------------------------------------------------------------

def load_graphify(path: Path) -> tuple[list[dict], list[dict]]:
    if not path.exists():
        return [], []
    data = json.loads(path.read_text(encoding="utf-8"))
    nodes: dict[str, dict] = {}
    links: list[dict] = []
    seen_links: set[tuple[str, str]] = set()

    for gn in data.get("nodes", []):
        sf = gn.get("source_file", "")
        if not sf or sf.startswith("."):
            continue
        kw = gn.get("label", gn["id"])
        if not is_valid_keyword(kw):
            continue
        tags = [p for p in Path(sf).parts[:-1] if p not in SKIP_DIRS]
        nodes[gn["id"]] = {
            "keyword": kw, "turn": 0, "topic": kw[:80],
            "domain": infer_domain(sf), "sentiment": "neutral", "salience": 1,
            "entities": "[]", "tags": json.dumps(tags[:10]),
            "refs": json.dumps([{"type": "file", "path": sf, "description": kw[:80]}]),
        }

    for gl in data.get("links", []):
        s, t = gl.get("source"), gl.get("target")
        if not s or not t or s not in nodes or t not in nodes:
            continue
        if (s, t) not in seen_links and (t, s) not in seen_links:
            seen_links.add((s, t))
            links.append({
                "source": s, "target": t, "link_type": "deepening",
                "weight": "medium", "rationale": gl.get("relation", "connected"),
                "created_turn": 0, "last_active_turn": 0, "inactive_turns": 0,
            })
    return list(nodes.values()), links


# ---------------------------------------------------------------------------
# Scan raw .md files
# ---------------------------------------------------------------------------

def scan_md_files(root: Path) -> tuple[list[dict], list[dict]]:
    files: list[dict] = []
    for dirpath, dirs, names in os.walk(str(root)):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            if not name.endswith(".md"):
                continue
            fpath = Path(dirpath) / name
            files.append({
                "path": str(fpath),
                "relative": str(fpath.relative_to(root)),
                "filename": name[:-3],
            })

    nodes: dict[str, dict] = {}
    links: list[dict] = []
    seen_links: set[tuple[str, str]] = set()
    collisions: dict[str, list[dict]] = {}
    for f in files:
        collisions.setdefault(f["filename"], []).append(f)

    for f in files:
        kw = f["filename"]
        collided = len(collisions.get(kw, [])) > 1
        content = Path(f["path"]).read_text(encoding="utf-8", errors="replace")
        title = kw
        m = re.search(r"^#\s+(.+)", content, re.MULTILINE)
        if m:
            title = m.group(1).strip()[:80]
        if collided:
            parts = [p for p in Path(f["relative"]).parts[:-1] if p not in SKIP_DIRS]
            kw = f"{parts[-1]}/{kw}" if parts else kw
        if not is_valid_keyword(kw):
            continue

        domain = infer_domain(f["path"], content)
        tags = [p for p in Path(f["relative"]).parts[:-1] if p not in SKIP_DIRS]
        wikilinks = re.findall(r"\[\[([^\]]+?)(?:\|[^\]]+?)?\]\]", content)

        if kw in nodes:
            existing = nodes[kw]
            et = json.loads(existing["tags"]); er = json.loads(existing["refs"]); ee = json.loads(existing["entities"])
            for t in tags:
                if t not in et: et.append(t)
            ref = {"type": "file", "path": f["relative"], "description": title}
            if ref not in er: er.append(ref)
            for e in wikilinks:
                if e not in ee: ee.append(e)
            existing["salience"] += 1
            existing["tags"] = json.dumps(et[:10])
            existing["refs"] = json.dumps(er[:5])
            existing["entities"] = json.dumps(ee[:30])
        else:
            nodes[kw] = {
                "keyword": kw, "turn": 0, "topic": title, "domain": domain,
                "sentiment": "neutral", "salience": 1,
                "entities": json.dumps(wikilinks[:30]),
                "tags": json.dumps(tags[:10]),
                "refs": json.dumps([{"type": "file", "path": f["relative"], "description": title}]),
            }

        for target in wikilinks[:30]:
            if target == kw:
                continue
            if (kw, target) not in seen_links and (target, kw) not in seen_links:
                seen_links.add((kw, target))
                links.append({
                    "source": kw, "target": target, "link_type": "deepening",
                    "weight": "medium", "rationale": "wikilink",
                    "created_turn": 0, "last_active_turn": 0, "inactive_turns": 0,
                })
    return list(nodes.values()), links


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

def merge(nodes_a, links_a, nodes_b, links_b):
    merged: dict[str, dict] = {}
    for n in nodes_a + nodes_b:
        kw = n["keyword"]
        if kw in merged:
            en = merged[kw]
            en["salience"] += n.get("salience", 1)
            for attr in ("tags", "entities"):
                a = set(json.loads(en.get(attr, "[]"))); b = set(json.loads(n.get(attr, "[]")))
                en[attr] = json.dumps(list(a | b)[:30])
            ra = json.loads(en.get("refs", "[]")); rb = json.loads(n.get("refs", "[]"))
            seen_r = {json.dumps(r, sort_keys=True) for r in ra}
            for r in rb:
                k = json.dumps(r, sort_keys=True)
                if k not in seen_r:
                    seen_r.add(k); ra.append(r)
            en["refs"] = json.dumps(ra[:10])
        else:
            merged[kw] = dict(n)

    seen_links: set[tuple[str, str]] = set()
    out_links: list[dict] = []
    for lk in links_a + links_b:
        if (lk["source"], lk["target"]) not in seen_links and (lk["target"], lk["source"]) not in seen_links:
            seen_links.add((lk["source"], lk["target"]))
            out_links.append(lk)
    return list(merged.values()), out_links


# ---------------------------------------------------------------------------
# Embeddings (optional, inline)
# ---------------------------------------------------------------------------

def try_embed(nodes: list[dict]) -> list[tuple[str, bytes, int]] | None:
    """Return [(keyword, blob, dim)] or None if fastembed unavailable."""
    try:
        from fastembed import TextEmbedding
    except ImportError:
        return None
    embedder = TextEmbedding("sentence-transformers/all-MiniLM-L6-v2")
    dim = 384
    out: list[tuple[str, bytes, int]] = []
    for i, n in enumerate(nodes):
        kw = n["keyword"]
        text = kw if len(kw) > 3 else (n.get("topic") or kw)
        vec = list(embedder.embed(text))[0].tolist()
        out.append((kw, struct.pack(f"{len(vec)}f", *vec), dim))
        if i and i % 500 == 0:
            print(f"  embedded {i}/{len(nodes)}")
    return out


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def save_db(nodes, links, vectors, out_path: str):
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(out_path)
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT, turn INTEGER, topic TEXT,
                domain TEXT, sentiment TEXT, salience INTEGER,
                entities TEXT DEFAULT '[]', tags TEXT DEFAULT '[]', refs TEXT DEFAULT '[]');
            CREATE INDEX IF NOT EXISTS idx_nodes_keyword ON nodes(keyword);
            CREATE TABLE IF NOT EXISTS node_vectors (
                keyword TEXT PRIMARY KEY, embedding BLOB NOT NULL, dim INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT, target TEXT, link_type TEXT, weight TEXT,
                rationale TEXT, created_turn INTEGER,
                last_active_turn INTEGER, inactive_turns INTEGER);
            CREATE INDEX IF NOT EXISTS idx_links_source ON links(source);
            CREATE INDEX IF NOT EXISTS idx_links_target ON links(target);
        """)
        conn.execute("DELETE FROM meta")
        for k, v in [("session_id", "seed"), ("turn_count", "0"),
                     ("last_sentiment", "neutral"), ("last_topic", "seed knowledge")]:
            conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (k, v))

        conn.execute("DELETE FROM nodes")
        conn.executemany(
            "INSERT INTO nodes (keyword, turn, topic, domain, sentiment, salience, entities, tags, refs) "
            "VALUES (:keyword, :turn, :topic, :domain, :sentiment, :salience, :entities, :tags, :refs)", nodes)

        conn.execute("DELETE FROM links")
        conn.executemany(
            "INSERT INTO links (source, target, link_type, weight, rationale, created_turn, last_active_turn, inactive_turns) "
            "VALUES (:source, :target, :link_type, :weight, :rationale, :created_turn, :last_active_turn, :inactive_turns)", links)

        conn.execute("DELETE FROM node_vectors")
        if vectors:
            conn.executemany(
                "INSERT OR REPLACE INTO node_vectors (keyword, embedding, dim) VALUES (?, ?, ?)", vectors)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    if not args.vault:
        sys.exit("ERROR: no vault. Set NEURON_VAULT or pass --vault <path>.")
    vault = Path(args.vault)
    if not vault.exists():
        sys.exit(f"ERROR: vault not found: {vault}")

    print(f"Vault: {vault}")
    # Graphify output, if present anywhere under the vault
    gnodes, glinks = [], []
    for gj in vault.rglob("graphify-out/graph.json"):
        gn, gl = load_graphify(gj)
        print(f"  Graphify {gj}: {len(gn)} nodes, {len(gl)} links")
        gnodes += gn; glinks += gl

    print("Scanning .md files...")
    bnodes, blinks = scan_md_files(vault)
    print(f"  Markdown: {len(bnodes)} nodes, {len(blinks)} links")

    nodes, links = merge(gnodes, glinks, bnodes, blinks)
    print(f"Merged: {len(nodes)} nodes, {len(links)} links")

    vectors = None
    if not args.no_embed:
        print("Generating embeddings (fastembed)...")
        vectors = try_embed(nodes)
        if vectors is None:
            print("  fastembed not installed — skipping vectors.")
            print("  (run scripts/populate_vectors.py later to add semantic search)")
        else:
            print(f"  {len(vectors)} vectors generated")

    save_db(nodes, links, vectors, args.out)
    print(f"\nSaved: {args.out}")

    domains: dict[str, int] = {}
    for n in nodes:
        domains[n["domain"]] = domains.get(n["domain"], 0) + 1
    print("Domain distribution:")
    for d, c in sorted(domains.items(), key=lambda x: -x[1]):
        print(f"  {d}: {c}")
    print("\nThis DB is LOCAL. To ship it as the public seed, copy it to "
          "src/neuron/data/base_knowledge.db deliberately.")


if __name__ == "__main__":
    main()
