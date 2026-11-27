# INSTALL-AI — Neuron (persistent semantic memory)

> Instructions for an AI agent asked to install Neuron.

**Inside the umbrella repo** (`gray_matter/` and `neurag/` sit next to this
folder): stop here and follow `../gray_matter/INSTALL-AI.md`. It covers every
combination of the suite.

**In the standalone Neuron repo**, read on.

## Why you must not just run the installer

`install.ps1` / `install.sh` ask the human which mode and which embedding
model, with an interactive prompt. When you run them, nobody sees it: the
installer takes the default silently or fails on the prompt. So ask in chat
first, then run non-interactively (`GM_YES=1`) with the answers as flags.

## 1. Ask the user

**What to install** — always ask:

| # | Choice | What the user gets |
|---|---|---|
| 1 | **Neuron + Gray Matter** (recommended) | memory behind the GM gateway, session hooks, GUI; GM is fetched if missing |
| 2 | **Neuron alone** | memory only, registered directly in the MCP clients; no GUI, no bridges |

For the full suite (with NeuRAG too), point the user to the Gray Matter repo
and its `INSTALL-AI.md`.

**Embedding model** — changing it later means re-embedding the whole store:

| # | Model | Size | Note |
|---|---|---|---|
| 1 | `sentence-transformers/paraphrase-multilingual-mpnet-base-v2` | 1.0 GB | multilingual (EN+IT), default: best recall |
| 2 | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | 220 MB | multilingual (EN+IT), light: for a weak PC |

**If an install already exists**, ask: refresh the code (`-Force` /
`--force`), rebuild the venv (`-Clear` / `--clear`), or leave it. Data is kept
in both cases. Never offer a wipe.

## 2. Run

| # | Windows (PowerShell) | macOS / Linux |
|---|---|---|
| 1 | `$env:GM_YES="1"; $env:GM_NO_NEURAG="1"; .\install.ps1 -EmbedModel <model>` | `GM_YES=1 GM_NO_NEURAG=1 sh install.sh --embed-model <model>` |
| 2 | `$env:GM_YES="1"; .\install.ps1 --no-gm -EmbedModel <model>` | `GM_YES=1 sh install.sh --no-gm --embed-model <model>` |

Inside Claude Desktop or another MSIX app on Windows, the installer refuses to
run: see the MSIX section of the Gray Matter `INSTALL-AI.md`.

## 3. Verify

- With GM: `gray-matter doctor` (only gray-matter listed in the clients).
- Alone: `neuron5` is listed in the client's MCP config.

Then tell the user to restart the AI apps.

## Data and rollback

- Graph store: `<data-dir>/neuron/graphs/graph_<context>.db` (never touched by install).
- Optional Turso cloud: credentials in `.env` (see `.env.example`); commented out = local mode.
- Removal: `gray-matter uninstall` (interactive) or restore the `.bak` files.
- Agent usage loop: `pre_turn` before replying, `store_turn` after — full
  playbook in the `skill`/`help` tool.
