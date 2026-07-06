---
name: neuron-curated-memory
description: >
  Use Neuron (the semantic-memory MCP server) the RIGHT way so its graph stays
  clean and useful. Before answering, load prior context; after answering, save a
  CURATED turn — 3-5 concept keywords (nouns/entities/tech, never verbs or filler),
  typed links with a short rationale, and never a self-link. This raises graph
  quality, keeps memory across sessions, and saves the user tokens by making every
  future turn better-grounded. Use it whenever the `neuron` MCP tools are available
  (Claude Desktop/Code, Cursor, VS Code, OpenCode, Zed, or the ChatGPT bridge).
---

# Neuron — Curated Memory

Neuron is **your** persistent semantic memory: a concept graph that survives across
sessions. It exists to help *us evolve* — it saves the user tokens by raising the
quality of what's remembered, and it helps you (the model) process better by
grounding each answer in what came before instead of starting cold.

Treat Neuron as a **support you WANT to use**, not a logging chore. But memory is
only as good as what you put in it: **garbage in → a graph full of verbs and
noise → useless recall.** Curate.

## The loop (every meaningful turn)

```
1. BEFORE answering → load context
      get_context(topic="<3-5 word summary>")      ← prefer this
2. ANSWER using that context (silently — don't announce the tool)
3. AFTER answering → save a CURATED turn
      store_turn(keywords=[...], topic=..., links=[...], ...)
4. If the loaded context actually helped → confirm(keyword="<the useful one>")
```

- **Prefer `get_context(topic=…)` over `pre_turn`.** `pre_turn` over-weights recency;
  `get_context` gives you the topically-relevant slice.
- **Prefer curated `store_turn` over `auto`.** `auto` is the cheap 0-token fallback
  (fine for throwaway chatter); for anything worth remembering, YOU pick the concepts —
  you already understood the message, so curation costs almost nothing and keeps the
  graph clean.
- Not every turn deserves a write. Skip greetings, acknowledgements, and pure
  clarifications. Save turns that carry a decision, a fact, an entity, or a lesson.

## Curation rules (the whole point)

**Keywords — 3 to 5, CONCEPTS only:**
- ✅ nouns, entities, technologies, domain terms: `fastapi`, `redis`, `latenza`,
  `postgres`, `webhook`, `oauth`, `dashboard`.
- ❌ NEVER verbs or filler — in any language. Especially Italian "noi" forms:
  `usiamo`, `riduciamo`, `disegniamo`, `adottiamo`, `passiamo`, `gestiamo`; and
  connectors like `via`, `quindi`, `inoltre`, `using`, `adding`, `make`.
- Keep entities explicit (`stripe`, not "the payment thing"). Lowercase unless it's a
  proper name that matters.

**Links — typed, meaningful, never self:**
- Give each link a `link_type` and a short `rationale` (≤ ~8 words).
- **Never link a keyword to itself** (`react → react`) — including case variants.
- Only link things that are genuinely related; don't manufacture links to look busy.

**Before writing, screen for duplicates:**
- `find_candidates(keywords=[...])` to reuse an existing node instead of creating a
  near-duplicate (`postgres` vs `postgresql`).

## Example

> User (IT): "Usiamo FastAPI con Redis per ridurre la latenza; adottiamo indici e
> passiamo a Postgres per le query lente."

- ❌ Bad: `keywords=["usiamo","fastapi","riduciamo","adottiamo","passiamo"]`
- ✅ Good:
  ```
  store_turn(
    topic="api latency optimization",
    keywords=["fastapi", "redis", "latenza", "postgres", "indici"],
    domain="architecture", intent="task",
    entities=["FastAPI", "Redis", "Postgres"],
    links=[
      {"source":"redis","target":"latenza","link_type":"deepening","rationale":"cache cuts latency"},
      {"source":"postgres","target":"query-lente","link_type":"causal","rationale":"slow queries → indexes"}
    ]
  )
  ```

## Discoverability

Run the **`help`** tool for the full command reference (one line each), and
**`status`** for the current graph state. Neuron degrades gracefully — if a tool
returns nothing, just proceed; never block the conversation on it.

**Bottom line:** load before, curate after, concepts-not-verbs, no self-links. That's
how Neuron stays a memory worth having.
