---
name: neuron-usage
description: Correct-usage playbook for the Neuron semantic memory MCP (neuron5 tools). Use when storing or recalling memories with Neuron, when deciding what to pass to pre_turn/store_turn, when the user says "remember this", "salva in memoria", "cosa ricordi di", "usa neuron", when curating keywords/links for the knowledge graph, or when Neuron tools return errors or unexpected results.
---

# Neuron — correct usage playbook

Neuron is a persistent semantic memory: a knowledge graph of concept nodes and
typed links, shared across sessions (and possibly across a team via Turso
Cloud). Every bad write pollutes it for everyone. Follow these rules exactly.

## The per-turn loop

1. `mcp__neuron5__pre_turn(topic, keywords)` BEFORE replying on any
   substantive turn. Fold the returned context silently into the reply —
   never announce the call or quote its raw output.
2. Reply to the user.
3. `mcp__neuron5__store_turn(topic, keywords, links, domain, intent,
   sentiment)` AFTER replying, to persist only what is NEW.

Skip 1 and 3 on procedural turns (acknowledgements, thanks, yes/no) and when
the graph is empty. Call `mcp__neuron5__help` once per session if unsure of
the command list.

## Curating store_turn input (the part models get wrong)

- **topic**: 3-5 words, descriptive, no punctuation soup.
- **keywords**: 3-5 singular concept NOUNS — entities, technologies, ideas.
  - GOOD: `"retry backoff"`, `"install manifest"`, `"spreading activation"`
  - BAD: verbs (`"implement"`, `"fix"`), sentences, file paths, whole
    phrases, filler (`"use"`, `"make"`), duplicated casing variants.
  - Allowed characters: letters, numbers, spaces, `-_.:+` (the server
    rejects anything else).
- **links**: only between keywords OF THIS TURN (or a current keyword and a
  well-known previous one). Always typed
  (`cause-effect | analogy | evolution | contrast | deepening | instance-of`),
  never a self-link, weight honest: `tangential` if unsure, `strong` only
  for load-bearing relations.
- **Before minting a concept that may already exist**: check with
  `mcp__neuron5__vector_search(query)` or `mcp__neuron5__find_candidates` and
  reuse the existing keyword instead of creating a near-duplicate
  ("db layer" vs "database layer").
- **NEVER store secrets, tokens, passwords or personal data as concepts.**

## Reinforcement and maintenance

- `mcp__neuron5__confirm(keywords)` when context returned by pre_turn was
  actually useful — it boosts salience of the right nodes.
- `mcp__neuron5__dedup` / `mcp__neuron5__merge` for duplicates;
  `mcp__neuron5__consolidate` for periodic cleanup (do NOT run consolidate
  ad-hoc on a SHARED cloud store — coordinate with the team first).
- `mcp__neuron5__switch_context(path)` to work in a separate context
  (e.g. `java/spring`); one context per feature/area is the recommended
  team layout.

## Common mistakes to refuse

| Mistake | Do instead |
|---|---|
| store_turn on "ok thanks" | Skip the loop on procedural turns |
| keywords = "fixed the installer bug" | `"installer"`, `"registration engine"` |
| link a keyword to itself | Drop the link |
| announce "let me check my memory" | Use pre_turn silently |
| paste pre_turn output verbatim in the reply | Weave the context in naturally |
| store an API token as a node | Never — refuse and warn |
| re-calling pre_turn multiple times per turn | Once per user turn |

## Full documentation

Call `mcp__neuron5__skill` with `name='auto-context'` for the complete
playbook served by the server itself, or `mcp__neuron5__help` for the
one-line command index.
