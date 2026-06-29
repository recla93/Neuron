# Developer Guide — Neuron

## Table of Contents

- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Dependencies](#dependencies)
- [Vector Embeddings](#vector-embeddings)
- [Fallback Chain](#fallback-chain)
- [Key Behaviors](#key-behaviors)
- [MCP Client Configuration](#mcp-client-configuration)
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
│       ├── models.py          # Node, Link, Graph dataclasses + SQLite persistence
│       ├── registry.py        # GraphRegistry — multi-context, resolve_chain inheritance
│       └── server.py          # MCP server (19 tools, Turso/SQLite)
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   └── test_server.py         # Smoke tests
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
| `pyturso>=0.6.1` | yes | Turso DB engine (vector_distance_cos) |
| `ollama` | no | LLM provider for chat mode |
| `openai` | no | LLM provider for chat mode |
| `anthropic` | no | LLM provider for chat mode |
| `google-generativeai` | no | LLM provider for chat mode |

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

| Component | Primary | Fallback |
|---|---|---|
| Embedding | fastembed 384-dim | — |
| Database | pyturso (Turso) | sqlite3 (no vector search) |
| Vector search | `vector_distance_cos()` SQL | Python cosine in memory |

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

### Keyword normalization and dedup

All keywords are normalized at ingestion (`strip().lower()`). `add_node` deduplicates on the normalized key and max-merges salience rather than creating duplicates. `add_link` normalizes source/target and deduplicates in both directions.

### Auto-link thresholds

Semantic auto-links (generated from `store_turn`) use cosine similarity thresholds: `≥0.65` → strong, `≥0.45` → medium, `≥0.30` → tangential. Top 10 candidates per keyword are evaluated per call, with cross-call dedup to avoid re-adding existing links.

### Salience and decay

Nodes accumulate salience from `store_turn` (intent weight) and `confirm` (explicit feedback boost). Nodes not referenced in the last 5 turns lose 1 salience point. Tangential links expire after 5 inactive turns via `prune_tangential()`.

---

## MCP Client Configuration

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

On Linux/macOS, replace `cmd /c %LOCALAPPDATA%...` with `python3 -m neuron`.

---

## Interactive CLI Mode

Neuron includes a standalone chat mode (`run_interactive.py`) that connects directly to an LLM.
This is separate from the MCP server — it exists for testing and terminal use.

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

GitHub Actions workflow in `.github/workflows/ci.yml`:

```yaml
on: [push, pull_request]
jobs:
  check:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install mcp fastembed pyturso
      - run: python -m compileall src/
      - run: python -c "import mcp; import turso; from fastembed import TextEmbedding; print('OK')"
```

On PR: verifies syntax + imports on Windows. Linux/macOS can be added per contributor request.

## License

PolyForm Noncommercial License 1.0.0. See [LICENSE](LICENSE).
