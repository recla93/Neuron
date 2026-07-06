# Neuron — Auto-Context Skill

Persistent semantic memory across conversations.
Every exchange leaves traces in a graph; connections accumulate over time,
enriching responses with context that would otherwise be lost between sessions.

Works with any MCP-compatible client: Claude, GPT-4, Gemini, Ollama, or any
OpenAI-compatible endpoint.

---

## Flow Overview

```
User message arrives
      │
      ▼
[PRE]  neuron_pre_turn(topic, keywords)      ← single call: status + compact context
      │   Inject compact context into reasoning before responding
      ▼
[RESPOND]  Generate response
      │
      ▼
[POST]  Extract concepts → neuron_store_turn → neuron_confirm (if context was useful)
```

---

## PRE — Load Context (before every response)

Run at the start of each turn, before generating the response.

### Shortcut — single call (recommended for all providers)
```
neuron_pre_turn(
  topic="<3-5 word summary of user message>",
  keywords=["<2-4 extracted keywords>"],
  max_tokens=200
)
```
Returns status + compact context in one call. Output example:
```
[neuron] ctx=backend turn=14 nodes=42 links=31(active 18)
links:kotlin_flow-[s]->coroutines|spring_boot-[m]->di | nodes:kotlin_flow(22),spring_boot(18)
```
Use this instead of separate `status` + `get_context` calls.

### Alternative — explicit steps (if you need separate control)

**Step 1 — Status check (first turn only)**
```
neuron_status
```
If `turn_count == 0` and `nodes == 0`: graph is empty, skip to RESPOND.

**Step 2 — Load context**
```
neuron_get_context(
  topic="<3-5 word summary of user message>",
  keywords=["<2-4 extracted keywords>"],
  depth=1,
  format="compact",
  max_tokens=150
)
```

**Inject context silently into your reasoning** before composing the response.
Do not show it to the user. Use it as invisible cognitive substrate:
- Referenced nodes → concepts already established in this relationship
- Links → known connections to leverage or extend
- High-salience nodes → what the user cares about most

> **Context inheritance:** if the active context has no results, `get_context`
> and `pre_turn` automatically search parent contexts (e.g. `default`).
> Results are annotated with `(from:<parent>)` when inherited.

### Skip PRE if
- Turn is purely procedural ("ok", "thanks", "yes/no" with no content)
- Graph is empty (first turn ever)

---

## POST — Save Turn (after responding)

Run after generating the response, for significant exchanges only.

### When to save
Save if the exchange introduced:
- New concepts, domain, or intent
- A question answered with substantive content
- A discovered connection worth remembering

**Skip** if the turn was: pure acknowledgement, reformatting, copy-paste,
or a one-word answer with no semantic content.

### Step 1 — Extract concepts

**Native extraction (Claude, GPT-4, Gemini, capable models):**
Extract internally before calling store_turn:

```json
{
  "topic": "main topic in 3-5 words",
  "domain": "free-form label, e.g. AI, backend, frontend, gaming, architecture, general — any works",
  "intent": "question|task|exploration|clarification|feedback",
  "sentiment": "neutral|positive|critical|urgent",
  "keywords": ["kw1", "kw2", "kw3", "kw4"],
  "entities": ["people", "technologies", "frameworks"],
  "tags": ["optional free labels"],
  "links": [
    {
      "source": "new_keyword",
      "target": "existing_keyword",
      "link_type": "cause-effect|analogy|evolution|contrast|deepening|instance-of",
      "weight": "strong|medium|tangential",
      "rationale": "brief explanation in 10-15 words"
    }
  ]
}
```

Keywords: abstract and generalizable. "contextual memory" not "the way you remember things".

Link weights:
- `strong` → same semantic area, direct impact on current reasoning
- `medium` → indirect but useful connection
- `tangential` → speculative, expires after 5 inactive turns

**Fallback (models that cannot do native extraction):**
```
neuron_auto(text="<full user message + assistant response>")
```
This runs heuristic NLP extraction server-side. Less precise but always works.

