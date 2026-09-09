#!/usr/bin/env python3
"""UserPromptSubmit / PreCompact nudge: "you have not saved in a while".

WHY THIS EXISTS
---------------
The memory loop is self-referential: pre_turn's reply says "then store_turn",
store_turn's reply says "next: pre_turn". Every reminder lives INSIDE the reply
of the previous link, so skipping one link also skips the reminder for the next
one. Observed 2026-09-04: one store_turn in a ~15-turn session, and the loop
never restarted, because nothing outside it could restart it.

UserPromptSubmit fires on every real user message, whatever the model chose to
call. That is the external trigger the cycle lacks.

NO COUNTER FILE
---------------
The transcript Claude Code hands us on stdin IS the counter: count the real
user prompts since the last store_turn tool_use. One source of truth, nothing
to reset, nothing to keep in sync with the MCP server (a hook cannot see MCP
calls, but it can read the log of them).

Read STRUCTURALLY, not by substring. The first cut matched '"type":"user"' and
'"name":"..store_turn"' as text: it worked on a real transcript (compact JSONL)
and broke on every fixture, because the separators are not part of any
contract. Worse, a bare `store_turn` substring is reset by a user TYPING the
word -- and the sessions that talk about the memory loop are exactly the ones
that must not silence it. json.loads per line costs milliseconds on a session
of thousands of lines.

Three kinds of role=user line are NOT a turn of the user: tool results, agent
sidechains, and meta entries (hook output injected back into context -- this
hook's own output included, which would otherwise count itself).

Fail-silent by design: no transcript, no reminder. This is a nudge, not a
safety net, and a wrong nudge every turn teaches the model to ignore it.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from neuron_sessionstart_hook import installed_slugs, owner  # noqa: E402

EVERY = 8                                   # prompts between reminders


def _blocks(entry):
    msg = entry.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _saves(entry) -> bool:
    """True if this assistant entry actually CALLED store_turn."""
    return any(b.get("type") == "tool_use"
               and str(b.get("name", "")).endswith("store_turn")
               for b in _blocks(entry))


def _is_user_turn(entry) -> bool:
    """True for a prompt the human actually typed."""
    if entry.get("type") != "user" or entry.get("isSidechain") or entry.get("isMeta"):
        return False
    return not any(b.get("type") == "tool_result" for b in _blocks(entry))


def unsaved_prompts(transcript: str) -> int:
    """Real user prompts logged after the last store_turn call. -1 if unknown."""
    try:
        lines = Path(transcript).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return -1
    n = 0
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        if _saves(entry):
            n = 0
        elif _is_user_turn(entry):
            n += 1
    return n


def message(n: int, prefix: str) -> str:
    return ("[neuron] %d turni dall'ultimo store_turn. Se c'e' qualcosa di nuovo "
            "in questa sessione, chiama %sstore_turn(topic, keywords, links) "
            "prima di continuare." % (n, prefix))


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (OSError, ValueError):
        sys.exit(0)
    if not isinstance(payload, dict):
        sys.exit(0)

    slug = owner(installed_slugs())
    if slug not in ("gray-matter", "neuron"):
        sys.exit(0)
    prefix = "mcp__%s__" % slug

    n = unsaved_prompts(payload.get("transcript_path") or "")
    event = payload.get("hook_event_name", "")
    if event == "PreCompact":
        # Last chance before the context is squeezed: nudge unless we just saved.
        if n != 0:
            print(message(max(n, 0), prefix))
    elif n > 0 and n % EVERY == 0:
        print(message(n, prefix))
    sys.exit(0)


if __name__ == "__main__":
    main()
