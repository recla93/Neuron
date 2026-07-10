"""Turso cloud readiness check — OFFLINE.

Reports which database tier neuron.db would select and whether the environment is
ready for Turso cloud, WITHOUT opening any database connection. Safe to run
anywhere: it never contacts the remote Turso server, never imports the heavy
neuron deps, and never prints the auth token.

Usage:
    python scripts/check_cloud_config.py

Exit code: 0 if the resolved configuration is self-consistent, 1 if cloud is
half-configured (e.g. credentials set but the `cloud` extra not installed), which
would make the MCP server fail at import.
"""
from __future__ import annotations

import importlib.util
import os
import sys

# Make Unicode output safe on legacy Windows consoles (cp1252): reconfigure
# stdout/stderr to UTF-8 so the glyphs printed below never raise
# UnicodeEncodeError. Best-effort and never fatal.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _load_dotenv_minimal(env_path: str) -> dict[str, str]:
    """Minimal .env reader: KEY=VALUE lines only, skips comments and blank lines.
    Returns a dict of key -> value for uncommented lines."""
    result: dict[str, str] = {}
    if not os.path.isfile(env_path):
        return result
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" in stripped:
                key, _, val = stripped.partition("=")
                result[key.strip()] = val.strip().strip("\"'")
    return result


def _present(name: str, env_file_vars: dict[str, str]) -> bool:
    """Check env var: process env first, then .env file (uncommented lines only)."""
    val = os.environ.get(name, "").strip()
    if val:
        return True
    val = env_file_vars.get(name, "").strip()
    return bool(val)


def main() -> int:
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    env_file_vars = _load_dotenv_minimal(env_path)

    url_set = _present("TURSO_DATABASE_URL", env_file_vars)
    token_set = _present("TURSO_AUTH_TOKEN", env_file_vars)
    remote_requested = url_set and token_set
    remote_partial = (url_set or token_set) and not remote_requested

    have_libsql = importlib.util.find_spec("libsql_client") is not None
    have_pyturso = importlib.util.find_spec("turso") is not None

    if remote_requested:
        engine = "Turso (cloud)"
    elif have_pyturso:
        engine = "Turso (local)"
    else:
        engine = "SQLite"

    print("Neuron — Turso cloud readiness (offline check)")
    print("-" * 48)
    print(f"  TURSO_DATABASE_URL set : {url_set}")
    print(f"  TURSO_AUTH_TOKEN set   : {token_set}")
    print(f"  cloud extra installed  : {have_libsql}  (libsql-client)")
    print(f"  local engine installed : {have_pyturso}  (pyturso)")
    print(f"  → resolved DB tier     : {engine}")
    print("-" * 48)

    problems: list[str] = []
    if remote_requested and not have_libsql:
        problems.append(
            "Cloud credentials are set but 'libsql-client' is NOT installed.\n"
            "      The server imports libsql_client at startup when these vars are set,\n"
            "      so it would crash on import. Fix:  pip install -e .[cloud]"
        )
    if remote_partial:
        problems.append(
            "Only ONE of TURSO_DATABASE_URL / TURSO_AUTH_TOKEN is set; cloud needs both.\n"
            "      Neuron will silently stay on the local tier until both are present."
        )
    if not remote_requested and not have_pyturso:
        problems.append(
            "No cloud credentials and 'pyturso' not installed → stdlib sqlite3 fallback,\n"
            "      which has NO native vector search (slower Python-side cosine). Fix:\n"
            "      pip install -e .  (installs pyturso)"
        )

    if problems:
        print("Issues:")
        for p in problems:
            print(f"  ⚠️  {p}")
    else:
        print("OK: configuration is self-consistent. (No connection was attempted.)")

    if remote_requested and have_libsql:
        print("\nCloud is configured AND its dependency is present. To actually verify")
        print("connectivity against the real Turso DB (this offline check never connects),")
        print("run the online onboarding tool, which does a real read+write probe:")
        print("    python scripts/connect_turso.py --check-only")

    # Only a hard "half-configured cloud" state is a failure: it breaks startup.
    return 1 if (remote_requested and not have_libsql) or remote_partial else 0


if __name__ == "__main__":
    raise SystemExit(main())
