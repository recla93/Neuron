# Neuron — Persistent semantic memory for AI

**Current release: v5.0.1 "Synapse"** — associative memory (Hebbian link reinforcement,
spreading activation, cross-context drift, sleep-mode consolidation) plus auto-handshake
plugins for OpenCode and Claude Code. See [CHANGELOG](CHANGELOG.md) for the full v5 story.

Neuron is an **MCP server** that gives LLMs long-term memory. Across conversations it builds a
**concept graph**: every exchange saves keywords with 384-dim vector embeddings and semantic
links, retrievable in later sessions — per topic **context**, with inheritance from parent
contexts. It runs **local-first** (a single `.db` file, no network) and can optionally back a
**shared team memory** on Turso Cloud, where several people write into the same knowledge at
once without stepping on each other.

- **Local by default** — embedded libSQL (pyturso) with native `vector_distance_cos()`, or
  stdlib sqlite3 as a last resort. No daemon, no HTTP port.
- **Shared & concurrent (optional)** — point everyone at one Turso Cloud DB. Writes are
  incremental and atomic: two people editing the same node both count, and no one's save wipes
  another's rows. See the [Team guide](docs/TEAM.md).
- **Any MCP client** — Claude Desktop/Code, Cursor, OpenCode, VS Code, Windsurf, Zed, and more
  via local stdio; ChatGPT via an HTTP [bridge](docs/BRIDGE.md).

Requires **Python 3.10–3.14**.

## Install

### Windows

The easy path: **double-click `Configuration.bat`** and choose
**Install / Update Neuron → FULL**. It handles everything (prerequisites →
PyTurso → Neuron + embedding model), can wire Neuron into your AI app, and every
run is logged under `%LOCALAPPDATA%\Programs\neuron\logs\`. Or run the installer
directly:

```powershell
.\install.ps1
```

Neuron installs as a real Python package into a dedicated venv, using a **pre-built `pyturso`
wheel** from `.\vendor` (Python 3.10–3.14) so no C/Rust compiler is needed — it only falls back
to the *minimal* MSVC build tools if your Python is outside that prebuilt range. `fastembed`
(semantic embeddings) is mandatory. See **[INSTALL.md](INSTALL.md)** for the manual path and
troubleshooting.

**Updating an existing install:** pull the latest, then `Configuration.bat` →
**Install / Update Neuron → FULL**. It installs with `pip --upgrade` and refuses
to let an older bundled wheel shadow newer source, so you always land the newest
code. (To start completely clean, use **Clean install / Uninstall Neuron** first.)

### Linux / macOS

`pyturso` has prebuilt wheels on PyPI for Linux/macOS, so a plain install works:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install neuron-<version>-py3-none-any.whl     # from the GitHub release
# or, from a source checkout:  pip install ".[dev]"
python -m neuron
```

## Storage: local, or shared on Turso Cloud

Neuron resolves its storage tier automatically, in this order:

1. **Turso Cloud** — when `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN` are set. Memory is shared
   and survives across machines; `vector_distance_cos()` runs server-side.
2. **Local pyturso** — embedded libSQL, native vector search, one local file (the default).
3. **stdlib sqlite3** — last resort, Python-side cosine similarity.

Everything goes through one connection layer (`neuron.db`), so the tiers are interchangeable
with **no code changes** — the only difference between working solo and as a team is the
connection string.

### Turn on the cloud (recommended flow)

```bash
pip install "neuron[cloud]"          # adds libsql-client
python scripts/connect_turso.py      # prompts for URL + token, TESTS the connection for real,
                                     # then saves them to .env (the token is never printed)
```

`connect_turso.py` runs a live read + write probe before saving, and transparently falls back
from the `libsql://` (WebSocket) URL to `https://` if the endpoint rejects the WS handshake —
saving whichever scheme actually works. The server **auto-loads `.env`** at startup, so once
it's saved the cloud is used automatically (a real environment variable always wins; disable
with `NEURON_NO_DOTENV=1`). To validate end-to-end against your Turso DB:
`python scripts/smoke_cloud.py`.

For a whole team on one shared DB, see the **[Team guide](docs/TEAM.md)**.

## Seed knowledge (optional — bring your own)

A **seed** is an optional pre-built knowledge base (your notes/docs turned into nodes + 384-dim
vectors) that warm-starts cross-domain suggestions so the AI isn't blank on turn one.

**Neuron ships without a seed** — it works completely fine empty and learns from your
conversations. (We deliberately don't bundle one: a seed is personal, and a bad/placeholder DB
used to crash vector search. The loader now hard-guards against any seed that isn't a real
SQLite file ≥ 512 bytes.) In `Configuration.bat`, **“Seed knowledge DB (what & how)”** walks you
through building one. Manually:

