# Neuron — notes for coding agents

Neuron is the persistent semantic memory of the Gray Matter suite: an MCP
server that keeps a graph of concepts (nodes, typed links, salience, trust,
decay, episodes) across sessions.

## Where to change it

Develop in the umbrella repo, **recla93/GrayMatterEnvironment**, folder
`neuron/`. The standalone `recla93/Neuron` repo is a read-only mirror: its
`main` receives only the sync job, and a pull request opened there is not built.

## Layout

| Path | What |
|---|---|
| `src/neuron/server.py` | MCP server: tool definitions and handlers (`store_turn`, `pre_turn`, ...) |
| `src/neuron/models.py` | The graph: nodes, links, episodes, decay, save |
| `src/neuron/db.py` | Storage tiers: Turso cloud → embedded Turso → stdlib sqlite3 |
| `src/neuron/search.py` | Embeddings (`_embed_one` normalizes to unit length) and vector search |
| `src/neuron/curation.py` | The keyword gate of `store_turn` |
| `src/neuron/registry.py` | One graph per context |
| `src/neuron/clients/` | Session hooks and plugins for the MCP clients, shipped in the wheel |
| `tests/` | The suite; `tests/_mockdeps.py` fakes `mcp`/`fastembed` for light tests |

## Rules

- Tests: `python -m pytest neuron/tests -q` from the umbrella root, never
  together with the other two suites in one process.
- Line endings: LF, except `.ps1`/`.cmd`/`.bat` (CRLF). On Windows pass
  `newline="\n"` to `write_text`.
- Manual checks: `NEURON_NO_DOTENV=1` and `NS_GRAPHS_DIR=<tmp dir>`, so nothing
  touches a real memory or the shared cloud DB.
- The full environment rules are in the umbrella's `ENVIRONMENT.md`.
