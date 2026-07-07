"""Turso Cloud onboarding — connect, TEST for real, then save.

The "no-frills" path for a team (<=6) to join a shared Turso Cloud database:

    python scripts/connect_turso.py

It will:
  1. ask for the database URL and auth token (token entry is hidden),
  2. actually connect and run a read + a write probe against the real DB,
  3. only if the probe succeeds, offer to save the credentials to .env.

Nothing is written unless the connection works, and the auth token is never
printed back to the screen or logged. Non-interactive use (e.g. from an
installer) is supported via flags:

    python scripts/connect_turso.py --url libsql://... --token *** --yes
    python scripts/connect_turso.py --check-only        # test, never write
    python scripts/connect_turso.py --show-token        # token entry VISIBLE, to verify a paste

Exit codes: 0 = connection OK (and saved, unless --check-only), 1 = failure.

This is the online counterpart to scripts/check_cloud_config.py (which is
offline and never connects). See docs/DEVELOPER.md > Enabling Turso Cloud.
"""
from __future__ import annotations

import argparse
import getpass
import os
import re
import sys

# Make Unicode output safe on legacy Windows consoles (cp1252): reconfigure
# stdout/stderr to UTF-8 so the glyphs printed below never raise
# UnicodeEncodeError. Best-effort and never fatal.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# A throwaway table name used only to confirm write access, then dropped.
_PROBE_TABLE = "_neuron_conn_probe"


def _mask(token: str) -> str:
    """Render a token safe to display: only its length, never its content."""
    return f"<{len(token)} chars>" if token else "<empty>"


def _preview(token: str) -> str:
    """A verifiable-but-guarded preview: head…tail + length, so a user can
    confirm they pasted the right value without splashing the whole secret on
    screen. For very short strings just show the length."""
    if not token:
        return "<empty>"
    if len(token) <= 24:
        return f"<{len(token)} chars>"
    return f"{token[:12]}…{token[-8:]}  ({len(token)} chars)"


# ---------------------------------------------------------------------------
# Credential sanitising / validation
# ---------------------------------------------------------------------------
# A stray newline/CR inside the auth token is the classic cause of "every
# scheme failed": the token goes into the HTTP header ``Authorization: Bearer
# <token>``, and the underlying HTTP stack REJECTS any header value containing a
# control char (\r/\n/\0) to prevent header injection. Since the bad header is
# built the same way for libsql://, wss://, ws:// and https://, they all fail
# identically — which looks like a connection problem but is really a malformed
# credential (usually a token that got wrapped across lines on copy-paste, or a
# .env value with CRLF/quotes). ``.strip()`` only cleans the ends, so an
# *internal* line break slips through; here we strip whitespace/control chars
# everywhere, then validate what remains.

# Anything that is whitespace or an ASCII/Unicode control character.
_CTRL_WS_RE = re.compile(r"[\s\x00-\x1f\x7f]")
# A Turso JWT is base64url with '.' separators; allow base64 '=' padding too.
_TOKEN_ALLOWED_RE = re.compile(r"^[A-Za-z0-9._~+/=-]+$")
# RFC-3986-ish set: enough for libsql://host:port/path?query URLs.
_URL_ALLOWED_RE = re.compile(r"^[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+$")
_URL_SCHEMES = ("libsql", "wss", "ws", "https", "http")


def sanitize_credential(value: str) -> str:
    """Drop stray whitespace/control chars anywhere in the value (not just the
    ends). This removes the hidden \\r/\\n that breaks the auth header while
    leaving the real credential content untouched."""
    return _CTRL_WS_RE.sub("", value or "")


def validate_url(url: str) -> str | None:
    """Return a human error message if the URL is unusable, else ``None``."""
    if not url:
        return "URL vuoto."
    if not _URL_ALLOWED_RE.match(url):
        bad = sorted({c for c in url if not _URL_ALLOWED_RE.match(c)})
        return f"URL contiene caratteri non validi: {bad!r}."
    if "://" not in url:
        return "URL senza schema (atteso libsql://… o https://…)."
    scheme = url.split("://", 1)[0].lower()
    if scheme not in _URL_SCHEMES:
        return f"schema '{scheme}' non supportato (usa {' / '.join(_URL_SCHEMES)})."
    return None


