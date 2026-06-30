# Developer Guide — Neuron

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
│  server.py  (Python)                                      │
│  ├── 19 MCP tools                                         │
│  ├── vector embedding (384-dim semantic, fastembed)        │
│  └── search: Turso vector_distance_cos() or Python        │
├──────────────────────────────────────────────────────────┤
│  models.py — Node, Link, Graph dataclasses                │
│  registry.py — GraphRegistry (multi-context, inheritance) │
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
│       ├── __init__.py        # Package init, version
│       ├── __main__.py        # `python -m neuron` entry point
│       ├── db.py              # DB tier selector: Turso cloud > local pyturso > sqlite3
│       ├── models.py          # Node, Link, Graph dataclasses + SQLite persistence
│       ├── registry.py        # GraphRegistry — multi-context, resolve_chain inheritance
│       ├── server.py          # MCP server — PRODUCTION path (19 tools, Turso/SQLite)
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
│   ├── SKILL_base.md          # Minimal LLM instructions
│   ├── SKILL_full.md          # Full LLM instructions
│   └── auto-context.md        # PRE+POST auto-context skill (provider-agnostic)
├── clients/                   # MCP config examples per client
│   ├── claude-desktop.example.json
│   ├── claude-code.example.json
│   ├── cursor.example.json
│   ├── vscode.example.json
│   ├── zed.example.json
│   ├── cline-roocode.example.json
│   ├── opencode.example.json
│   └── generic-mcp.example.json
├── install.ps1                # Windows installer (auto-registers OpenCode, Claude Desktop, Cursor)
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
| `mcp>=1.28.0` | yes | MCP SDK |
| `fastembed>=0.5.0` | yes | 384-dim semantic embedding |
| `pyturso>=0.6.1` | yes | Local Turso DB engine (vector_distance_cos) |
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

384-dim semantic embeddings via `fastembed` (sentence-transformers/all-MiniLM-L6-v2, ONNX runtime ~80MB model).
Downloaded on first `import`.

```python
from fastembed import TextEmbedding
embedder = TextEmbedding()
vec = list(embedder.embed("database"))[0]  # 384-dim float32
```

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

2. **Set both credentials** (see `.env.example` for a template). Both must be present and
   non-empty, or Neuron silently stays on the local tier:

   ```bash
   export TURSO_DATABASE_URL="libsql://your-db-your-org.turso.io"
   export TURSO_AUTH_TOKEN="..."
   ```

3. **Run the offline readiness check** — reports the resolved tier and flags a
   half-configured state (credentials set but extra missing, or only one var set). It never
   opens a connection and never prints the token:

   ```bash
   python scripts/check_cloud_config.py
   ```

4. **Validate connectivity (final step, currently out of scope).** With both the extra and
   the credentials in place, run a one-off query in a dev shell to confirm the real Turso DB
   answers and `vector_distance_cos()` is available server-side, e.g.:

   ```python
   from neuron import db
   conn = db.connect("graph_default.db")   # → RemoteTursoConnection when cloud is configured
   print(conn.execute("select 1").fetchone())
   conn.close()
   ```

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

- **Heuristic (default):** `SemanticExtractor` — lexical analysis, token scoring, pattern
  matching. Zero tokens, deterministic, fast, fully unit-tested. Used by `auto` and by
  `extract` unless overridden.
- **LLM (opt-in):** `_llm_extract` calls an Ollama / OpenAI-compatible endpoint
  (`NS_LLM_ENDPOINT`, `NS_LLM_MODEL`). Exposed only via the `extract` tool with
  `use_llm=true`; the `auto` pipeline never enables it.

**Design decision — heuristic stays the default; LLM is not enabled on the live path.**
The `auto` pipeline runs every turn, so an LLM call there would add a synchronous HTTP
round-trip per turn, introduce a hard dependency on a running model endpoint, and make the
path non-deterministic. The heuristic already covers topic/keywords/domain/intent/sentiment.
Use `extract(use_llm=true)` when higher-quality concepts are worth the latency/cost for a
specific call. ⚠️ Known issue: `_llm_extract` is synchronous and is currently called from
the async `call_tool` handler without `asyncio.to_thread`, so a `use_llm=true` call blocks
the event loop until it returns. Wrapping it in `asyncio.to_thread` is tracked separately
before LLM extraction is recommended for any high-throughput use.

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
  stdio→HTTP bridge (e.g. [`mcp-remote`](https://www.npmjs.com/package/mcp-remote)) and
  register the resulting URL as a custom connector — see
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
      "command": ["cmd", "/c", "%LOCALAPPDATA%\\Programs\\neuron\\scripts\\run_mcp.bat"],
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
      "args": ["/c", "%LOCALAPPDATA%\\Programs\\neuron\\scripts\\run_mcp.bat"]
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
it cannot launch a local stdio process. Expose Neuron over HTTP with a bridge, then register
the URL:

```bash
# 1. Run Neuron behind an HTTP bridge (example with mcp-remote / a stdio→HTTP proxy)
npx mcp-remote --port 8000 -- python3 -m neuron
# 2. Make the port reachable over HTTPS (reverse proxy, tunnel, or a hosted box)
# 3. In ChatGPT → Settings → Connectors (Developer Mode) → add the https://… MCP URL
```

Notes: MCP connectors in ChatGPT require Developer Mode (beta) and are limited to Plus / Pro /
Business / Enterprise / Education plans on the web. Because this exposes a network endpoint,
restrict access (auth/tunnel) — Neuron itself ships with no auth layer.

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

### Verify syntax

```bash
python -m compileall src/
```

## CI/CD

GitHub Actions workflow in `.github/workflows/ci.yml` (on push / PR, `windows-latest`,
Python 3.12): caches the fastembed ONNX model, `pip install -e .[dev]`, byte-compiles
`src/`, runs `pytest tests/ -v`, and finally dry-runs the deploy script
(`scripts/deploy.ps1 -DryRun`) as a sync-logic sanity check. Linux/macOS can be added per
contributor request.

### Deploy / sync to the active install

The MCP server actually used by clients runs from a **separate install dir**
(`%LOCALAPPDATA%\Programs\neuron`), populated by `install.ps1`. That installer also sets up
the toolchain (Rust/MSVC), creates the venv and installs deps — heavy, and not what you want
for a quick "push my code edits to the install" loop. Use `scripts/deploy.ps1` for that:

```powershell
# preview exactly what would change (no writes):
powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1 -DryRun

# sync changed/new files, then byte-compile + import-check the install:
powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1

# also remove files deleted from source, and run the test suite in the install:
powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1 -Prune -RunTests
```

It copies only the deployable set (code, config, docs, the seed `knowledge\base_knowledge.db`),
never the install's `.venv`, `graphs\` or `knowledge_grown\`, is idempotent (MD5 diff), and
checks that the deployed `__version__` matches source. This replaces hand-copying and keeps
source ↔ install from drifting between releases.

## License

PolyForm Noncommercial License 1.0.0. See [LICENSE](LICENSE).
