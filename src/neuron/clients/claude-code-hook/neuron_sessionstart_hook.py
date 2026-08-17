#!/usr/bin/env python3
"""SessionStart handshake for the Gray Matter suite (Claude Code / Cowork).

Claude Code runs this at session start and puts its stdout straight into the
model's context (SessionStart is one of the hook events where plain stdout is
accepted as context -- docs.claude.com/en/docs/claude-code/hooks). The MCP
`instructions` field is host-optional: nothing in the spec obliges a client to
show it. This hook is the second, guaranteed delivery path for the same
reminder.

WHO SPEAKS
----------
Exactly one tool owns the handshake, decided HERE, at session start, not at
install time:

    gray-matter installed?  -> Gray Matter speaks (full suite, or GM + a peer)
    else neuron?            -> Neuron speaks     (standalone)
    else neurag?            -> NeuRAG speaks     (standalone)
    else                    -> say nothing

Deciding at deploy time is what broke before: every installer deployed its own
hook, so a full suite opened each session with TWO handshakes, and the blunt fix
was to empty the Cowork plugin's hooks.json. Now all three tools deploy THIS
file to the SAME path -- deploying twice is idempotent -- and a single owner is
resolved at runtime. The double handshake cannot happen by construction.

HOW MANY TIMES
--------------
Owner-resolution answers WHO speaks, not HOW OFTEN. Idempotence-by-path only
holds inside one channel: the Cowork plugin registers this same script from
${CLAUDE_PLUGIN_ROOT}, a different path from ~/.claude/hooks, so a machine with
both channels wired ran it TWICE and the session opened with the identical
block repeated -- observed 2026-08-03. Each process sees only itself, so no
amount of runtime owner-resolution can dedupe them.

So the guard is a session-scoped claim: the first process to create the marker
for this session_id speaks, the others exit silently. O_EXCL makes the claim
atomic, so two hooks racing at startup cannot both win. Fail-open by design --
if anything at all goes wrong (no stdin, no session_id, unwritable temp) we
speak, because a handshake said twice costs tokens while a handshake never said
costs the whole memory loop.

It also fixes the mirror bug: the tool-name prefix used to be hardcoded to
`mcp__gray-matter__`, so a STANDALONE install told the model to call tools that
do not exist in that session (exactly the old `mcp__neuron5__*` failure, in the
other direction).

WHEN THE TOOLS ARE NOT LOADED YET
---------------------------------
The escape clause fired on the wrong condition. Some clients defer MCP tool
schemas -- the tools are present, but a call made before the schema is loaded
fails -- while the closing line said that no reachable tool means memory is not
connected. So the first failed call of the session and the permission to ignore
the loop arrived together, at the worst possible moment.

"Not loaded" and "not there" are now two different things: load the schema and
retry once, and only a tool list with no entry at all silences the loop. The
retry is capped at one because a genuinely absent tool must stay cheap.

Measured while writing this, on a session that carried this block in context
from its first turn: pre_turn 2, store_turn 2, across fourteen substantive
turns.

WHY IT CANNOT BREAK YOUR SESSION
--------------------------------
stdlib only, and it never imports neuron / neurag / gray_matter: the registry is
plain JSON on disk. A broken install, a half-written venv or a missing registry
costs a silent no-op, never a slow or failed session start.

KEEP IN SYNC: byte-identical copies live in the two tools that ship client
assets (neuron/src/neuron/clients/ and neurag/clients/, each with a Claude Code
and a Cowork copy). Gray Matter ships none -- it deploys theirs.
`test_handshake.py` fails if they drift.
"""

import json
import os
import sys
from pathlib import Path

# Registry priority == ownership. Keep in sync with gray_matter/gme.py.
_PRIORITY = ("gray-matter", "neuron", "neurag")


