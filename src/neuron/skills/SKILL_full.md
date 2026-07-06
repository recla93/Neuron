# Neuron v4.0 — Full Skill

Persistent semantic memory across conversations.
Each exchange leaves traces in a concept graph; connections accumulate over
time, enriching responses with context that would otherwise be lost.

> **If your client supports MCP tools** (OpenCode, Claude Desktop, Cursor, etc.),
> use `auto-context.md` instead — it gives you full PRE+POST tool control
> with automatic context inheritance and salience feedback.
> This skill is for clients without MCP tool access, or as a reference.

---

## PHASE 1 — PRE: load context (before every response)

**Option A — MCP tools available (recommended):**
```
neuron_pre_turn(topic="<3-5 word summary>", keywords=["kw1", "kw2"], max_tokens=200)
```
Returns: `[neuron] ctx=<context> turn=N nodes=M links=K(active J)` + compact knowledge.
Inject silently into reasoning. If the active context has no results, `pre_turn`
automatically searches parent contexts (e.g. `default`) — no extra call needed.

**Option B — explicit steps:**
```
neuron_status              # first turn only: check if graph has history
neuron_get_context(topic="...", keywords=[...], format="compact", max_tokens=150)
```

**Option C — no MCP tools:**
Recall internally from prior turns: active keywords, established links, last topic.
Use as invisible cognitive substrate before composing the response.

Skip PRE if the turn is purely procedural ("ok", "thanks", empty ack).

---

## PHASE 2 — EXTRACTION

Analyze the current message and extract internally:

```json
{
  "topic": "main topic in 3-5 words",
  "entities": ["people", "technologies", "frameworks", "concepts"],
  "intent": "question|task|exploration|clarification|feedback",
  "sentiment": "neutral|positive|critical|urgent",
  "domain": "free-form label, e.g. AI, backend, frontend, gaming, architecture, general — any works",
  "keywords": ["kw1", "kw2", "kw3", "kw4"],
  "tags": ["optional free labels"],
  "references": [{"type": "file|url|commit", "path": "...", "description": "..."}]
}
```

Keywords: abstract and generalizable ("contextual memory", not "the way you remember things").
Compound entities ("Kotlin Flow", "Spring Boot") count as keywords when semantically relevant.

---

## PHASE 3 — LINKING

For each semantically related keyword pair, create a link:

```json
{
  "source": "current_keyword",
  "target": "previous_keyword",
  "link_type": "cause-effect|analogy|evolution|contrast|deepening|instance-of",
  "weight": "strong|medium|tangential",
  "rationale": "brief explanation in 10-15 words"
}
```

**Weights:**
- `strong` → same semantic area, direct impact on current reasoning
- `medium` → indirect but useful connection
- `tangential` → weak, expires after 5 inactive turns

**Module M2 — Domain Boost:** links between nodes of the same `domain`
auto-upgrade from `tangential` to `medium`.

**Auto-link thresholds (server-side):** cosine similarity ≥0.65 → strong,
≥0.45 → medium, ≥0.30 → tangential. Top 10 candidates evaluated per keyword.

---

## PHASE 4 — POST: save turn (after responding)

Save if the exchange introduced new concepts, answered a question, or established
a connection worth remembering. Skip for pure acknowledgements.

**If MCP tools are available:**

```
neuron_store_turn(
  topic="...",
  domain="<free-form label, e.g. backend, gaming, general>",
  intent="question|task|exploration|clarification|feedback",
  sentiment="neutral|positive|critical|urgent",
  keywords=["kw1", "kw2", "kw3", "kw4"],
  entities=["entity1", "entity2"],
  tags=["optional"],
  links=[{
    "source": "kw1",
    "target": "kw2",
    "link_type": "deepening",
    "weight": "strong",
    "rationale": "brief explanation"
  }]
)
```

**If context from PRE influenced the response**, boost the useful nodes:
```
neuron_confirm(keywords=["useful_kw1", "useful_kw2"], boost=2)
```
This increases salience so those nodes surface more prominently in future turns.

**If keyword duplicates may exist**, screen first:
```
neuron_find_candidates(keywords=["kw1", "kw2"])
```
Reuse existing keyword names rather than creating near-duplicates.

**If node aliases exist** (e.g. "kotlin" and "kotlin_lang" are the same):
```
neuron_merge(canonical="kotlin", aliases=["kotlin_lang"])
```

**Fallback (small models / no native extraction):**
```
neuron_auto(text="<full user message + assistant response>")
```

**If no MCP tools:** maintain the graph in memory — add nodes, increment
inactivity counters, remove tangential links inactive >5 turns.

---

## PHASE 5 — OUTPUT

Respond normally, semantically enriched by the loaded context.

