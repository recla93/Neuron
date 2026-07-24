"""Optional .env loader (T16) — makes saved cloud credentials "just work".

`scripts/connect_turso.py` writes TURSO_DATABASE_URL / TURSO_AUTH_TOKEN to a
`.env`, but `db.py` reads those from ``os.environ`` **at import time** and nothing
else populates them. This module loads the `.env` into ``os.environ`` so the
server picks the credentials up automatically at startup.

Because `db.py` resolves the tier at import, this must run *before* ``neuron.db``
is imported — so it is invoked from ``neuron/__init__.py``, which the interpreter
executes before importing any ``neuron`` submodule.

Deliberately conservative:
  * **real environment always wins** — values already in ``os.environ`` are never
    overwritten (``setdefault``), so a client's ``env`` block still takes priority;
  * **disabled under pytest** and via ``NEURON_NO_DOTENV=1`` — a developer's real
    ``.env`` (with live cloud credentials) must never silently switch the test
    suite onto the remote DB;
  * runs at most once; never raises.
"""
from __future__ import annotations

import os
import re
import sys

# Strip stray whitespace/control chars anywhere in a credential value, not just
# the ends. The auth token becomes an HTTP header; the HTTP stack rejects any
# header value containing \\r/\\n/\\0 (header-injection guard), so a hidden
# newline from copy-paste or a CRLF-wrapped .env makes every scheme fail.
_CTRL_WS_RE = re.compile(r"[\s\x00-\x1f\x7f]")


def sanitize_credential(value: str) -> str:
    return _CTRL_WS_RE.sub("", value or "")

_loaded = False


def _find_env_file() -> str | None:
    """Locate the .env: an explicit ``NEURON_ENV_FILE`` wins; otherwise walk up
    from the current working directory (the server may be launched from a
    subdirectory of the project)."""
    explicit = os.environ.get("NEURON_ENV_FILE", "").strip()
    if explicit:
        return explicit if os.path.isfile(explicit) else None
    d = os.getcwd()
    for _ in range(8):
        cand = os.path.join(d, ".env")
        if os.path.isfile(cand):
            return cand
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1]
    return v


def _is_test_run() -> bool:
    """True when we're clearly running under a test harness — never auto-load
    then, so a real .env can't flip the suite onto the live cloud."""
    return "pytest" in sys.modules or bool(os.environ.get("PYTEST_CURRENT_TEST"))


def load_dotenv_once(path: str | None = None) -> bool:
    """Populate os.environ from a .env (real env wins). Returns True if a file
    was read. No-op under pytest / NEURON_NO_DOTENV, and after the first call."""
    global _loaded
    if _loaded:
        return False
    _loaded = True
    if os.environ.get("NEURON_NO_DOTENV", "").strip():
        return False
    if _is_test_run():
        return False
    path = path or _find_env_file()
    if not path or not os.path.isfile(path):
        return False
    try:
        # utf-8-sig: PowerShell 5.1 `Set-Content -Encoding utf8` writes a BOM
        # that would corrupt the first key (audit 2026-07-21, keep-in-sync with
        # gray_matter/_env.py).
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                if key:
                    os.environ.setdefault(key, _unquote(val))

        # Warn if .env contains TURSO_ vars but they aren't in os.environ after
        # loading — means real env vars are set (which is fine), or the .env
        # format is wrong (CRLF-wrapped value, unquoted trailing garbage, ...).
        if not os.environ.get("TURSO_DATABASE_URL") or not os.environ.get("TURSO_AUTH_TOKEN"):
            try:
                with open(path, encoding="utf-8") as _check:
                    for _line in _check:
                        _line = _line.strip()
                        # Check EVERY TURSO_ line: a prior `break` after the first
                        # one meant a malformed TURSO_AUTH_TOKEN was never reported
                        # when TURSO_DATABASE_URL happened to load fine.
                        if _line.startswith("TURSO_") and "=" in _line:
                            _key, _ = _line.split("=", 1)
                            if _key.strip() not in os.environ:
                                print(
                                    f"  [?] .env has {_key.strip()} but it was not loaded "
                                    f"(real env var set, or malformed value in .env?)",
                                    file=sys.stderr,
                                )
            except OSError:
                pass
    except OSError:
        return False
    return True
