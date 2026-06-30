# Neuron — Persistent semantic memory for AI

Neuron is an **MCP server** that gives LLMs long-term memory.
It builds a concept graph across conversations: each exchange saves keywords
with vector embeddings and semantic links, retrievable in later sessions.

## Installation

### Windows

```powershell
.\install.ps1
```

The installer handles everything: Python → Rust → Windows SDK + MSVC (C++ tools only)
→ pip (mcp, fastembed, pyturso, 3 retries, hard fail). **fastembed is mandatory** — 384-dim semantic embeddings.

At the end, it asks whether to install packages for the **standalone chat** (`run_interactive.py`).
If you use Neuron only as an MCP server (OpenCode/Claude/Cursor), choose **0 (None)**.

### Linux / macOS

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install mcp pyturso fastembed  # fastembed is mandatory
python -m neuron
```

## Database engine

By default Neuron persists the graph through the **local pyturso engine**
(embedded libSQL, native `vector_distance_cos()`, single local `.db` file —
no cloud, no network). This is consistent across the whole codebase
(`neuron.db`): main graph storage, seed vector search, and dev scripts all
go through the same connection layer.

To switch to **real Turso cloud** (memory that survives across machines,
not just one local file), install the optional `cloud` extra and set two
environment variables:

```bash
pip install "neuron[cloud]"   # adds libsql-client
export TURSO_DATABASE_URL="libsql://your-db.turso.io"
export TURSO_AUTH_TOKEN="..."
```

When both are set, `neuron.db.connect()` talks to the remote Turso database
instead of the local file — no code changes needed. Leave them unset to
keep everything local (the current default).

## MCP Configuration

Neuron is a **local stdio MCP server**: the client launches it as a subprocess
(`python -m neuron`, or `run_mcp.bat` on Windows) — there's no daemon or HTTP port.
Mounting it therefore means registering that launch command with your client.

On Windows, `install.ps1` auto-registers **OpenCode**, **Claude Desktop** and **Cursor**.
Everything else is a one-time manual entry:

| Client | How to mount | Notes |
|---|---|---|
| OpenCode, Claude Desktop, Cursor | auto-registered by `install.ps1` | restart the client |
| Claude Code, Cline/Roocode, VS Code, Windsurf, Zed, Continue.dev, Cody, Amazon Q | add the launch command (see [DEVELOPER.md](DEVELOPER.md#mcp-client-configuration)) | local stdio |
| **Perplexity** (macOS app) | Settings → Connectors → add local MCP; command `python3`, args `-m neuron` | macOS-only; needs the PerplexityXPC helper |
| **ChatGPT / OpenAI** | wrap Neuron in a stdio→HTTP bridge (e.g. `mcp-remote`), then add the HTTPS URL as a connector | Developer Mode, paid plans; no local stdio support |

The universal launch command is `python3 -m neuron` (run inside the project venv so `mcp`,
`fastembed` and `pyturso` resolve); on the Windows install it's
`cmd /c %LOCALAPPDATA%\Programs\neuron\scripts\run_mcp.bat`. Full per-client JSON snippets and
the ChatGPT/Perplexity walkthroughs are in [DEVELOPER.md](DEVELOPER.md#mcp-client-configuration).

### OpenCode (`~/.config/opencode/opencode.json`)

```json
{
  "mcp": {
    "neuron": {
      "command": ["cmd", "/c", "%LOCALAPPDATA%\\Programs\\neuron\\scripts\\run_mcp.bat"],
      "type": "local"
    }
  },
  "instructions": [
    "%LOCALAPPDATA%\\Programs\\neuron\\skills\\auto-context.md"
  ]
}
```

The `instructions` field loads the auto-context skill, which tells the model to call
`neuron_pre_turn` at the start of each turn and `neuron_store_turn` after responding.

### Other clients

Claude Desktop, Claude Code, Cursor, Cline/Roocode, Windsurf, VS Code, Zed, Continue.dev,
Cody, Amazon Q, plus Perplexity (macOS) and ChatGPT (via bridge) — see
[DEVELOPER.md](DEVELOPER.md#mcp-client-configuration).

## Context inheritance

When the active context has no results for a topic, `neuron_get_context` and `neuron_pre_turn`
automatically search parent contexts (e.g. `default`) and annotate the output with `(from:<parent>)`.
This means nodes stored in `default` are always accessible regardless of which context is active.

## MCP Tools

| Tool | Description |
|---|---|
| `neuron_pre_turn(topic, keywords)` | **PRE shortcut** — status + compact context in one call |
| `neuron_status` | Graph state (nodes, links, active context) |
| `neuron_get_context(topic, ...)` | Retrieve related nodes and links; `format=compact` for injection; inherits from parent contexts automatically |
| `neuron_store_turn` | Save turn: keywords, links, entities, tags |
| `neuron_confirm(keywords)` | Boost salience of nodes that influenced the response |
| `neuron_auto(text)` | Heuristic extraction + save in one call (fallback for smaller models) |
| `neuron_extract(text)` | Standalone semantic extraction (no save) |
| `neuron_find_candidates(keywords)` | Find similar existing keywords before storing (dedup) |
| `neuron_merge(canonical, aliases)` | Absorb duplicate nodes into a single canonical node |
| `neuron_vector_search(keywords)` | Semantic vector search (no link traversal) |
| `neuron_summary` | Top nodes and recent links overview |
| `neuron_forgotten` | Concepts not touched in N turns |
| `neuron_switch_context(context)` | Switch active domain context (e.g. `java/spring`) |
| `neuron_list_contexts` | List all available contexts |
| `neuron_prune` | Force pruning of expired tangential links |
| `neuron_flash` / `neuron_dedup` | Toggle semantic flash / dedup features |
| `neuron_export` / `neuron_reset` | Export full graph as JSON / clear graph |

## API Keys (standalone chat only)

Environment variables for cloud providers:
- `OPENAI_API_KEY` — OpenAI / Azure / Compatible
- `ANTHROPIC_API_KEY` — Claude
- `GEMINI_API_KEY` — Google Gemini

Or save the config with `--save-config`:

```bash
python scripts/run_interactive.py --provider openai --model gpt-4o --save-config
```

## License

PolyForm Noncommercial 1.0.0 — see [LICENSE](LICENSE).
