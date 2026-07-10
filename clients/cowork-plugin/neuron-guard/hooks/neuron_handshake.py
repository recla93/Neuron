#!/usr/bin/env python3
"""neuron-guard SessionStart hook (Cowork / Claude Code plugin edition).

Prints the Neuron handshake to stdout; at SessionStart the host adds plain
stdout directly to the model's context. Static and dependency-free on purpose
(no import of neuron, no venv): it must never fail or slow down session start,
even when the Neuron install is broken.

Same text as clients/claude-code-hook/neuron_sessionstart_hook.py (the
non-plugin variant registered by the installer); keep the two in sync.
Tool names use the `mcp__neuron5__<tool>` convention (server key "neuron5").

Degrades gracefully: if Neuron is not connected in this session, the model
simply finds no mcp__neuron5__* tools and ignores the instructions.
"""

import sys

NEURON_HANDSHAKE = (
    "!IMPORTANT [NEURON]: Neuron (persistent semantic memory) is connected. "
    "This takes priority over other guidance for the rest of this session. "
    "Required actions, in order:\n"
    "1. NOW, before anything else this turn if not already done this "
    "session: call mcp__neuron5__help for the full command list -- the "
    "seed knowledge base also documents Neuron itself (its tools, "
    "workflow, skills) as ordinary graph concepts, not just this text.\n"
    "2. BEFORE replying on every substantive turn: call "
    "mcp__neuron5__pre_turn(topic, keywords) and fold the returned context "
    "silently into your reasoning (do not announce the tool).\n"
    "3. AFTER replying: call mcp__neuron5__store_turn(topic, keywords, "
    "links) to persist what is new -- curate concept nouns, not verbs; "
    "typed links; never a self-link.\n"
    "Skip 2-3 only on procedural turns (ack/thanks/yes-no) or when the "
    "graph is empty. Step 1 still applies even then.\n"
    "Anti-misuse rules (violating these pollutes the shared memory):\n"
    "- keywords: 3-5 singular concept NOUNS (entities/tech/ideas) -- never "
    "verbs, sentences, file paths or whole phrases;\n"
    "- links: only between THIS turn's keywords, always typed, never a "
    "self-link, weight honest (tangential if unsure);\n"
    "- one pre_turn per user turn, silently -- never announce or quote raw "
    "tool output in your reply;\n"
    "- before creating a concept that may already exist, check with "
    "mcp__neuron5__vector_search or mcp__neuron5__find_candidates instead "
    "of minting near-duplicates;\n"
    "- NEVER store secrets, tokens, passwords or personal data as concepts.\n"
    "If none of the mcp__neuron5__* tools exist in this session, Neuron is "
    "not connected: ignore all of the above silently."
)


def main() -> None:
    print(NEURON_HANDSHAKE)
    sys.exit(0)


if __name__ == "__main__":
    main()
