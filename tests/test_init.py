"""Tests for `neuron init` (client wiring). Pure stdlib — no mcp/fastembed needed,
so these run anywhere, including CI without the model deps."""

import json
import os
import sys
from pathlib import Path

import pytest

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
# init.py lives under src/ (src layout); make it importable without an install.
_src = os.path.join(_project_root, "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

from neuron import init as ni  # noqa: E402


def _cfg(home: Path) -> Path:
    return home / ".config" / "opencode" / "opencode.json"


def _skill(home: Path) -> Path:
    return home / ".config" / "opencode" / "skills" / "neuron" / "neuron-opener.md"


def test_init_fresh_home_writes_skill_and_wires_config(tmp_path):
    rep = ni.init_opencode(home=tmp_path)
    assert rep["ok"]
    # opener copied and non-empty
    assert _skill(tmp_path).exists()
    assert "pre_turn" in _skill(tmp_path).read_text(encoding="utf-8")
    # config created with the skill path in instructions[]
    data = json.loads(_cfg(tmp_path).read_text(encoding="utf-8"))
    assert data["instructions"] == [str(_skill(tmp_path))]


def test_init_is_idempotent(tmp_path):
    ni.init_opencode(home=tmp_path)
    ni.init_opencode(home=tmp_path)
    data = json.loads(_cfg(tmp_path).read_text(encoding="utf-8"))
    # the path appears exactly once, never duplicated
    assert data["instructions"].count(str(_skill(tmp_path))) == 1


def test_init_preserves_existing_config_and_backs_up(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({
        "mcp": {"neuron": {"command": ["x"], "type": "local"}},
        "instructions": ["~/AGENTS.md"],
    }), encoding="utf-8")

    ni.init_opencode(home=tmp_path)
    data = json.loads(cfg.read_text(encoding="utf-8"))
    # existing entries preserved, ours appended
    assert data["instructions"] == ["~/AGENTS.md", str(_skill(tmp_path))]
    assert data["mcp"]["neuron"]["type"] == "local"
    # a backup was written
    assert Path(str(cfg) + ".neuron-init.bak").exists()


def test_init_dry_run_writes_nothing(tmp_path):
    rep = ni.init_opencode(home=tmp_path, dry_run=True)
    assert rep["ok"]
    assert not _skill(tmp_path).exists()
    assert not _cfg(tmp_path).exists()


def test_init_refuses_invalid_json(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("{ not valid json, // comment", encoding="utf-8")

    rep = ni.init_opencode(home=tmp_path)
    assert rep["ok"] is False
    # the invalid file is left untouched
    assert cfg.read_text(encoding="utf-8").startswith("{ not valid")


def test_init_normalizes_scalar_instructions(tmp_path):
    """If instructions was a bare string, it's coerced to a list, not clobbered."""
    cfg = _cfg(tmp_path)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"instructions": "~/AGENTS.md"}), encoding="utf-8")

    ni.init_opencode(home=tmp_path)
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["instructions"] == ["~/AGENTS.md", str(_skill(tmp_path))]