def validate_token(token: str) -> str | None:
    """Return a human error message if the token is unusable, else ``None``.
    Never includes the token content in the message."""
    if not token:
        return "token vuoto."
    if not _TOKEN_ALLOWED_RE.match(token):
        bad = sorted({c for c in token if not _TOKEN_ALLOWED_RE.match(c)})
        return f"token contiene caratteri non validi: {bad!r}."
    return None


# ---------------------------------------------------------------------------
# URL scheme handling
# ---------------------------------------------------------------------------

def candidate_urls(url: str) -> list[str]:
    """Ordered connection URLs to try for a given Turso URL.

    The Python ``libsql-client`` picks its transport from the scheme:
    ``libsql://`` / ``wss://`` use a WebSocket (Hrana-over-WS), while
    ``https://`` uses Hrana-over-HTTP. Some Turso endpoints reject the WS
    upgrade with ``WSServerHandshakeError: 400`` even though the same database
    answers fine over HTTP — so we try the given scheme first and transparently
    fall back to the HTTP (``https://``) form. Both transports support the same
    SQL, including server-side ``vector_distance_cos()``.
    """
    url = url.strip().rstrip("/")
    out: list[str] = [url]
    for prefix in ("libsql://", "wss://", "ws://"):
        if url.startswith(prefix):
            out.append("https://" + url[len(prefix):])
            break
    else:
        if url.startswith("http://"):
            out.append("https://" + url[len("http://"):])
    # dedupe, keep order
    seen: set[str] = set()
    return [u for u in out if not (u in seen or seen.add(u))]


# ---------------------------------------------------------------------------
# Connection test (real network I/O) — isolated so it can be swapped in tests.
# ---------------------------------------------------------------------------

def _probe_one(libsql_client, url: str, token: str) -> tuple[bool, str]:
    """Run the read + write probe against a single URL. (ok, message)."""
    client = None
    try:
        client = libsql_client.create_client_sync(url=url, auth_token=token)
        rs = client.execute("SELECT 1")
        if not rs.rows or tuple(rs.rows[0].astuple()) != (1,):
            return (False, "read probe (SELECT 1) returned an unexpected result")
        client.execute(f"CREATE TABLE IF NOT EXISTS {_PROBE_TABLE} (k INTEGER)")
        client.execute(f"DELETE FROM {_PROBE_TABLE}")
        client.execute(f"INSERT INTO {_PROBE_TABLE} (k) VALUES (1)")
        rs = client.execute(f"SELECT k FROM {_PROBE_TABLE}")
        got = tuple(rs.rows[0].astuple()) if rs.rows else ()
        client.execute(f"DROP TABLE IF EXISTS {_PROBE_TABLE}")
        if got != (1,):
            return (False, "read OK but write probe did not round-trip — the token may be read-only")
        return (True, "read and write both succeeded")
    except Exception as exc:  # noqa: BLE001
        try:
            if client is not None:
                client.execute(f"DROP TABLE IF EXISTS {_PROBE_TABLE}")
        except Exception:
            pass
        return (False, f"{type(exc).__name__}: {exc}")
    finally:
        try:
            if client is not None:
                client.close()
        except Exception:
            pass


def probe_connection(url: str, token: str) -> tuple[bool, str | None, str]:
    """Open a real Turso connection and run a read + write probe, trying each
    candidate URL scheme.

    Returns ``(ok, working_url, message)``. ``working_url`` is the scheme that
    actually connected (may differ from the input, e.g. https:// instead of
    libsql://) and is what should be saved. Never raises; never includes the
    token in the message.
    """
    try:
        import libsql_client  # type: ignore
    except ImportError:
        return (False, None,
                "The 'libsql-client' package is not installed, so a cloud "
                "connection cannot be tested.\n  Install the cloud extra first: "
                "pip install -e .[cloud]")

    attempts: list[str] = []
    for cand in candidate_urls(url):
        ok, detail = _probe_one(libsql_client, cand, token)
        if ok:
            note = "" if cand == url.strip().rstrip("/") else f" (via {cand.split('://', 1)[0]}://)"
            return (True, cand, f"Connection OK — {detail}.{note}")
        attempts.append(f"  - {cand.split('://', 1)[0]}://…  {detail}")
    return (False, None, "Connection failed for every scheme tried:\n" + "\n".join(attempts))


