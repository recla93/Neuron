# INSTALL-AI — Neuron (persistent semantic memory)

> Instructions for an AI agent. Neuron can run standalone, but the recommended
> model is behind the Gray Matter gateway (bundled with every install).
> Versione italiana: `INSTALL-AI.it.md`.

## Recommended path (via Gray Matter)

```bash
pip install -e .                       # from the Neuron root
pip install -e ../gray_matter          # the gateway, if present in the checkout
gray-matter install --dry-run
gray-matter install                    # registers ONLY gray-matter in clients
gray-matter doctor                     # verify: only gray-matter listed
```

Session hooks (claude-code/cowork/opencode) are deployed by `gray-matter
install`; other clients get the MCP `instructions` at handshake.

## Standalone (without Gray Matter)

```bash
pip install -e .
neuron register                        # registers neuron5 in detected clients
python -m pytest tests -q              # expect all green
```

Restart your AI apps after registration.

## Data and rollback

- Graph store: `<data-dir>/neuron5/graphs/graph_<context>.db` (never touched by install).
- Optional Turso cloud: credentials in `.env` (see `.env.example`); commented out = local mode.
- Deregistration: `gray-matter uninstall` (interactive) or restore the `.bak` files.
- Agent usage loop: `pre_turn` before replying, `store_turn` after — full
  playbook in the `skill`/`help` tool.
