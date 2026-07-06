"""Tests for the skill-delivery funnel:

  * Fase 0 — the packaged skills (shipped in the wheel under src/neuron/skills)
    stay byte-identical to the editable source at repo-root skills/ (drift guard).
  * Fase 1 — the MCP `instructions` signpost exists, is compact, and is wired
    into InitializationOptions.
  * Fase 2 — the four skills are exposed as MCP resources (neuron://skill/...),
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
    ("auto-context.md",),
    ("SKILL_base.md",),
    ("SKILL_full.md",),
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
    from neuron.server import SIGNPOST
    assert SIGNPOST and SIGNPOST.strip()
    # Token economy: the always-on signpost must stay small (~150 tokens ~= 900 chars).
    assert len(SIGNPOST) < 1000, f"signpost too long: {len(SIGNPOST)} chars"
    # It must state the loop and point at the door.
    assert "pre_turn" in SIGNPOST and "store_turn" in SIGNPOST
    assert "neuron://skill/auto-context" in SIGNPOST


def test_signpost_wired_into_init_options():
    """main() passes SIGNPOST as InitializationOptions.instructions."""
    pytest.importorskip("mcp")
    import inspect
    import neuron.server as srv
    assert "instructions=SIGNPOST" in inspect.getsource(srv.main)


# ---------------------------------------------------------------------------
# Fase 2 — skills as MCP resources
# ---------------------------------------------------------------------------

def test_list_resources_exposes_all_skills():
    pytest.importorskip("mcp")
    from neuron.server import list_resources, _SKILLS
    resources = asyncio.run(list_resources())
    assert len(resources) == len(_SKILLS) == 4
    uris = {str(r.uri).rstrip("/") for r in resources}
    assert uris == set(_SKILLS.keys())
    for r in resources:
        assert r.mimeType == "text/markdown"
        assert r.name and r.description


def test_read_resource_returns_skill_text():
    pytest.importorskip("mcp")
    from neuron.server import read_resource
    out = asyncio.run(read_resource("neuron://skill/auto-context"))
    assert out and out[0].content
    assert out[0].mime_type == "text/markdown"
    assert "Auto-Context" in out[0].content


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

def test_skill_tool_listed_and_enum_matches_resources():
    """The `skill` tool exists and every enum value maps to a known skill, so the
    opener's `skill(name=...)` pointer is always valid."""
    pytest.importorskip("mcp")
    from neuron.server import list_tools, _SKILLS
    tools = asyncio.run(list_tools())
    skill = next((t for t in tools if t.name == "skill"), None)
    assert skill is not None, "skill tool not registered"
    enum = skill.inputSchema["properties"]["name"]["enum"]
    assert set(enum) == {k.rsplit("/", 1)[1] for k in _SKILLS}


def test_skill_tool_returns_full_text():
    pytest.importorskip("mcp")
    from neuron.server import call_tool
    out = asyncio.run(call_tool("skill", {"name": "auto-context"}))
    assert out and "Auto-Context" in out[0].text


def test_skill_tool_unknown_name_is_graceful():
    """Unknown skill name must not crash the tool — it returns a helpful message."""
    pytest.importorskip("mcp")
    from neuron.server import call_tool
    out = asyncio.run(call_tool("skill", {"name": "nope"}))
    assert "Unknown skill" in out[0].text