# ---------------------------------------------------------------------------
# .env writing — update the two keys in place, preserve everything else.
# ---------------------------------------------------------------------------

def update_env_file(path: str, values: dict[str, str]) -> None:
    """Set each key=value in the .env at `path`, updating existing lines in
    place and appending any that are missing. Other lines are preserved."""
    lines: list[str] = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()

    remaining = dict(values)
    out: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped else ""
        if key in remaining and not stripped.startswith("#"):
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    for key, val in remaining.items():
        out.append(f"{key}={val}")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Connect & test a Turso Cloud DB, then save to .env.")
    parser.add_argument("--url", help="Turso database URL (libsql://...). Prompted if omitted.")
    parser.add_argument("--token", help="Turso auth token. Prompted (hidden) if omitted.")
    parser.add_argument("--env-file", default=".env", help="Path to the .env file (default: ./.env).")
    parser.add_argument("--yes", action="store_true", help="Save without asking for confirmation.")
    parser.add_argument("--check-only", action="store_true",
                        help="Only test the connection; never write .env.")
    parser.add_argument("--show-token", action="store_true",
                        help="Mostra il token mentre lo incolli (input visibile, non "
                             "nascosto) così puoi verificarlo. Default: nascosto.")
    args = parser.parse_args(argv)

    url = (args.url or "").strip() or input("Turso database URL (libsql://...): ").strip()
    token = (args.token or "").strip()
    if not token:
        if args.show_token:
            # Visible entry: the whole point is to SEE what got pasted, since a
            # hidden prompt hides a mangled paste (wrapped/truncated token).
            token = input("Turso auth token (visibile): ").strip()
        else:
            token = getpass.getpass("Turso auth token (nascosto, usa --show-token per vederlo): ").strip()

    # Strip stray whitespace/control chars ANYWHERE (not just the ends): a hidden
    # newline in the token is what makes every scheme fail with a header-injection
    # rejection. Report if the cleanup actually removed something, so the user
    # learns their pasted value was malformed.
    url_clean = sanitize_credential(url)
    token_clean = sanitize_credential(token)
    if url_clean != url:
        print("  ⚠️  rimossi spazi/caratteri di controllo dall'URL.")
    if token_clean != token:
        print("  ⚠️  rimossi spazi/caratteri di controllo dal token "
              "(probabile a-capo nascosto nel copia-incolla).")
    url, token = url_clean, token_clean

    if not url or not token:
        print("Both a URL and a token are required. Aborting.", file=sys.stderr)
        return 1

    err = validate_url(url) or validate_token(token)
    if err:
        print(f"Credenziali non valide: {err}", file=sys.stderr)
        print("  Ricontrolla di aver incollato URL e token senza spazi o a-capo interni.",
              file=sys.stderr)
        return 1

    print(f"\nTesting connection to: {url}")
    if args.show_token:
        # Explicit opt-in: echo the full token so it can be checked char-by-char.
        print(f"  token: {token}")
    else:
        print(f"  token: {_preview(token)}  (usa --show-token per vederlo intero)")
    ok, working_url, msg = probe_connection(url, token)
    print(f"  {'✅' if ok else '❌'} {msg}")
    if not ok:
        print("\nNothing was saved. Fix the issue above and re-run.", file=sys.stderr)
        return 1

    # Save the URL that actually connected (its scheme may differ from the input,
    # e.g. https:// when libsql:// fails the WebSocket handshake) so the server
    # uses the working transport too.
    save_url = working_url or url.strip().rstrip("/")
    if save_url != url.strip().rstrip("/"):
        print(f"  → will store the working URL: {save_url}")

    if args.check_only:
        print("\n--check-only: connection verified, .env left unchanged.")
        return 0

    if not args.yes:
        ans = input(f"\nSave these credentials to {args.env_file}? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            print("Not saved (connection was still verified OK).")
            return 0

    update_env_file(args.env_file, {
        "TURSO_DATABASE_URL": save_url,
        "TURSO_AUTH_TOKEN": token,
    })
    print(f"\nSaved to {args.env_file}. The token is stored there — keep the file "
          "private (it is gitignored).")
    print("Neuron will use the shared cloud DB the next time it starts with this env.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
