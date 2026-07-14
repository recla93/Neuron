"""Tests for the skill-delivery funnel:

  * Fase 0 — the packaged skills (shipped in the wheel under src/neuron/skills)
    stay byte-identical to the editable source at repo-root skills/ (drift guard).
  * Fase 1 — the MCP `instructions` signpost exists, is compact, and is wired
    into InitializationOptions.
  * Fase 2 — the two skills are exposed as MCP resources (neuron://skill/...),
    readable on demand, with a clean error for unknown URIs.

These mirror test_server.py's style: no mocking, `importorskip('mcp')` for the
parts that need the server module, plain filesystem checks for the rest.
"""

import asyncio
import os
import sys

import pytest

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


def _pkg_skill_path(*parts):
    return os.path.join(_project_root, "src", "neuron", "skills", *parts)


def _root_skill_path(*parts):
    return os.path.join(_project_root, "skills", *parts)


# Every skill file, as (path parts). The nested one is the curated-memory SKILL.
SKILL_FILES = [
    ("playbook.md",),
    ("neuron-opener.md",),
    ("neuron-curated-memory", "SKILL.md"),
]


# ---------------------------------------------------------------------------
# Fase 0 — packaged copy stays in sync with the editable root source
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("parts", SKILL_FILES)
def test_packaged_skill_matches_root(parts):
    """The wheel copy under src/neuron/skills must be byte-identical to the
    editable source at repo-root skills/ — so the two never silently diverge."""
    root = _root_skill_path(*parts)
    pkg = _pkg_skill_path(*parts)
    assert os.path.exists(root), f"missing root skill: {root}"
    assert os.path.exists(pkg), f"missing packaged skill: {pkg}"
    with open(root, encoding="utf-8") as a, open(pkg, encoding="utf-8") as b:
        assert a.read() == b.read(), f"packaged skill diverged from root: {parts}"


# ---------------------------------------------------------------------------
# Fase 1 — the signpost
# ---------------------------------------------------------------------------

def test_signpost_present_and_compact():
    pytest.importorskip("mcp")
    from neuron.server import SIGNPOST_BASE, _build_signpost
    assert SIGNPOST_BASE and SIGNPOST_BASE.strip()
    # Token economy: the always-on signpost must stay small (~150 tokens ~= 900 chars).
    assert len(_build_signpost()) < 1000, f"signpost too long: {len(_build_signpost())} chars"
    # It must state the loop and point at the door (the `skill` tool, which the
    # model can actually call — resources aren't reliably model-followable).
    assert "pre_turn" in SIGNPOST_BASE and "store_turn" in SIGNPOST_BASE
    assert "skill(name='playbook')" in SIGNPOST_BASE


def test_signpost_wired_into_init_options():
    """main() passes the dynamic signpost as InitializationOptions.instructions."""
    pytest.importorskip("mcp")
    import inspect
    import neuron.server as srv
    assert "instructions=_build_signpost()" in inspect.getsource(srv.main)


def test_loop_hint_appended_to_plaintext_but_not_json():
    """call_tool wrapper (E2.5b) nudges plain-text tool outputs back onto the
    loop, but must never corrupt a JSON payload (export/consolidate/...)."""
    pytest.importorskip("mcp")
    import json
    from neuron.server import call_tool, _LOOP_HINT

    reset = asyncio.run(call_tool("reset", {}))          # plain text -> gets the nudge
    assert reset[0].text.endswith(_LOOP_HINT)

    export = asyncio.run(call_tool("export", {}))         # JSON -> stays parseable
    assert _LOOP_HINT not in export[0].text
    json.loads(export[0].text)


# ---------------------------------------------------------------------------
# Fase 2 — skills as MCP resources
# ---------------------------------------------------------------------------

def test_list_resources_exposes_all_skills():
    pytest.importorskip("mcp")
    from neuron.server import list_resources, _SKILLS
    resources = asyncio.run(list_resources())
    assert len(resources) == len(_SKILLS) == 2
    uris = {str(r.uri).rstrip("/") for r in resources}
    assert uris == set(_SKILLS.keys())
    for r in resources:
        assert r.mimeType == "text/markdown"
        assert r.name and r.description


def test_read_resource_returns_skill_text():
    pytest.importorskip("mcp")
    from neuron.server import read_resource
    out = asyncio.run(read_resource("neuron://skill/playbook"))
    assert out and out[0].content
    assert out[0].mime_type == "text/markdown"
    assert "Playbook" in out[0].content


def test_read_resource_matches_packaged_file():
    pytest.importorskip("mcp")
    from neuron.server import read_resource
    out = asyncio.run(read_resource("neuron://skill/curated"))
    with open(_pkg_skill_path("neuron-curated-memory", "SKILL.md"), encoding="utf-8") as fh:
        assert out[0].content == fh.read()


def test_read_resource_unknown_uri_raises():
    pytest.importorskip("mcp")
    from neuron.server import read_resource
    with pytest.raises(ValueError):
        asyncio.run(read_resource("neuron://skill/does-not-exist"))


# ---------------------------------------------------------------------------
# The `skill` tool — the model-followable path to the full playbook
# ---------------------------------------------------------------------------

def test_skill_tool_enum_matches_resources():
    """The `skill` tool advertises exactly the skills it can serve. The enum is
    derived from _SKILLS in server.py, so this just guards that derivation."""
    pytest.importorskip("mcp")
    from neuron.server import _SKILL_NAMES, _SKILLS
    assert set(_SKILL_NAMES) == {k.rsplit("/", 1)[1] for k in _SKILLS}


def test_skill_tool_serves_every_declared_name():
    """Every advertised skill name resolves to real content via call_tool — the
    stable, model-facing path (avoids introspecting Tool objects, which vary by
    mcp version). This is what makes the opener's skill(name=...) pointer valid."""
    pytest.importorskip("mcp")
    from neuron.server import call_tool, _SKILL_NAMES
    for name in _SKILL_NAMES:
        out = asyncio.run(call_tool("skill", {"name": name}))
        assert out and out[0].text.strip(), f"skill '{name}' returned nothing"


def test_skill_tool_returns_full_text():
    pytest.importorskip("mcp")
    from neuron.server import call_tool
    out = asyncio.run(call_tool("skill", {"name": "playbook"}))
    assert out and "Playbook" in out[0].text


def test_skill_tool_unknown_name_is_graceful():
    """Unknown skill name must not crash the tool — it returns a helpful message."""
    pytest.importorskip("mcp")
    from neuron.server import call_tool
    out = asyncio.run(call_tool("skill", {"name": "nope"}))
    assert "Unknown skill" in out[0].text
