"""The HTTP bridge must not publish itself open without a key.

keep-in-sync with neurag/tests/test_bridge_auth.py: same attack, same defense,
env prefixed NEURON_.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from neuron import http_transport  # noqa: E402


def test_loopback_needs_no_token(monkeypatch):
    monkeypatch.delenv("NEURON_BRIDGE_TOKEN", raising=False)
    assert not http_transport._refuse_open_bind("127.0.0.1")
    assert not http_transport._refuse_open_bind("localhost")


def test_open_bind_without_token_is_refused(monkeypatch):
    monkeypatch.delenv("NEURON_BRIDGE_TOKEN", raising=False)
    monkeypatch.delenv("NEURON_BRIDGE_ALLOW_OPEN", raising=False)
    assert http_transport._refuse_open_bind("0.0.0.0")
    with pytest.raises(SystemExit):
        http_transport.serve(app=None, host="0.0.0.0", port=1)


def test_open_bind_with_token_or_escape_hatch_passes(monkeypatch):
    monkeypatch.setenv("NEURON_BRIDGE_TOKEN", "s3cret")
    assert not http_transport._refuse_open_bind("0.0.0.0")

    monkeypatch.delenv("NEURON_BRIDGE_TOKEN", raising=False)
    monkeypatch.setenv("NEURON_BRIDGE_ALLOW_OPEN", "1")
    assert not http_transport._refuse_open_bind("0.0.0.0")
