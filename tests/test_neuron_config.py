"""`neuron config`: the knobs the control center renders, starting with the cap
on concepts per graph (500 was reached by a real graph on 2026-11-04)."""
import json

import pytest

from neuron import config


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.delenv("NEURON_MAX_NODES", raising=False)
    return tmp_path


def _list(capsys):
    assert config.config_cli(["list", "--json"]) == 0
    return {k["key"]: k for k in json.loads(capsys.readouterr().out)["knobs"]}


def test_the_default_cap_is_1000_and_the_gui_gets_its_help(home, capsys):
    knob = _list(capsys)["max_nodes"]
    assert knob["value"] == 1000 and knob["default"] == 1000 and knob["type"] == "int"
    assert "5000" in knob["help"]


def test_set_writes_the_user_env_file(home, capsys):
    assert config.config_cli(["set", "max_nodes", "3000", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["value"] == 3000
    with open(config.user_env_file(), encoding="utf-8") as f:
        assert "NEURON_MAX_NODES=3000" in f.read()


@pytest.mark.parametrize("bad", ["50", "5001", "mille"])
def test_out_of_range_or_not_a_number_is_refused(home, bad, capsys):
    assert config.config_cli(["set", "max_nodes", bad]) == 1
    import os
    assert not os.path.exists(config.user_env_file())


def test_a_hand_edited_value_is_clamped(home, monkeypatch):
    monkeypatch.setenv("NEURON_MAX_NODES", "999999")
    assert config.knob_value("max_nodes") == 5000
