"""Tests for the Turso local/cloud toggle + read-only check (connect.py).

Covers: read_saved_creds, cloud_creds_present, set_cloud_active round-trip, and
the CLI `--use-local` / `--use-cloud` / `--check-only` (non-interactive) paths —
the machinery behind the GUI's Check Cloud / Switch to Local / Switch to Cloud
buttons. No network: these paths never call probe_connection.
"""
from __future__ import annotations

import os
import sys

sys.modules.setdefault("turso", None)

from neuron import connect as C


def _write(env, text):
    env.write_text(text, encoding="utf-8")
    return str(env)


def test_read_saved_creds_active(tmp_path, monkeypatch):
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
    p = _write(tmp_path / ".env",
               "TURSO_DATABASE_URL=libsql://x.turso.io\nTURSO_AUTH_TOKEN=abc\n")
    assert C.read_saved_creds(p) == {"url": "libsql://x.turso.io", "token": "abc"}
    assert C.cloud_creds_present(p) is True


def test_read_saved_creds_none(tmp_path, monkeypatch):
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
    p = _write(tmp_path / ".env", "OTHER=1\n")
    assert C.read_saved_creds(p) == {"url": "", "token": ""}
    assert C.cloud_creds_present(p) is False


def test_toggle_local_then_cloud(tmp_path, monkeypatch):
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
    p = _write(tmp_path / ".env",
               "TURSO_DATABASE_URL=libsql://x\nTURSO_AUTH_TOKEN=abc\n")
    # -> local: creds get commented, no longer active, but still present
    assert C.set_cloud_active(p, active=False) is True
    assert C.read_saved_creds(p) == {"url": "", "token": ""}
    assert C.cloud_creds_present(p) is True
    assert C.set_cloud_active(p, active=False) is False           # idempotent
    # -> cloud: re-enabled
    assert C.set_cloud_active(p, active=True) is True
    assert C.read_saved_creds(p)["url"] == "libsql://x"
    assert C.set_cloud_active(p, active=True) is False            # idempotent


def test_cli_use_cloud_without_creds_fails(tmp_path, monkeypatch):
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
    p = _write(tmp_path / ".env", "OTHER=1\n")
    assert C.main(["--use-cloud", "--env-file", p]) == 1          # nothing to switch to


def test_cli_use_local_and_cloud_roundtrip(tmp_path, monkeypatch):
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
    p = _write(tmp_path / ".env",
               "TURSO_DATABASE_URL=libsql://x\nTURSO_AUTH_TOKEN=abc\n")
    assert C.main(["--use-local", "--env-file", p]) == 0
    assert C.read_saved_creds(p) == {"url": "", "token": ""}
    assert C.main(["--use-cloud", "--env-file", p]) == 0
    assert C.read_saved_creds(p)["url"] == "libsql://x"


def test_cli_check_only_not_configured_is_quiet(tmp_path, monkeypatch):
    # Non-interactive (pytest stdin is not a tty): --check-only must NOT prompt;
    # with no saved creds it returns 1 with guidance, never raising EOFError.
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
    p = _write(tmp_path / ".env", "OTHER=1\n")
    assert C.main(["--check-only", "--env-file", p]) == 1
