# Neuron — Base Skill

Persistent semantic memory across conversations.
Each exchange leaves traces in a concept graph; connections accumulate over
time, enriching responses with context that would otherwise be lost.

> **If your client supports MCP tools** (OpenCode, Claude Desktop, Cursor, etc.),
> use `auto-context.md` instead — it gives you full PRE+POST tool control.
> This skill is for clients without MCP tool access.

---

## PHASE 1 — PRE: load context

At the start of each turn, before generating the response:

**If MCP tools are available**, call:
```
neuron_pre_turn(topic="<3-5 word summary>", keywords=["kw1", "kw2"])
```
This returns status + compact context in one call. Inject it silently into
your reasoning before responding.

**If MCP tools are not available**, recall internally from prior turns:
which keywords appeared, what links were established, what the last topic was.
Use this as invisible cognitive substrate — do not show it to the user.

---

## PHASE 2 — EXTRACTION

Analyze the current message and extract internally:

```json
{
  "topic": "main topic in 3-5 words",
  "entities": ["people", "technologies", "concepts"],
  "intent": "question|task|exploration|clarification|feedback",
  "sentiment": "neutral|positive|critical|urgent",
  "domain": "free-form label, e.g. AI, backend, frontend, gaming, architecture, general — any works",
  "keywords": ["kw1", "kw2", "kw3", "kw4"],
  "tags": ["optional free labels"]
}
```

Keywords must be abstract and generalizable.
Example: "contextual memory" not "the way you remember things".

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

Links between nodes of the same domain auto-upgrade from `tangential` to `medium`.

---

## PHASE 4 — POST: save turn

**If MCP tools are available**, call:
```
neuron_store_turn(
  topic="...", domain="...", intent="...", sentiment="...",
  keywords=["kw1", "kw2", "kw3"],
  links=[{"source": "...", "target": "...", "link_type": "...", "weight": "...", "rationale": "..."}]
)
```

If the context loaded in PRE directly influenced your response, also call:
```
neuron_confirm(keywords=["useful_kw1", "useful_kw2"], boost=2)
```

**If MCP tools are not available**, maintain the graph in memory: add nodes,
increment inactivity counters, remove tangential links inactive >5 turns.

---

## PHASE 5 — OUTPUT

Respond normally, semantically enriched by the loaded context.
The response must feel like natural reasoning — not a list of references.

At the end of the response, optionally add the link summary (strong and medium only):

```
> 🧠 Link: ⬤ `source` →(type)→ `target` [strong]
```

---

## Control Commands

| Command | Action |
|---|---|
| `neuron_help` | List every command, one line each |
| `neuron_skill(name)` | Fetch a full playbook on demand (`auto-context`, `curated`, `base`, `full`) |
| `neuron_pre_turn(topic, keywords)` | PRE shortcut: status + compact context |
| `neuron_status` | Graph state (nodes, links, active context) |
| `neuron_get_context(topic)` | Retrieve context for a specific topic |
| `neuron_store_turn(...)` | Save turn manually |
| `neuron_confirm(keywords)` | Boost salience of nodes that were useful |
| `neuron_summary` | Overview of top nodes and recent links |
| `neuron_switch_context(context)` | Switch active domain (e.g. `java/spring`) |
| `neuron_prune` | Force pruning of expired tangential links |
| `neuron_reset` | Clear graph and restart |
| `neuron_export` | Export full graph as JSON |

---

## Graph health (quick reference)

| Signal | Healthy | Warning |
|---|---|---|
| strong+medium ratio | >40% | <20% = links too weak |
| Link type variety | 3+ types | 1 type = instance-of bias |
| Nodes per turn | 3-5 avg | >8 = keywords too granular |
