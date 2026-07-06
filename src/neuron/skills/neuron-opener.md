# Neuron — how to use it (loaded every session)

Neuron is your persistent semantic memory across turns, via the `neuron` MCP tools.
Treat it as a support you WANT to use: load before, curate after. Never announce the
tools to the user — fold what you retrieve silently into your reasoning.

## Per-turn loop (substantive turns only)

1. BEFORE replying — load context:
   `pre_turn(topic="<3-5 word summary>", keywords=["<2-4 concepts>"])`
   Use the returned links/nodes as invisible substrate for your answer.
2. AFTER replying — persist what's new:
   `store_turn(topic=..., keywords=[...], links=[...], domain=..., intent=..., sentiment=...)`
   If the loaded context actually helped, also `confirm(keywords=[...])`.

Skip both on procedural turns (ok / thanks / yes-no); skip `pre_turn` when the graph
is empty. Cheap fallback when you won't curate yourself:
`auto(text="<user message + your reply>")`.

## Curation rules (keep the graph clean)

- **keywords: 3-5 CONCEPT nouns / entities / tech** — never verbs or filler, in any
  language (no `usiamo`, `adottiamo`, `using`, `make`, `via`, `quindi`).
- **links: typed** (`cause-effect | analogy | evolution | contrast | deepening |
  instance-of`) with a short rationale; **never link a keyword to itself**.
- Before storing, screen duplicates with `find_candidates(keywords=[...])` and reuse
  an existing node name instead of creating a near-duplicate.

## More on demand

- Full playbook: call `skill(name="auto-context")` — returns the complete workflow.
  Also `skill(name="curated")` (curation rules), `"base"`, `"full"`.
- `help` — full command list, one line each.
- `status` / `summary` — graph state; `switch_context("domain")` on a sharp topic shift.

Neuron degrades gracefully — if a tool returns nothing, just proceed; never block the
conversation on it.
