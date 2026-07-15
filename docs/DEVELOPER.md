# 📖 Developer Guide — Neuron

> Part of **[Neuron](../README.md)** — persistent semantic memory for AI. This guide covers the
> internals: architecture, the memory dynamics, the DB layer, per-client config and CI.
> For installing, see **[INSTALL.md](../INSTALL.md)**; for the release story, see
> **[CHANGELOG.md](../CHANGELOG.md)**.

## Table of Contents

- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Dependencies](#dependencies)
- [Vector Embeddings](#vector-embeddings)
- [Fallback Chain](#fallback-chain)
- [Enabling Turso Cloud](#enabling-turso-cloud)
- [Key Behaviors](#key-behaviors)
- [MCP Client Configuration](#mcp-client-configuration)
  - [Transport: local stdio vs remote](#transport-local-stdio-vs-remote)
  - [Config by client reference](#config-by-client-reference)
- [Interactive CLI Mode](#interactive-cli-mode)
- [Development Setup](#development-setup)
- [CI/CD](#cicd)
- [License](#license)

---

## Architecture

```
YOUR MCP CLIENT (OpenCode, Claude Desktop, Cursor, etc.)
     │  calls MCP tools (stdin/stdout)
     ▼
┌──────────────────────────────────────────────────────────┐
│  server.py  (Python) — MCP entry, ~22 tools               │
│  ├── extraction.py — semantic extractor + lexicons        │
│  ├── curation.py   — quality gate (filler/dup/link fixup) │
│  ├── search.py / stimulus.py — retrieval + associative     │
│  │                            stimulus engine             │
│  ├── funnel.py     — signpost + skill registry            │
│  └── vector embedding (384-dim, fastembed) · Turso         │
│      vector_distance_cos() or Python fallback             │
├──────────────────────────────────────────────────────────┤
│  models.py — Node, Link, Graph, episodes                  │
│  registry.py — GraphRegistry (multi-context, inheritance) │
│  clients.py — cross-platform client registration engine   │
└────────────────────────────────┬─────────────────────────┘
                                 ▼
┌────────────────────────────────┬─────────────────────────┐
│  Turso Database (pyturso) — native vector search          │
│  ├── <context>.db per context (nodes, links, vectors)     │
│  ├── vector_distance_cos() inside Turso                   │
│  └── Python fallback (cosine similarity in memory)        │
└───────────────────────────────────────────────────────────┘
```

The MCP server runs as a **stdio subprocess** of the MCP client. No HTTP layer, no daemon — every LLM tool call is processed inline.

**Multi-context:** each context (`default`, `java/spring`, `python/django`, ...) is a separate graph stored in its own `.db` file. Contexts form a hierarchy: `java/spring` inherits from `java`, which inherits from `default`. When a lookup finds no results in the active context, `get_context` and `pre_turn` automatically walk up the chain.

## Project Structure

```
Neuron/
├── src/
│   └── neuron/
│       ├── __init__.py        # Package init, version, .env autoload
│       ├── __main__.py        # `python -m neuron` entry point (server / CLI dispatch)
│       ├── db.py              # DB tier selector: Turso cloud > local pyturso > sqlite3
│       ├── models.py          # Node, Link, Graph + episodes + SQLite persistence
│       ├── registry.py        # GraphRegistry — multi-context, resolve_chain inheritance
│       ├── server.py          # MCP server — PRODUCTION path (~22 tools, Turso/SQLite)
│       ├── extraction.py      # SemanticExtractor + lexicons (split from server.py)
│       ├── curation.py        # Quality gate: filler/dup drop, link canonicalization
│       ├── search.py          # Vector + graph retrieval
│       ├── stimulus.py        # Spreading activation / associative stimulus engine
│       ├── funnel.py          # Signpost + skill registry (the "door")
│       ├── clients.py         # Cross-platform client registration engine
│       ├── setup.py / manage.py / init.py  # `neuron setup|manage|register|doctor` CLIs
│       └── engine.py          # Standalone CLI engine for run_interactive.py — NOT production
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_core.py           # Core/unit tests
│   └── test_server.py         # MCP server smoke tests
├── scripts/
│   ├── run_interactive.py     # Interactive CLI chat (6 LLM providers)
│   ├── run_mcp.bat            # Windows MCP stdio launcher
│   ├── check.ps1              # Dependency check + repair
│   ├── neuron-summary.ps1     # Terminal graph summary
│   └── neuron_summary_query.py
├── skills/
│   ├── playbook.md            # Full PRE+POST workflow skill (provider-agnostic)
│   ├── neuron-opener.md       # Compact opener (OpenCode instructions)
│   └── neuron-curated-memory/ # Graph-hygiene curation skill
├── clients/                   # MCP config examples per client
│   ├── claude-desktop.example.json
│   ├── claude-code.example.json
│   ├── cursor.example.json
│   ├── vscode.example.json
│   ├── zed.example.json
│   ├── cline-roocode.example.json
│   ├── opencode.example.json
│   └── generic-mcp.example.json
├── NeuronInstaller.exe        # Windows first-run bootstrapper (no Python/terminal needed)
├── install.ps1                # installer engine (called by the bootstrapper)
├── pyproject.toml
├── README.md
├── DEVELOPER.md
├── LICENSE
├── .gitignore
└── .github/workflows/ci.yml
```

## Dependencies

| Package | Required | Purpose |
|---|---|---|
| `mcp>=1.28.0,<2.0` | yes | MCP SDK |
| `fastembed>=0.5.0,<1.0` | yes | 384-dim semantic embedding |
| `pyturso==0.6.1` | yes | Local Turso DB engine (vector_distance_cos) |
| `libsql-client>=0.3.1` | no (`neuron[cloud]`) | Real Turso cloud DB, used when `TURSO_DATABASE_URL`/`TURSO_AUTH_TOKEN` are set |
| `ollama` | no | LLM provider for chat mode |
| `openai` | no | LLM provider for chat mode |
| `anthropic` | no | LLM provider for chat mode |
| `google-generativeai` | no | LLM provider for chat mode |

All database access in the codebase goes through `neuron.db` (`connect()` /
`connect_local()`), which picks the engine tier: remote Turso cloud > local
pyturso engine > stdlib sqlite3. There is no other sqlite3-direct code path
left — `models.py` (graph persistence), `server.py` (vector search) and the
dev scripts under `scripts/` all use it.

The MCP server runs with only the first 3. The LLM providers are only for `run_interactive.py`.

## Vector Embeddings

384-dim semantic embeddings via `fastembed`, ONNX runtime, downloaded on first `import`.

```python
from fastembed import TextEmbedding
embedder = TextEmbedding()
vec = list(embedder.embed("database"))[0]  # 384-dim float32
```

### Embedding model (ADR-001)

The model is configurable via **`NS_EMBED_MODEL`**. Default:
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384-dim, multilingual) — covers
EN **and IT** in one comparable space. English-only workload → set
`NS_EMBED_MODEL=sentence-transformers/all-MiniLM-L6-v2` for a lighter model.

| model | dim | recall@3 | IT recall | ms/emb |
|---|---|---|---|---|
| `all-MiniLM-L6-v2` | 384 | 0.92 | 0.89 | 15.0 |
| `paraphrase-multilingual-MiniLM-L12-v2` (default) | 384 | 1.00 | 1.00 | 6.9 |

Benchmark: `python scripts/bench_embed.py --k 3` (fixture `tests/fixtures/bench_pairs_en_it.jsonl`).

**Vectors from different models are not comparable.** Changing `NS_EMBED_MODEL` requires re-embedding
the store: run `python scripts/reembed.py`. `save_sqlite` records `meta.embed_model`/`meta.embed_dim`;
`load_sqlite` ignores stored vectors (and recomputes them) when they disagree with the active model,
so a model switch degrades gracefully instead of returning garbage cosine scores. For a non-384-dim
model also set `NS_EMBED_DIM` to match (a mismatch raises on first embed).

## Fallback Chain

### Installer

| Component | URL 1 | URL 2 | URL 3 | Final |
|---|---|---|---|---|
| `rustup-init.exe` | `win.rustup.rs` | `static.rust-lang.org` | GitHub raw | exit 1 |
| Windows SDK | `fwlink/2120843` | Microsoft mirror | — | skip |
| MSVC Build Tools | `aka.ms/vs/17/release` | Microsoft mirror | — | 3 tries → GNU MinGW |
| GNU fallback | — | — | — | exit 1 if GNU fails |
| pip (mcp, fastembed, pyturso) | PyPI (3 retries) | — | — | exit 1 |

### Runtime (`mcp_server.py`)

| Component | Primary | Secondary | Final fallback |
|---|---|---|---|
| Embedding | fastembed 384-dim | — | — |
| Database | Turso cloud (`libsql-client`, if `TURSO_DATABASE_URL`/`TURSO_AUTH_TOKEN` set) | pyturso (local Turso engine) | sqlite3 (no vector search) |
| Vector search | `vector_distance_cos()` SQL (cloud or local engine) | — | Python cosine in memory |

## Enabling Turso Cloud

The cloud tier (`RemoteTursoConnection` in `src/neuron/db.py`) is implemented but ships
**disabled**: with no credentials, Neuron uses the local pyturso engine. The environment is
prepared so cloud can be switched on without code changes — connectivity against a real
Turso DB has not yet been validated end-to-end (tracked as a separate task).

To enable it:

1. **Install the cloud extra first** (the server imports `libsql_client` at startup once the
   credentials are set, so installing it first avoids an import crash):

   ```bash
   pip install -e .[cloud]      # or:  pip install "neuron[cloud]"
   ```

2. **Connect, test for real, and save — one command (recommended):**

   ```bash
   python scripts/connect_turso.py
   ```

   It prompts for the database URL and auth token (token entry is hidden), then actually
   connects and runs a **read + write probe** against the real Turso DB. Only if the probe
   succeeds does it offer to save the two credentials into `.env`. The token is never printed
   or logged. Flags: `--check-only` (test, never write), `--url/--token/--yes` for
   non-interactive/installer use. This is the online counterpart to the offline
   `check_cloud_config.py`.

   > The URL saved may differ in scheme from what you typed: `libsql://` uses a WebSocket
   > transport that some Turso endpoints reject with `WSServerHandshakeError: 400`; the tool
   > transparently falls back to `https://` (Hrana-over-HTTP, same SQL) and saves whichever
   > scheme actually connected, so the server uses the working transport too.

   **The server auto-loads `.env` at startup** (T16): on launch, Neuron searches up from its
   working directory for a `.env` and populates any unset variables from it (a real
   environment variable always wins). So once `connect_turso.py` has written `.env`, the
   cloud is used automatically — no need to also wire the vars into the client `env` block.
   Override the file location with `NEURON_ENV_FILE=/path/.env`, or disable with
   `NEURON_NO_DOTENV=1`. Auto-loading is always skipped under pytest so the suite never hits
   the live cloud. To smoke-test the live path end-to-end: `python scripts/smoke_cloud.py`.

   Prefer to set the env vars by hand instead? Export both (or copy `.env.example` → `.env`);
   both must be non-empty or Neuron silently stays on the local tier:

   ```bash
   export TURSO_DATABASE_URL="libsql://your-db-your-org.turso.io"
   export TURSO_AUTH_TOKEN="..."
   ```

3. **Offline sanity check (optional)** — reports the resolved tier and flags a
   half-configured state (credentials set but extra missing, or only one var set). Never
   connects, never prints the token:

   ```bash
   python scripts/check_cloud_config.py
   ```

### Sharing one cloud DB across a team (≤ 6)

All members point at the **same** `TURSO_DATABASE_URL`; each pastes their **own** auth token
(create per-member tokens with the Turso CLI so they can be revoked individually). Everyone
then runs `python scripts/connect_turso.py` once. Because the store is keyed by a `context`
column (see `save_sqlite`/`load_sqlite`), several contexts coexist in the shared tables
without colliding, and each member's saves upsert only their delta — no save wipes another's
rows. A solo user and the team run the **same code**; the only difference is the connection
string, so nothing behaves differently when working alone.

Tier selection is automatic and ordered: **Turso cloud** (both env vars) → **local pyturso**
(installed) → **stdlib sqlite3** (last resort, no native vector search). `connect_local()`
always stays local for file-scoped seed/vector operations even when cloud is active.

## Key Behaviors

### Context inheritance

`get_context` and `pre_turn` always search the full resolution chain when the active graph returns no results:

```
active: java/spring  →  java  →  default
```

If "virtual threads" exists only in `default`, querying it from `java/spring` still returns it, annotated with `(from:default)`. No client-side configuration needed.

### pre_turn shortcut

`neuron_pre_turn(topic, keywords)` is a single-call alternative to `status` + `get_context(compact)`. It returns a one-liner status followed by compact context:

```
[neuron] ctx=backend turn=14 nodes=42 links=31(active 18)
links:kotlin_flow-[s]->coroutines|spring_boot-[m]->di | nodes:kotlin_flow(22),spring_boot(18)
```

Designed for providers without automatic injection hooks (OpenCode, Cursor, etc.) — one call at turn start gives everything needed to answer with memory.

### Concept extraction (heuristic by default, LLM opt-in)

Extraction runs on the per-turn hot path (`auto` tool → `_auto_extract`). Two modes exist:

- **Heuristic only:** `SemanticExtractor` — lexical analysis, token scoring, pattern
  matching. Zero tokens, deterministic, fast, fully unit-tested. Used by `auto` and `extract`.
  Server-side LLM extraction was removed — LLM-based extraction is the calling LLM's
  responsibility via `store_turn`.

### Keyword normalization and dedup

All keywords are normalized at ingestion (`strip().lower()`). `add_node` deduplicates on the normalized key and max-merges salience rather than creating duplicates. `add_link` normalizes source/target and deduplicates in both directions.

### Auto-link thresholds

Semantic auto-links (generated from `store_turn`) use cosine similarity thresholds: `≥0.65` → strong, `≥0.45` → medium, `≥0.30` → tangential. Top 10 candidates per keyword are evaluated per call, with cross-call dedup to avoid re-adding existing links.

### Salience and decay

Nodes accumulate salience from `store_turn` (intent weight) and `confirm` (explicit feedback boost). Nodes not referenced in the last 5 turns lose 1 salience point. Tangential links expire after 5 inactive turns via `prune_tangential()`.

---

## MCP Client Configuration

### Transport: local stdio vs remote

Neuron is a **local stdio MCP server** — it has no HTTP layer and is launched as a
subprocess by the client (`python -m neuron`, or `run_mcp.bat` on Windows). How you mount it
depends on what transport the client speaks:

- **Local-stdio clients** (Claude Desktop, Claude Code, Cursor, OpenCode, Cline/Roocode,
  VS Code Copilot, Windsurf, Zed, Continue.dev, Cody, Amazon Q, and the **Perplexity macOS**
  app) accept a launch command directly. Register the command below and restart the client.
- **Remote-only clients** (ChatGPT / OpenAI Apps & connectors) connect to an **HTTPS MCP
  endpoint**, not a local process. To use Neuron there, wrap the stdio server with a
  stdio→HTTP bridge ([`mcp-proxy`](https://github.com/sparfenyuk/mcp-proxy) in server mode),
  expose it over a public HTTPS tunnel, and register that URL as a custom connector — see
  [ChatGPT / OpenAI](#chatgpt--openai-via-bridge) below.

The launch command itself is the same everywhere:

| Platform | Command | Args |
|---|---|---|
| **Windows** (active install) | `cmd` | `/c %LOCALAPPDATA%\Programs\neuron\scripts\run_mcp.bat` |
| **Windows** (dev repo) / **Linux** / **macOS** | `python3` | `-m neuron` (inside the project venv) |

On Linux/macOS, run from the project venv so `mcp`, `fastembed` and `pyturso` are importable
(`source .venv/bin/activate` or point the client at `.venv/bin/python3`).

### OpenCode

```json
{
  "mcp": {
    "neuron": {
      "command": ["cmd", "/c", "%LOCALAPPDATA%\\Programs\\neuron5\\scripts\\run_mcp.bat"],
      "type": "local"
    }
  }
}
```

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "neuron": {
      "command": "cmd",
      "args": ["/c", "%LOCALAPPDATA%\\Programs\\neuron5\\scripts\\run_mcp.bat"]
    }
  }
}
```

### Claude Code (`.mcp.json` or `~/.claude.json`)

```json
{
  "mcpServers": {
    "neuron": {
      "command": "python3",
      "args": ["-m", "neuron"]
    }
  }
}
```

### Cursor (`~/.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "neuron": {
      "command": "python3",
      "args": ["-m", "neuron"]
    }
  }
}
```

### Cline / Roocode (`~/.vscode/globalStorage/.../mcp_config.json`)

```json
{
  "mcpServers": {
    "neuron": {
      "command": "python3",
      "args": ["-m", "neuron"]
    }
  }
}
```

### VS Code (Copilot)

In `.vscode/settings.json`:

```json
{
  "github.copilot.mcpServers": {
    "neuron": {
      "command": "python3",
      "args": ["-m", "neuron"]
    }
  }
}
```

### Windsurf (`~/.codeium/windsurf/mcp_config.json`)

```json
{
  "mcpServers": {
    "neuron": {
      "command": "python3",
      "args": ["-m", "neuron"]
    }
  }
}
```

### Zed (`~/.config/zed/settings.json`)

```json
{
  "mcp_servers": {
    "neuron": {
      "command": "python3",
      "args": ["-m", "neuron"]
    }
  }
}
```

### Continue.dev (`~/.continue/config.json`)

```json
{
  "experimental": {
    "mcpServers": {
      "neuron": {
        "command": "python3",
        "args": ["-m", "neuron"]
      }
    }
  }
}
```

### Cody (`~/.cody/mcp.json`)

```json
{
  "mcpServers": {
    "neuron": {
      "command": "python3",
      "args": ["-m", "neuron"]
    }
  }
}
```

### Amazon Q Developer (`~/.aws/amazon-q/mcp.json`)

```json
{
  "mcpServers": {
    "neuron": {
      "command": "python3",
      "args": ["-m", "neuron"]
    }
  }
}
```

### Perplexity (macOS app — local MCP)

The Perplexity **Mac** app can run local MCP servers through its `PerplexityXPC` helper
(Perplexity → Settings → Connectors → Add local MCP). There is no JSON config file: you fill
in the command and args in the UI.

| Field | Value |
|---|---|
| Command | `/path/to/Neuron/.venv/bin/python3` (or `python3` if the venv is active) |
| Arguments | `-m neuron` |

Install the `PerplexityXPC` helper when prompted, then add Neuron as a local connector and
enable it. Local MCP is currently macOS-only; on Windows use one of the stdio clients above,
or the bridge route below. Remote custom connectors are a paid-tier Perplexity feature.

### ChatGPT / OpenAI (via bridge)

ChatGPT (Developer Mode connectors / Apps SDK) only talks to **remote HTTPS** MCP endpoints —
it cannot launch a local stdio process. Neuron is a stdio server, so you put a **stdio→HTTP
bridge in front of it** and give ChatGPT the bridge's public URL. Transport and storage are
independent: the bridge only changes *how the client reaches Neuron*; the wrapped
`python -m neuron` still resolves its storage tier from the environment exactly as usual
(local files, or the shared Turso Cloud if `.env` / the env provide the credentials).

Use a bridge that **runs a stdio server and exposes it over HTTP/SSE** — the right tool is
[`mcp-proxy`](https://github.com/sparfenyuk/mcp-proxy) in *server* mode. (Do **not** use
`mcp-remote`: that goes the other direction — it lets a stdio *client* reach a remote HTTP
server, which is the opposite of what we need.)

```bash
# 1. Wrap Neuron's stdio server and serve it over HTTP/SSE on localhost.
#    (exact flag names vary by mcp-proxy version — see its README)
uvx mcp-proxy --port 8000 -- python3 -m neuron
#    → local endpoints: http://127.0.0.1:8000/mcp (Streamable HTTP) and /sse (legacy)

# 2. Expose that local port over PUBLIC HTTPS — ChatGPT connectors are remote and
#    can't reach localhost. A quick tunnel:
cloudflared tunnel --url http://127.0.0.1:8000
#    → gives a https://<random>.trycloudflare.com URL

# 3. In your client (Perplexity, or ChatGPT → Settings → Connectors (Developer Mode))
#    add the public HTTPS URL with the /mcp path, e.g.
#    https://<random>.trycloudflare.com/mcp   (NOT /sse — Cloudflare buffers the SSE
#    handshake so the legacy endpoint times out behind a tunnel).
```

Notes:
- MCP connectors in ChatGPT require Developer Mode (beta) and are limited to Plus / Pro /
  Business / Enterprise / Education plans on the web.
- **Security:** the tunnel exposes a network endpoint and Neuron ships with **no auth layer**.
  Keep the tunnel private/short-lived, or put access control in front of it (e.g. Cloudflare
  Access), and revoke it when unused.
- A **native** HTTP transport (Neuron serving Streamable HTTP directly, no bridge) is a
  possible future addition — see T15 in `TASKLIST.md`. It would remove the bridge hop but
  still needs a public HTTPS endpoint for ChatGPT, so the bridge is the simplest path today.

### Config by client reference

| Client | Config file | Server key | Restart |
|---|---|---|---|
| **OpenCode** | `~/.config/opencode/opencode.json` | `mcp` | `/mcp reload` |
| **Claude Code** | `.mcp.json` or `~/.claude.json` | `mcpServers` | `/mcp` or restart |
| **Claude Desktop** | `claude_desktop_config.json` | `mcpServers` | app restart |
| **Cursor** | `~/.cursor/mcp.json` | `mcpServers` | app restart |
| **Cline / Roocode** | VS Code global storage | `mcpServers` | app restart |
| **Windsurf** | `~/.codeium/windsurf/mcp_config.json` | `mcpServers` | app restart |
| **VS Code (Copilot)** | `.vscode/settings.json` | `github.copilot.mcpServers` | app restart |
| **Zed** | `~/.config/zed/settings.json` | `mcp_servers` | project restart |
| **Continue.dev** | `~/.continue/config.json` | `experimental.mcpServers` | IDE restart |
| **Cody** | `~/.cody/mcp.json` | `mcpServers` | IDE restart |
| **Amazon Q** | `~/.aws/amazon-q/mcp.json` | `mcpServers` | IDE restart |
| **Perplexity (macOS)** | in-app UI (no file) | Settings → Connectors → local MCP | re-enable connector |
| **ChatGPT / OpenAI** | in-app UI (no file) | Developer Mode → Connectors (HTTPS URL) | reconnect connector |

On Linux/macOS, replace `cmd /c %LOCALAPPDATA%...` with `python3 -m neuron`. ChatGPT requires a
stdio→HTTP bridge; Perplexity local MCP is macOS-only (see the subsections above).

---

## Interactive CLI Mode

Neuron includes a standalone chat mode (`run_interactive.py`) that connects directly to an LLM.
This is separate from the MCP server — it exists for testing and terminal use.

> **Not the production path.** `run_interactive.py` is powered by `src/neuron/engine.py`,
> a self-contained engine that reimplements extraction/linking/flash independently of
> `server.py`. The two do **not** share code and are **not** kept in functional parity:
> the engine is versioned on its own (historically "v3.1") and may lag the server (v3.3).
> Edit `engine.py` only for the interactive CLI; for production MCP behaviour edit `server.py`.

### Supported Providers

| Provider | Package | Flag | Default model | Fast model |
|---|---|---|---|---|
| **Ollama** (local) | `ollama` | `--provider ollama` | `qwen2.5:14b` | `qwen2.5:3b` |
| **OpenAI** | `openai` | `--provider openai` | `gpt-4o` | `gpt-4o-mini` |
| **Azure OpenAI** | `openai` | `--provider azure` | `gpt-4o` | `gpt-4o-mini` |
| **Anthropic** | `anthropic` | `--provider anthropic` | `claude-sonnet-4-5` | `claude-haiku-3-5` |
| **Gemini** | `google-generativeai` | `--provider gemini` | `gemini-2.5-pro` | `gemini-2.0-flash-lite` |
| **Compatible** | `openai` | `--provider compatible --base-url ...` | `mistral` | same as main |

### Provider CLI

```bash
# Ollama (locale, nessuna API key)
python scripts/run_interactive.py --provider ollama

# OpenAI
python scripts/run_interactive.py --provider openai --api-key sk-...

# Anthropic Claude
python scripts/run_interactive.py --provider anthropic --api-key sk-ant-...

# Compatible (LM Studio, Groq, Perplexity, DeepSeek, vLLM, LiteLLM...)
python scripts/run_interactive.py --provider compatible --base-url http://localhost:1234/v1
```

### API Key Resolution

Keys are resolved in this order (see `resolve_key()` in `run_interactive.py`):

1. `--api-key` CLI argument
2. Environment variable (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `AZURE_OPENAI_API_KEY`)
3. `~/.neuron/config.json` (saved with `--save-config`)

### In-Chat Commands

| Command | Action |
|---|---|
| `/neuron status` | Graph state |
| `/neuron summary` | Graph summary |
| `/neuron prune` | Prune tangential links |
| `/neuron flash` | Toggle semantic flashbacks |
| `/neuron export` | Export graph as JSON |
| `/neuron reset` | Clear graph |
| `/exit` or Ctrl+C | Exit |

---

## Development Setup

```bash
git clone https://github.com/<your-user>/Neuron.git
cd Neuron
python3 -m venv .venv && source .venv/bin/activate
pip install -e .[dev]
```

### Available commands

```bash
python -m neuron                      # Start MCP server (stdio)
python scripts/run_interactive.py     # Start interactive chat
python scripts/run_interactive.py --provider ollama  # Chat with local LLM
```

### Windows installer and GUI

For a clean Windows machine, distribute `NeuronInstaller.exe` together with the
repository files, especially `install.ps1` and `vendor/`. The small bootstrapper is
compiled from `installer/NeuronInstaller.cs` with the .NET Framework compiler already
present on Windows; it locates or asks for the source folder, runs `install.ps1 -Yes`,
shows its output and opens the installed Control Center. It is intentionally a
bootstrapper, not a self-contained offline bundle.

After installation, the supported human entry point is **Neuron — Control Center**.
The Tkinter GUI owns setup, registration, deploy/update, Turso, Bridge/Tunnel, graph
maintenance and vault import. `scripts/run_mcp.bat` remains only as a compatibility
launcher for legacy MCP registrations; it is not an interactive user entry point.

Build the bootstrapper on Windows with:

```powershell
powershell -ExecutionPolicy Bypass -File installer\build-installer.ps1
```

### Verify syntax

```bash
python -m compileall src/
```

## CI/CD

Two workflows:

- **`.github/workflows/ci.yml`** (on push / PR): a `test` job on `windows-latest`
  (caches the fastembed ONNX model, `pip install -e .[dev]`, byte-compiles `src/`,
  runs `pytest tests/ -v`) and a `build` job on `ubuntu-latest` that runs
  `python -m build` and verifies the resulting wheel imports `neuron`.
- **`.github/workflows/release.yml`** (on tag `v*`): builds the Windows
  `pyturso` wheels (matrix 3.10–3.14), builds the Neuron wheel + sdist, and
  publishes a GitHub Release with all assets attached. See
  [INSTALL.md](../INSTALL.md) for how those assets are consumed.

### Releasing

1. Bump `__version__` in `src/neuron/__init__.py` (the single source of truth;
   `pyproject.toml` reads it dynamically and the MCP server reports it). Follow
   semver: PATCH for fixes, MINOR for features, MAJOR for breaking/behavior changes.
2. Move the `[Unreleased]` notes in `CHANGELOG.md` under a new
   `## [X.Y.Z] - <date>` heading and fill in Added/Changed/Fixed/Removed.
3. Run the full test suite (`scripts/run_tests.ps1`) and do a clean-machine install
   smoke test via the Neuron Control Center → Setup / Manage. If you build the
   wheel/sdist locally to test, delete `build/`, `dist/`, and `src/*.egg-info`
   first — a stale `build/` staging dir or a cached `egg-info/SOURCES.txt` can
   bundle old files (CI builds from a fresh checkout, so the tagged Release is
   unaffected). Sanity-check the artifacts: the wheel should contain only the
   `neuron` package, and the sdist should not ship `CLAUDE.md` or any `.db` seed.
4. (Optional) Ship a seed: build one with `python scripts/import_vault.py` (set
   `NEURON_VAULT` or pass `--vault`), then copy the resulting DB into
   `src/neuron/data/base_knowledge.db` — only a real, populated SQLite file.
5. Tag and push: `git tag vX.Y.Z && git push --tags`. `release.yml` builds the
   prebuilt PyTurso wheels and publishes the GitHub Release.
6. To bump `pyturso`, change the pin in `pyproject.toml` **and** the version in
   `release.yml`'s `build-pyturso-win` job together.

### Deploy / sync to the active install

The MCP server actually used by clients runs from a **separate install dir**
(`%LOCALAPPDATA%\Programs\neuron5`), populated by `install.ps1`. That installer also sets up
the toolchain (Rust/MSVC), creates the venv and installs deps — heavy, and not what you want
for a quick "push my code edits to the install" loop. Use `scripts/deploy.ps1` for that:

```powershell
# preview the plan (source vs installed version, target venv) — no writes:
powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1 -DryRun

# reinstall the current source into the install venv, then import-check it:
powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1

# also run the test suite via the install venv (if it has pytest):
powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1 -RunTests
```

It **reinstalls the package into the install's venv** (`pip install --force-reinstall --no-deps`),
which is what `python -m neuron` actually imports (site-packages) — not a loose copy of `src\`
that nothing loads. `--force-reinstall` also beats the "same `__version__` → pip skips" trap, so
identical-version code still lands. It never touches the install's `.venv` interpreter, `graphs\`
or `logs\`, verifies by importing `neuron.server` **from the venv**, and confirms the installed
`__version__` matches source. This replaces hand-copying and keeps source ↔ install from drifting
between releases. (`-Prune` is now a no-op — a wheel reinstall already replaces stale files.)

## Running v4 and v5 "Synapse" side by side

Neuron ships as two active lines: **v4.x** (the stable line, `master`) and **v5.x "Synapse"**
(this branch — associative-memory engine: Hebbian links, spreading activation, drift, sleep-mode).
They are designed to coexist on one machine as **separate MCP servers with isolated memory**, because
v5 changed the DB schema and the default embedding model — a shared graph store would corrupt each
other's vectors. Nothing in the package needs renaming; the two lines differ only in *install
identity*:

| Axis | v4 | v5 "Synapse" |
|---|---|---|
| Version | `4.x` | `5.x` (current: **5.3.1**) |
| MCP server name (`Server(...)`) | `neuron` | `neuron5` |
| Default graph store | `%LOCALAPPDATA%\neuron\graphs` | `%LOCALAPPDATA%\neuron5\graphs` |
| Install dir (target) | `%LOCALAPPDATA%\Programs\neuron` | `%LOCALAPPDATA%\Programs\neuron5` |
| MCP client config key | `neuron` | `neuron5` |

Each install is a self-contained directory with its own venv, so `import neuron` never clashes — the
isolation is purely at the install/registration layer plus the distinct default store dir (baked into
`_default_graphs_dir()` on this branch). Register both in the client's MCP config under different keys
(`neuron` → v4's `run_mcp.bat`, `neuron5` → v5's) and they appear as two independent memories. Override
either store with `NS_GRAPHS_DIR` if you want a custom location. See `TASKLIST.md` T39 for the
remaining install-layer wiring (deploy target dir + installer registration name).

## Memory dynamics (v5 "Synapse")

v5 turns the graph from a tagged store into an associative memory. All of the below
live in `src/neuron/models.py` (pure graph logic) and `src/neuron/server.py` (wiring).

**Hebbian reinforcement (E2.1).** When two keywords co-occur in a turn, the link between
them accrues `co_activation_count` — at most once per `HEBBIAN_COOLDOWN` (2) turns — and its
weight is promoted `tangential→medium→strong` at 3 and 8. Promotion is monotone (a stale
concurrent writer can only raise it). `Graph.reinforce_coactivation()`, called from
`store_turn`/`auto`. Schema: `links.co_activation_count`.

**Composite retrieval ranking (E2.2).** `get_context` ranks nodes by
`RANK_WEIGHTS = sim·0.5 + salience·0.3 + recency·0.2` (tunable) instead of weight→recency, so a
salient neighbour surfaces even without a direct vector match. Auto-consolidation protects
nodes with `salience ≥ CONSOLIDATE_PROTECT_SALIENCE` (8) from being merged.

**Spreading activation (E2.3).** `Graph.spreading_activation(seeds, k=2)` propagates activation
along links, each hop weighted by `link_strength × (1 + salience/max) × decay`. Hebbian-strong
links carry more; `decay<1` and small `k` prevent flooding. It's the engine behind the flashes
and the pre-staged stimulus.

**Unified flashes (E2.4).** The three heuristics — 💤 dormant pulse, 🔗 cross-domain spark,
⚡ creative leap — now feed one selector: spreading activation scores the in-graph candidates and
only the **top-2** are emitted (`_build_context_window`). Cross-domain stays a distinct signal
(the engine is single-graph). A future "Option B" (engine as the primary generator) is noted in
the code.

**Piggyback stimulus (E2.5).** `store_turn` and `pre_turn` append a compact one-line stimulus
(top spreading-activation node), capped to ~40 tokens and suppressed below
`STIMULUS_MIN_ACTIVATION` (no noise on a cold graph). `_stimulus_block()`.

**Cross-context drift (E3.1/E3.2).** When a node from another *visited* context surfaces
alongside the current keywords (via the cross-domain spark), Neuron forms an implicit `drift`
link: born tangential, `DRIFT_COOLDOWN` (5), pruned after `DRIFT_EXPIRY_TURNS` (3) idle turns
(faster than intra-context tangentials), reinforced via the Hebbian counter. Drift stays out of
the normal views and surfaces only on `get_context(depth≥3)`, rendered `target@context`. Schema:
`links.target_context`. `Graph.form_drift_link()`.

**Sleep-mode + pre-staging (E3.3/E3.4).** On load, if a context was idle >`SLEEP_IDLE_SECONDS`
(30 min), `Graph.sleep_maybe()` (called from `registry.get`) consolidates (when
`NS_CONSOLIDATE_AUTO`) and pre-computes the top stimulus into `meta.staged_stimulus`. `pre_turn`
serves it once via `take_staged_stimulus()` if fresher than `STAGE_FRESH_SECONDS` (6h) — a warm
start that works around MCP's lack of push. Degrades to "consolidate-at-startup-if-idle" with no
external scheduler. Schema: `meta.last_active_timestamp` / `staged_stimulus` / `staged_ts`.

**Testing each** (all real-dep-free except where noted): `tests/test_hebbian.py`,
`test_composite_ranking.py` (needs mcp/fastembed), `test_spreading.py`, `test_core.py`
(flash cap), `test_stimulus_piggyback.py` (needs mcp/fastembed), `test_drift.py`, `test_sleep.py`.
Tuning knobs are the module constants above — calibrate on real data.

## License

PolyForm Noncommercial License 1.0.0. See [LICENSE](../LICENSE).

## Consolidation & search scaling (ANN threshold)

Neuron keeps the graph small so search stays fast, rather than reaching for an
approximate-nearest-neighbour index too early.

**Consolidation.** `neuron consolidate` (or the `consolidate` MCP tool) merges
near-duplicate concepts (cosine > 0.85, configurable) and archives low-salience
orphans to a recoverable `_graveyard` table. Enable the automatic pass by setting
`NS_CONSOLIDATE_AUTO=1` — it then runs every 20 turns after a save. Merges are
salience-aware (`protect_salience`) so important nodes are never absorbed, and
everything is recoverable from `_graveyard`.

**Search cost today.** Vector search is a linear scan: `vector_distance_cos` in
SQL on the Turso tiers, a Python cosine loop on the plain-sqlite tier. Cost is
O(N) per query in the *active context* (search is context-scoped, so N is the
nodes of one context, not the whole store). Missing vectors are computed once and
cached/persisted (never re-embedded per search).

**When to introduce an ANN index.** Stay on the linear scan until a single
context regularly exceeds **~10–20k nodes** and query latency becomes noticeable.
At that point add an approximate index on `node_vectors` (libSQL/Turso
`libsql_vector_idx` / DiskANN) to move from O(N) to sub-linear search. Before then
it is over-engineering: pruning + consolidation + context scoping keep N well
under that range for normal use.
