# neuron-guard

Correct-usage guard for the [Neuron](https://github.com/) semantic memory MCP
server (v5 "Synapse", server key `neuron5`).

## What it does

- **SessionStart hook** — at every session start/resume/clear/compact, injects
  the Neuron handshake into context: the per-turn loop
  (`help` → `pre_turn` → reply → `store_turn`) plus the anti-misuse rules
  (curated concept-noun keywords, typed links, no self-links, no secrets,
  dedup before minting near-duplicate concepts). This is a host-guaranteed
  delivery path: the MCP `instructions` field is optional for clients, a
  registered hook's stdout is not.
- **Skill `neuron-usage`** — an on-demand playbook Claude loads when working
  with Neuron memory (storing, recalling, curating), with the full curation
  rules and a common-mistakes table.

## Requirements

- Neuron installed and registered as an MCP server under the key `neuron5`
  (use the Neuron installer / `neuron register`). The plugin does NOT bundle
  the server; if Neuron is not connected, the hook text tells the model to
  ignore itself, so the plugin is harmless.
- `python` on PATH (already required by Neuron itself).

## Install

Cowork: Settings → Plugins → install `neuron-guard.plugin`.

## Relation to the non-plugin hooks

The same handshake is also deployable without this plugin:
`clients/claude-code-hook/neuron_sessionstart_hook.py` (registered in
`~/.claude/settings.json` by the installer) and
`clients/opencode-plugin/neuron-handshake.mjs` for OpenCode. Keep the three
texts in sync — single source of truth is the anti-misuse wording in
`clients/claude-code-hook/neuron_sessionstart_hook.py`.
