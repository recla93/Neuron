"""Neuron -> Gray-Matter optional autoregister: the opt-out contract.

Full integration (real registration/heartbeat) needs a running Gray-Matter and
is out of scope here; this pins the user-facing guarantee: NEURON_NO_GM=1 makes
the hook a silent no-op (no thread, no error), so Neuron always starts.
"""
import pytest


def test_gm_register_optout_is_silent(monkeypatch):
    pytest.importorskip("mcp")
    import neuron.server as srv
    monkeypatch.setenv("NEURON_NO_GM", "1")
    srv._maybe_register_gray_matter()  # opt-out: returns at once, starts no thread, raises nothing