def _gme_root() -> Path:
    """Mirror of gray_matter.gme.gme_root() -- deliberately NOT imported.

    I JSON dei tool stanno in ``<base>/GrayMatterEnvironment/registry``: il
    registro e' sceso di un livello quando GrayMatterEnvironment/ e' diventata
    la radice unica della suite (ci vivono anche neuron/, neurag/, graymatter/).
    Questo mirror era rimasto al layout PIATTO di prima, quindi `installed_slugs`
    globbava una cartella di sole sottocartelle e tornava sempre vuoto: owner()
    = None e l'handshake non e' mai partito su nessuna macchina col layout
    nuovo. Verificato su installazione reale. Come in gme_root(), un registro
    ESISTENTE vince: si accetta ancora il piatto per le installazioni vecchie.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", "")
    else:
        base = os.environ.get("XDG_DATA_HOME", "") or os.path.join(
            os.path.expanduser("~"), ".local", "share")
    if not base:                       # scrubbed env -> never a relative path
        base = os.path.expanduser("~")
    suite = Path(base) / "GrayMatterEnvironment"
    current = suite / "registry"
    if current.is_dir():
        return current
    if suite.is_dir() and any(suite.glob("*.json")):
        return suite               # registro piatto pre-suite
    return current


def installed_slugs() -> set:
    """Slugs the GME registry reports as installed. Never raises."""
    out = set()
    try:
        for f in _gme_root().glob("*.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue
            if isinstance(d, dict) and d.get("status") == "installed" and d.get("key"):
                out.add(d["key"])
    except OSError:
        pass
    return out


def owner(installed=None):
    """The one tool that speaks this session, or None."""
    have = installed_slugs() if installed is None else set(installed)
    for slug in _PRIORITY:
        if slug in have:
            return slug
    return None


def _memory_block(p: str, with_knowledge: bool) -> str:
    extra = (
        f"Knowledge base: {p}knowledge_query(query) when the question touches "
        "indexed material.\n"
    ) if with_knowledge else ""
    return (
        "!IMPORTANT [NEURON]: persistent semantic memory is connected - priority "
        "for this session.\n"
        f"Each substantive turn: call {p}pre_turn(topic, keywords) BEFORE replying "
        f"(fold the returned context in silently), then {p}store_turn(topic, "
        "keywords, links) AFTER, to persist what is new.\n"
        "Keywords = 3-5 concept NOUNS (no verbs/paths); links typed, never a "
        f"self-link; before minting a concept check {p}find_candidates; never "
        "store secrets or tokens.\n"
        + extra +
        "Skip on procedural turns (ack/thanks/yes-no) or an empty graph. Once per "
        f"session: {p}help (commands), {p}skill(name='playbook') (full workflow).\n"
        "If a call fails because the tool is not loaded, load its schema and retry "
        f"once - some clients defer MCP tools. Only a tool list with no {p}* entry "
        "at all means memory is not connected: then ignore this silently."
    )


def _knowledge_block(p: str) -> str:
    return (
        "!IMPORTANT [NEURAG]: a knowledge base is connected - use it before "
        "answering from memory.\n"
        f"Search it with {p}knowledge_query(query) when the question touches "
        "indexed material; cite what you used.\n"
        f"Once per session: {p}skill(name='usage') for the retrieval workflow "
        "(chunking, filters, when NOT to search).\n"
        "If a call fails because the tool is not loaded, load its schema and retry "
        f"once - some clients defer MCP tools. Only a tool list with no {p}* entry "
        "at all means the knowledge base is not connected: then ignore this silently."
    )


def handshake(slug: str, installed=None) -> str:
    """The reminder, with the prefix that actually exists in THIS session.

    The OWNER decides the prefix: under the gateway model the client registers
    only "gray-matter" and the peer tools are served through it pass-through.

    The CAPABILITIES decide the text: Gray Matter with only NeuRAG beside it
    must not announce a memory loop that has no server behind it. Announcing a
    capability that is not installed is the same class of bug as announcing the
    wrong prefix -- the model calls a tool that is not there.
    """
    p = "mcp__%s__" % slug
    have = set(installed) if installed is not None else {slug}
    if slug != "gray-matter":
        return _knowledge_block(p) if slug == "neurag" else _memory_block(p, False)

    mem, know = "neuron" in have, "neurag" in have
    if mem:
        return _memory_block(p, know)
    if know:
        return _knowledge_block(p)
    return ""          # gateway with no peers: nothing to push


def _session_id() -> str:
    """The session id Claude Code hands the hook on stdin, or '' if unreadable.

    Never blocks and never raises: stdin is already closed or already filled by
    the host when a hook runs, and a missing id simply means no claim is made.
    """
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return ""
        payload = json.loads(sys.stdin.read() or "{}")
        sid = payload.get("session_id") if isinstance(payload, dict) else None
        return str(sid) if sid else ""
    except (OSError, ValueError, AttributeError):
        return ""


def claim(session_id: str, tmpdir=None) -> bool:
    """True if THIS process is the one that speaks for ``session_id``.

    Atomic via O_EXCL: of two hooks racing at session start, exactly one
    creates the marker and the loser stays quiet. Fail-open — an empty id or an
    unwritable temp dir returns True, because the cost of speaking twice is
    tokens and the cost of never speaking is the whole loop.
    """
    if not session_id:
        return True
    import tempfile
    base = Path(tmpdir) if tmpdir else Path(tempfile.gettempdir())
    marker = base / ("neuron-handshake-%s" % "".join(
        c for c in session_id if c.isalnum() or c in "-_")[:64])
    try:
        fd = os.open(str(marker), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        return True
    except FileExistsError:
        return False
    except OSError:
        return True


def main() -> None:
    have = installed_slugs()
    slug = owner(have)
    if slug and claim(_session_id()):
        text = handshake(slug, have)
        if text:
            print(text)
    sys.exit(0)


if __name__ == "__main__":
    main()
