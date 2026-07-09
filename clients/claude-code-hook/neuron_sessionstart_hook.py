#!/usr/bin/env python3
"""Neuron SessionStart hook for Claude Code.

Registered by scripts/neuron5-config.ps1 (Install-ClaudeCodeSessionHook) in
~/.claude/settings.json under hooks.SessionStart, for the "startup", "resume",
"clear" and "compact" matchers. Claude Code runs this script and adds its
stdout directly to context at the start of the conversation (SessionStart is
one of the hook events where plain stdout, not just the hookSpecificOutput
JSON, is accepted as context -- see docs.claude.com/en/docs/claude-code/hooks).

Why this exists: the MCP `instructions` field (Neuron's dynamic SIGNPOST, see
server.py) is host-optional -- nothing in the MCP spec obligates a client to
surface it to the model. This hook is a second, independent delivery path for
the same handshake reminder, using a mechanism Claude Code documents and
guarantees (a registered hook's stdout at SessionStart). Kept as a static,
self-contained string (no import of neuron / no venv dependency) so it never
fails or slows down session start, even if the Neuron install is broken.

Tool names below use Claude Code's `mcp__<server>__<tool>` convention for a
server registered under the key "neuron5" in ~/.claude.json.
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
    "graph is empty. Step 1 still applies even then."
)


def main() -> None:
    print(NEURON_HANDSHAKE)
    sys.exit(0)


if __name__ == "__main__":
    main()
