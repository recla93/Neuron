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

# Compact handshake: the minimal per-turn loop only; the full curation rules
# live on demand in skill(name='playbook') / 'curated'. Keep in sync with the
# other client hooks and server.py's SIGNPOST (funnel.py).
NEURON_HANDSHAKE = (
    "!IMPORTANT [NEURON]: persistent semantic memory is connected - priority "
    "for this session.\n"
    "Each substantive turn: call mcp__neuron5__pre_turn(topic, keywords) BEFORE "
    "replying (fold the returned context in silently), then "
    "mcp__neuron5__store_turn(topic, keywords, links) AFTER, to persist what is new.\n"
    "Keywords = 3-5 concept NOUNS (no verbs/paths); links typed, never a "
    "self-link; before minting a concept check mcp__neuron5__find_candidates; "
    "never store secrets or tokens.\n"
    "Skip on procedural turns (ack/thanks/yes-no) or an empty graph. Once per "
    "session: mcp__neuron5__help (commands), mcp__neuron5__skill(name='playbook') "
    "(full workflow).\n"
    "If no mcp__neuron5__* tools exist here, Neuron is not connected - ignore this silently."
)


def main() -> None:
    print(NEURON_HANDSHAKE)
    sys.exit(0)


if __name__ == "__main__":
    main()
