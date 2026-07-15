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


def _preview(token: str) -> str:
    """Show only the token length by default. The token isn't very sensitive
    (long, revocable) and lands in .env in cleartext anyway, so elaborate
    console masking is theater — confidentiality is handled by .env file perms +
    gitignore. Use --show-token to echo it whole for a char-by-char check."""
    return f"<{len(token)} chars>" if token else "<empty>"


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

# RFC-3986-ish set: enough for libsql://host:port/path?query URLs.
_URL_ALLOWED_RE = re.compile(r"^[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+$")
_URL_SCHEMES = ("libsql", "wss", "ws", "https", "http")


try:
    from neuron._env import sanitize_credential
except ImportError:
    import re as _re
    _CTRL_WS_RE_FALLBACK = _re.compile(r"[\s\x00-\x1f\x7f]")
    def sanitize_credential(value: str) -> str:
        return _CTRL_WS_RE_FALLBACK.sub("", value or "")


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


# NOTE: a token charset check used to live here; dropped — the real read+write
# network probe (_probe_one) validates the token authoritatively, and a regex
# risks false-rejecting a future token format. sanitize_credential (control-char
# strip) stays: it's the actual fix for the hidden-newline header-injection bug.


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

def _ensure_local_placeholders(env_path: str) -> None:
    """Add commented-out TURSO_LOCAL_* lines if they don't exist yet.

    The GUI's Turso toggle (Switch to LOCAL / CLOUD) swaps active
    comments between the cloud and local variable pairs, so both must
    be present in .env for the toggle to work without re-entry.
    """
    local_keys = ("TURSO_LOCAL_DATABASE_URL", "TURSO_LOCAL_AUTH_TOKEN")
    lines: list[str] = []
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            lines = f.read().splitlines()

    existing = {line.lstrip().split("=", 1)[0].strip()
                for line in lines if "=" in line}
    added = 0
    for key in local_keys:
        if key not in existing:
            lines.append(f"# {key}=")
            added += 1
    if added > 0:
        tmp = env_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, env_path)


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

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Saved-credential access + local/cloud toggle (used by --check-only, the
# --use-local/--use-cloud switches and the GUI Turso buttons).
# ---------------------------------------------------------------------------

_CLOUD_KEYS = ("TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN")


def read_saved_creds(env_file: str) -> dict[str, str]:
    """Return the ACTIVE (uncommented) Turso creds from `.env` plus the real
    environment (real env wins), sanitised. Empty strings when not configured.
    Read-only — never prompts, never writes."""
    vals: dict[str, str] = {}
    try:
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, _, v = s.partition("=")
                if k.strip() in _CLOUD_KEYS:
                    vals[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    url = os.environ.get("TURSO_DATABASE_URL") or vals.get("TURSO_DATABASE_URL", "")
    token = os.environ.get("TURSO_AUTH_TOKEN") or vals.get("TURSO_AUTH_TOKEN", "")
    return {"url": sanitize_credential(url), "token": sanitize_credential(token)}


def cloud_creds_present(env_file: str) -> bool:
    """True if Turso cloud credentials exist in ANY form — active in the real
    environment, or present in `.env` even if commented out. That means they
    were saved once and the user can switch to cloud. The GUI uses this to
    enable/disable its 'Switch to Cloud' button."""
    if os.environ.get("TURSO_DATABASE_URL") and os.environ.get("TURSO_AUTH_TOKEN"):
        return True
    found = {k: False for k in _CLOUD_KEYS}
    try:
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                body = line.strip().lstrip("#").strip()   # tolerate a leading '#'
                if "=" not in body:
                    continue
                k, _, v = body.partition("=")
                if k.strip() in found and v.strip().strip('"').strip("'"):
                    found[k.strip()] = True
    except OSError:
        return False
    return all(found.values())


def set_cloud_active(env_file: str, active: bool) -> bool:
    """Toggle the store between cloud and local by (un)commenting the two Turso
    cloud keys in `.env`. ``active=True`` uncomments them (use cloud);
    ``active=False`` comments them out (fall back to the local engine). Only
    those two keys are touched; every other line is preserved. Returns True if
    the file actually changed. The switch takes effect on the next server start
    (db.py reads TURSO_* at import)."""
    try:
        with open(env_file, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return False
    changed = False
    out: list[str] = []
    for line in lines:
        bare = line.lstrip()
        is_comment = bare.startswith("#")
        body = bare.lstrip("#").strip() if is_comment else bare
        key = body.split("=", 1)[0].strip() if "=" in body else ""
        if key in _CLOUD_KEYS:
            if active and is_comment:
                out.append(body); changed = True; continue
            if not active and not is_comment:
                out.append(f"# {body}"); changed = True; continue
        out.append(line)
    if changed:
        tmp = env_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
        os.replace(tmp, env_file)
    return changed


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
    parser.add_argument("--use-local", action="store_true",
                        help="Switch the store to the LOCAL engine (comment out the "
                             "Turso creds in .env). No network, no prompt.")
    parser.add_argument("--use-cloud", action="store_true",
                        help="Switch the store to Turso CLOUD (re-enable saved creds). "
                             "Fails if no credentials were ever saved.")
    args = parser.parse_args(argv)

    # Local/cloud toggle — pure .env edits, no network, no prompt. Effective on
    # the next server start (db.py reads TURSO_* at import).
    if args.use_local:
        changed = set_cloud_active(args.env_file, active=False)
        print("Store set to LOCAL"
              + (" — Turso credentials commented out." if changed else " (already local).")
              + "\nRestart the Neuron server / AI clients to apply.")
        return 0
    if args.use_cloud:
        if not cloud_creds_present(args.env_file):
            print("No saved Turso credentials to switch to. Run 'neuron connect' "
                  "first to add them.", file=sys.stderr)
            return 1
        changed = set_cloud_active(args.env_file, active=True)
        print("Store set to Turso CLOUD"
              + (" — credentials re-enabled." if changed else " (already cloud).")
              + "\nRestart the Neuron server / AI clients to apply.")
        return 0

    url = (args.url or "").strip()
    token = (args.token or "").strip()

    # --check-only with no explicit creds probes the ALREADY-SAVED ones
    # (read-only, no prompt). This is what the GUI 'Check Cloud' button uses.
    if args.check_only and not (url and token):
        saved = read_saved_creds(args.env_file)
        url = url or saved["url"]
        token = token or saved["token"]

    if not url or not token:
        # Prompt only with a real interactive terminal; otherwise give guidance
        # instead of hanging on input() / dumping an EOFError traceback.
        if not sys.stdin.isatty():
            if args.check_only:
                print("Turso Cloud non configurato: nessuna credenziale salvata in "
                      ".env. Usa 'Connect' (Turso → Connect) per aggiungerle.",
                      file=sys.stderr)
                return 1
            print("\nSessione non interattiva: passa --url e --token, oppure apri "
                  "'neuron connect' in un terminale (dalla GUI: Turso → Connect).",
                  file=sys.stderr)
            return 2
        try:
            if not url:
                url = input("Turso database URL (libsql://...): ").strip()
            if not token:
                if args.show_token:
                    # Visible entry: the whole point is to SEE what got pasted, since
                    # a hidden prompt hides a mangled paste (wrapped/truncated token).
                    token = input("Turso auth token (visibile): ").strip()
                else:
                    token = getpass.getpass("Turso auth token (nascosto, usa --show-token per vederlo): ").strip()
        except EOFError:
            print("\nSessione non interattiva: passa --url e --token, oppure apri "
                  "'neuron connect' in un terminale (dalla GUI: Turso → Connect).",
                  file=sys.stderr)
            return 2

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

    err = validate_url(url)
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

    # Ensure commented local-mode placeholders exist so the GUI toggle
    # (Switch to LOCAL / CLOUD) has both variable pairs to swap.
    _ensure_local_placeholders(args.env_file)

    print(f"\n[OK] Saved to {args.env_file}. Restart your AI client(s) so the "
          "server picks up the Turso cloud database.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