**Module M4 — Semantic Flashes:** if a strong link exists with a concept
from more than 3 turns ago, internally consider:
```
"This connects back to turn N regarding [previous_topic].
 Enrich the response without forcing the reference."
```

**Module M1 — Tone:** if sentiment shifts sharply toward `urgent`,
adapt the response register accordingly.

Optionally show the link summary at end of response (strong + medium only):
```
> 🧠 Link: ⬤ `source` →(type)→ `target` [strong]
```

---

## Optional Modules

| ID | Name | Description |
|---|---|---|
| M1 | Emotion/Tone | Tracks accumulated sentiment; signals sudden shifts |
| M2 | Domain Boost | Promotes links between nodes of the same domain |
| M3 | Periodic Summary | `neuron_summary` every ~20 turns to compress context |
| M4 | Semantic Flashes | Recalls strong concepts distant in time |
| M5 | Dual Model | Fast model for extraction, main model for response |
| M6 | Salience Score | Ranks nodes by dynamic relevance (always active) |
| M7 | Deduplication | `neuron_merge` to collapse alias nodes |
| M8 | Persistence | SQLite-backed graph survives across sessions (always on) |

---

## MCP Tool Reference

| Tool | Phase | When to use |
|---|---|---|
| `neuron_help` | Discovery | List every command, one line each |
| `neuron_skill(name)` | Discovery | Fetch a full playbook on demand (`auto-context`, `curated`, `base`, `full`) |
| `neuron_pre_turn(topic, keywords)` | PRE | **Recommended**: status + compact context in one call |
| `neuron_status` | PRE | Check graph state (if not using pre_turn) |
| `neuron_get_context(topic, ...)` | PRE | Fine-grained context with depth/format control |
| `neuron_store_turn(...)` | POST | Save turn: keywords, links, entities |
| `neuron_confirm(keywords)` | POST | Boost salience of nodes that influenced the response |
| `neuron_auto(text)` | POST | Heuristic extraction + save (fallback for small models) |
| `neuron_find_candidates(keywords)` | POST | Check for duplicate keywords before storing |
| `neuron_merge(canonical, aliases)` | POST | Absorb alias nodes into a single canonical node |
| `neuron_vector_search(keywords)` | Ad-hoc | Semantic search without link traversal |
| `neuron_summary` | Ad-hoc | Top nodes and recent links overview |
| `neuron_switch_context(context)` | Ad-hoc | Switch domain context (e.g. `java/spring`) |
| `neuron_list_contexts` | Ad-hoc | List all available contexts |
| `neuron_forgotten` | Ad-hoc | Concepts not touched in N turns |
| `neuron_prune` | Ad-hoc | Force pruning of expired tangential links |
| `neuron_flash` / `neuron_dedup` | Ad-hoc | Toggle semantic flash / dedup |
| `neuron_export` / `neuron_reset` | Ad-hoc | Export full graph / clear graph |

---

## Context inheritance

When the active context has no results for a topic, `get_context` and `pre_turn`
automatically walk the parent chain (`java/spring → java → default`).
Results from parent contexts are annotated with `(from:<parent>)`.
No extra configuration needed.

---

## Provider Compatibility

| Provider | PRE | POST extraction | Notes |
|---|---|---|---|
| Claude (Anthropic) | `neuron_pre_turn` | native | full |
| GPT-4 / GPT-4o | `neuron_pre_turn` | native | full |
| Gemini 1.5+ | `neuron_pre_turn` | native | full |
| Ollama 7B+ | `neuron_pre_turn` | partial | use `neuron_auto` if unsure |
| Ollama <7B | `neuron_pre_turn` | unreliable | `neuron_auto` recommended |
| Any OpenAI-compatible | `neuron_pre_turn` | model-dependent | `neuron_auto` if unsure |

---

## Graph health

| Signal | Healthy | Warning |
|---|---|---|
| strong+medium ratio | >40% | <20% = links too weak |
| Link type variety | 3+ types | 1 type = instance-of bias |
| Nodes per turn | 3-5 avg | >8 = keywords too granular |
| Pruned/total | <30% | >50% = too much noise |

Check with `neuron_summary` every ~20 turns.

---

## JSON Export Format

```json
{
  "session_id": "abc12345",
  "turn_count": 12,
  "nodes": [
    {"keyword": "contextual memory", "turn": 1, "topic": "Neuron", "domain": "AI",
     "sentiment": "neutral", "salience": 8}
  ],
  "links": [
    {"source": "cognitive stimuli", "target": "contextual memory",
     "link_type": "evolution", "weight": "strong",
     "rationale": "stimuli evolve the concept of contextual memory",
     "created_turn": 2, "last_active_turn": 8, "inactive_turns": 0}
  ]
}
```