```bash
export NEURON_VAULT=/path/to/vault    # Windows: set NEURON_VAULT=C:\path\to\vault
python scripts/import_vault.py         # -> ./knowledge/base_knowledge.db (local, with vectors)
```

To ship it as the default seed, copy the generated DB to `src/neuron/data/base_knowledge.db` —
**only** a real, populated SQLite file (never a truncated stub). See [docs/DEVELOPER.md](docs/DEVELOPER.md).

## Mounting in an MCP client

Neuron is a **local stdio MCP server**: the client launches it as a subprocess
(`python3 -m neuron`, or `run_mcp.bat` on Windows). Mounting means registering that launch
command. On Windows, `install.ps1` auto-registers **OpenCode**, **Claude Desktop** and
**Cursor**; everything else is a one-time manual entry.

| Client | How to mount | Notes |
|---|---|---|
| OpenCode, Claude Desktop, Cursor | auto-registered by `install.ps1` | restart the client |
| Claude Code, Cline/Roocode, VS Code, Windsurf, Zed, Continue.dev, Cody, Amazon Q | add the launch command | local stdio |
| **Perplexity** (macOS app) | Settings → Connectors → add local MCP (`python3 -m neuron`) | macOS-only; needs the PerplexityXPC helper |
| **ChatGPT / OpenAI** | via an HTTP bridge — see the **[Bridge guide](docs/BRIDGE.md)** | Developer Mode, paid plans; no local stdio |

Per-client JSON snippets live in [`clients/`](clients/) and the full walkthrough is in
**[docs/DEVELOPER.md](docs/DEVELOPER.md#mcp-client-configuration)**. Example, OpenCode
(`~/.config/opencode/opencode.json`):

```json
{
  "mcp": {
    "neuron": {
      "command": ["cmd", "/c", "%LOCALAPPDATA%\\Programs\\neuron\\scripts\\run_mcp.bat"],
      "type": "local"
    }
  },
  "instructions": ["%LOCALAPPDATA%\\Programs\\neuron\\skills\\auto-context.md"]
}
```

The `instructions` field loads the auto-context skill, which tells the model to call
`neuron_pre_turn` at the start of each turn and `neuron_store_turn` after responding.

## Context inheritance

When the active context has no results for a topic, `neuron_get_context` and `neuron_pre_turn`
automatically search parent contexts (e.g. `default`) and annotate the output with
`(from:<parent>)` — so nodes stored in `default` stay reachable from any context.

## MCP tools

| Tool | Description |
|---|---|
| `neuron_pre_turn(topic, keywords)` | **PRE shortcut** — status + compact context in one call |
| `neuron_status` | Graph state (nodes, links, active context) |
| `neuron_get_context(topic, ...)` | Related nodes and links; `format=compact` for injection; inherits from parents |
| `neuron_store_turn` | Save a turn: keywords, links, entities, tags |
| `neuron_confirm(keywords)` | Boost salience of nodes that influenced the response |
| `neuron_auto(text)` | Heuristic extraction + save in one call (fallback for smaller models) |
| `neuron_extract(text)` | Standalone semantic extraction (no save) |
| `neuron_find_candidates(keywords)` | Find similar existing keywords before storing (dedup) |
| `neuron_merge(canonical, aliases)` | Absorb duplicate nodes into one canonical node |
| `neuron_vector_search(keywords)` | Semantic vector search (no link traversal) |
| `neuron_summary` | Top nodes and recent links overview |
| `neuron_forgotten` | Concepts not touched in N turns |
| `neuron_switch_context(context)` / `neuron_list_contexts` | Switch / list domain contexts (e.g. `java/spring`) |
| `neuron_prune` | Force pruning of expired tangential links |
| `neuron_flash` / `neuron_dedup` | Toggle semantic flash / dedup features |
| `neuron_export` / `neuron_reset` | Export the graph as JSON / clear it |

## Development

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v        # unit tests (mock fastembed/mcp/turso — no network)
python -m build                   # wheel + sdist (CI verifies this on every push)
```

Architecture, per-client config, the DB layer, and the cloud/bridge details are in
**[docs/DEVELOPER.md](docs/DEVELOPER.md)**. Release/CI mechanics are in
[docs/RELEASE_PLAN.md](docs/RELEASE_PLAN.md).

## Standalone chat (optional)

A CLI playground (`scripts/run_interactive.py`) can talk to cloud providers — it is **not** the
production path (the MCP server uses a 0-token heuristic by default). Provider keys:
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`.

## License

PolyForm Noncommercial 1.0.0 — see [LICENSE](LICENSE).
