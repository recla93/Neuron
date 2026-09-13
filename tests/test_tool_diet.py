"""Tool diet: upkeep/admin tools are served but not announced.

The published schemas cost the model ~5k tokens per session for Neuron alone;
half of them are tools the per-turn loop never calls. They stay dispatchable
by name (CLI, Gray-Matter) — only the announcement changes.
"""
import asyncio

import pytest


@pytest.fixture
def srv(monkeypatch):
    import neuron.server as S
    monkeypatch.delenv("NEURON_TOOLS", raising=False)
    return S


def _names(S):
    return {t.name for t in asyncio.run(S.list_tools())}


def test_the_loop_tools_are_announced_and_the_admin_ones_are_not(srv):
    names = _names(srv)
    assert {"pre_turn", "store_turn", "confirm", "dismiss", "find_candidates",
            "get_context", "help", "skill"} <= names
    assert not (names & srv._ADMIN_TOOLS), names & srv._ADMIN_TOOLS
    # served regardless: the dispatch table still knows every one of them
    assert srv._ADMIN_TOOLS <= set(srv._HANDLERS)


def test_neuron_tools_all_announces_everything(srv, monkeypatch):
    monkeypatch.setenv("NEURON_TOOLS", "all")
    assert srv._ADMIN_TOOLS <= _names(srv)


def test_what_the_schema_dropped_is_in_help(srv):
    from neuron.funnel import HELP_TEXT
    for word in ("NEURON_TOOLS=all", "episode_lost", "switches the"):
        assert word in HELP_TEXT, word