### Step 2 — Screen for duplicates (optional, recommended for long sessions)
```
neuron_find_candidates(keywords=["kw1", "kw2", "kw3"])
```
If a candidate matches your keyword with high similarity, reuse the existing
keyword name (merge, don't create a duplicate node).

### Step 3 — Store
```
neuron_store_turn(
  topic="...",
  domain="...",
  intent="...",
  sentiment="...",
  keywords=["kw1", "kw2", "kw3", "kw4"],
  entities=["..."],
  tags=["..."],
  links=[{ "source": "...", "target": "...", "link_type": "...", "weight": "...", "rationale": "..." }]
)
```

Minimum viable call (when extraction was quick):
```
neuron_store_turn(topic="...", keywords=["kw1", "kw2"], links=[])
```

### Step 4 — Confirm useful context (optional but valuable)
If the context loaded in PRE directly influenced the response (you referenced
a stored connection, recalled a prior concept, or built on a previous link):
```
neuron_confirm(
  keywords=["kw_that_was_useful", "another_useful_kw"],
  boost=2
)
```
This boosts salience of useful nodes so they surface more prominently next time.
Skip if the PRE context had no impact on this response.

---

## Semantic Flashes (depth 2-3 exploration)

Every 5-7 turns, optionally probe for lateral connections:
```
neuron_get_context(
  topic="<current topic>",
  keywords=["<current keywords>"],
  depth=2,
  format="full",
  max_tokens=300
)
```
Depth 2-3 traverses 2-3 hops in the graph, surfacing unexpected analogies
and cross-domain sparks. Use the result to enrich the response with a
connection the user may not have considered — without forcing it.

---

## Token Budget

| Use case | format | max_tokens |
|---|---|---|
| Standard context injection | compact | 150 |
| Detailed exploration | full | 400 |
| Lateral flash | full | 300 |
| Summary/status | full | 600 |

---

## Smart Activation Rules

| Turn type | PRE | POST |
|---|---|---|
| Substantive exchange | yes | yes |
| Procedural ("ok", "thanks") | skip | skip |
| First turn, empty graph | skip | yes |
| Pure reformatting | yes | skip |
| Domain shift detected | yes | yes (switch_context first) |

---

## Context Switching

If the user's topic shifts sharply to a different domain:
```
neuron_switch_context(context="new_domain")
```
Then run PRE normally for the new context.
Cross-domain links are preserved automatically; the graph inherits from parent contexts.

---

## Available Tools

| Tool | When to use |
|---|---|
| `neuron_pre_turn` | PRE: status + context in one call (recommended shortcut) |
| `neuron_status` | First turn — check if graph has history (if not using pre_turn) |
| `neuron_get_context` | PRE: load context with fine-grained control. Params: `format`, `max_tokens`, `depth` |
| `neuron_store_turn` | POST: save keywords, links, metadata |
| `neuron_confirm` | POST: boost salience of nodes that were actually useful |
| `neuron_auto` | POST fallback: heuristic extraction from raw text |
| `neuron_find_candidates` | POST: screen for duplicate keywords before storing |
| `neuron_vector_search` | Ad-hoc semantic search (no link traversal) |
| `neuron_summary` | Overview of top nodes and recent links |
| `neuron_forgotten` | Find concepts not touched in N turns |
| `neuron_flash` | Enable/disable semantic flash mode |
| `neuron_switch_context` | Switch active domain context |
| `neuron_list_contexts` | List all available contexts |
| `neuron_prune` | Force pruning of expired tangential links |
| `neuron_export` | Export full graph as JSON (analytics only) |
| `neuron_reset` | Clear graph and restart |

---

## Provider Compatibility

This skill is provider-agnostic. The only differences between providers are:
1. whether `neuron_pre_turn` is callable as an MCP tool
2. whether native concept extraction (POST Step 1) is available

### PRE — who uses what

| Client / Provider | Recommended PRE |
|---|---|
| OpenCode | `neuron_pre_turn(topic, keywords)` — single MCP call |
| Claude Desktop | `neuron_pre_turn(topic, keywords)` |
| Claude Code | `neuron_pre_turn(topic, keywords)` |
| Cursor | `neuron_pre_turn(topic, keywords)` |
| Any client with MCP tool access | `neuron_pre_turn(topic, keywords)` |
| Client where pre_turn is unavailable | `neuron_status` → `neuron_get_context(format="compact")` |

`neuron_pre_turn` is available on all standard Neuron MCP server deployments (v3.3+).
It internally calls the same resolution logic as `neuron_get_context` — no difference in
output quality, just one fewer round-trip.

### POST — extraction by model capability

| Provider | Native extraction | Recommended |
|---|---|---|
| Claude (Anthropic) | full | native |
| GPT-4 / GPT-4o (OpenAI) | full | native |
| Gemini 1.5+ (Google) | full | native |
| Ollama local 7B+ | partial | neuron_auto fallback |
| Ollama local <7B | unreliable | neuron_auto |
| Any OpenAI-compatible | depends on model | neuron_auto if unsure |

When in doubt, use `neuron_auto` — it is always safe.

---

## Graph Health (quick reference)

| Signal | Healthy | Warning |
|---|---|---|
| strong+medium ratio | >40% | <20% = links too weak |
| Link type variety | 3+ types | 1 type = instance-of bias |
| Nodes per turn | 3-5 avg | >8 = keywords too granular |
| Pruned/total | <30% | >50% = too much noise |

Check with `neuron_summary` every ~20 turns.
