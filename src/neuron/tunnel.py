"""`neuron tunnel` — expose a local port over public HTTPS.

Two backends, chosen automatically:

1. **Cloudflare Named Tunnel** (recommended) — persistent URL, no uptime
   guarantee from quick tunnels. Requires a Cloudflare account and a domain.
   ``--named TUNNEL_NAME`` uses an existing named tunnel; ``--setup`` walks
   through first-time creation.
2. **Ngrok** — simpler, no account needed for basic use (limited free tier).
   Automatic fallback when cloudflared is missing or unconfigured.

Quick tunnels (``*.trycloudflare.com``) are still available via ``--quick``
but have **no uptime guarantee** — Cloudflare drops them after idle periods.

Usage::

    neuron tunnel --port 8000                     # auto-detect best backend
    neuron tunnel --named my-mcp --port 8000      # use a named CF tunnel
    neuron tunnel --quick --port 8000             # force quick tunnel (ephemeral)
    neuron tunnel --ngrok --port 8000             # force ngrok
    neuron tunnel --setup                         # first-time named tunnel setup
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

__all__ = ["main"]

# CREATE_NO_WINDOW (T81): under the windowless GUI, a console child would
# otherwise open its own CMD window.
_WIN_FLAGS = 0x08000000 if os.name == "nt" else 0

# URL patterns
_CF_QUICK_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
_CF_NAMED_RE = re.compile(r"https://[a-zA-Z0-9-]+\.[a-zA-Z0-9.-]+(?::\d+)?")
_NGROK_RE = re.compile(r"https://[a-z0-9-]+\.ngrok-free\.app")

# --- Credential storage (named tunnels) ---

def _cred_path() -> Path:
    """Where cloudflared stores tunnel credentials."""
    if os.name == "nt":
        return Path(os.environ.get("USERPROFILE", "~")) / ".cloudflared" / "cert.pem"
    return Path.home() / ".cloudflared" / "cert.pem"


def _tunnel_config_path() -> Path:
    """Where we store the user's tunnel name + account ID.

    This file lives INSIDE the GME folder, so it must land where the registry
    actually is — ask Gray Matter rather than recomputing. Two bugs came from
    the hand-rolled copy that used to be here:

    * on macOS it resolved `~/.local/share/GrayMatterEnvironment` while
      `gme_root()` resolved `~/Library/Application Support/...` — tunnel.json
      went into a folder the registry was not in;
    * `os.environ.get("LOCALAPPDATA", "~")` returns `""` when the variable is
      set-but-empty (service, scheduled task, scrubbed env), and `Path("")` is
      RELATIVE — so the config landed in the process's cwd. `Path("~")` is not
      expanded either. gme.py documents this exact failure as one that happened.

    Lazy import + mirror-on-failure: Neuron must keep working standalone, so a
    missing Gray Matter degrades instead of raising."""
    try:
        from gray_matter.gme import gme_root
        base = gme_root()
    except Exception:  # noqa: BLE001 — GM not installed: mirror its rule
        from neuron.config import default_suite, venv_suite
        base = Path(venv_suite() or default_suite())
    return base / "tunnel.json"


def _has_cf_credentials() -> bool:
    """Check if cloudflared has valid credentials for named tunnels."""
    return _cred_path().exists()


def _load_tunnel_config() -> dict:
    p = _tunnel_config_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_tunnel_config(cfg: dict) -> None:
    p = _tunnel_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


# --- Backend: Cloudflare quick tunnel (ephemeral) ---

def _run_cf_quick(url: str, *, on_url=None) -> int:
    """Run cloudflared quick tunnel once."""
    cf = shutil.which("cloudflared")
    if not cf:
        _print_install_cf()
        return 2
    proc = subprocess.Popen(
        [cf, "tunnel", "--url", url],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        encoding="utf-8", errors="replace", creationflags=_WIN_FLAGS)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            m = _CF_QUICK_RE.search(line)
            if m:
                public = m.group(0)
                print(f"\n  ==> MCP connector URL: {public}/mcp\n", flush=True)
                if on_url:
                    on_url(public)
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        raise


# --- Backend: Cloudflare named tunnel (persistent URL) ---

def _run_cf_named(tunnel_name: str, url: str, *, on_url=None) -> int:
    """Run a pre-existing named Cloudflare tunnel."""
    cf = shutil.which("cloudflared")
    if not cf:
        _print_install_cf()
        return 2
    proc = subprocess.Popen(
        [cf, "tunnel", "--url", url, tunnel_name],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        encoding="utf-8", errors="replace", creationflags=_WIN_FLAGS)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            # Named tunnels print the route, not a random URL — extract it
            m = _CF_NAMED_RE.search(line)
            if m and "trycloudflare" not in m.group(0):
                public = m.group(0)
                if not public.startswith("https://"):
                    public = "https://" + public
                print(f"\n  ==> MCP connector URL: {public}/mcp\n", flush=True)
                if on_url:
                    on_url(public)
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        raise


def _setup_cf_named() -> int:
    """Interactive first-time setup for a named Cloudflare tunnel.

    Walks the user through:
    1. cloudflared tunnel login (opens browser for Cloudflare auth)
    2. cloudflared tunnel create <name>
    3. Saves tunnel name to config
    """
    cf = shutil.which("cloudflared")
    if not cf:
        _print_install_cf()
        return 2

    print("\n=== Cloudflare Named Tunnel Setup ===\n")
    print("A named tunnel gives you a PERSISTENT URL that survives restarts.\n")
    print("Prerequisites:")
    print("  1. A Cloudflare account (free): https://dash.cloudflare.com/sign-up")
    print("  2. A domain added to Cloudflare (any domain you own)\n")

    if not _has_cf_credentials():
        print("Step 1: Log in to Cloudflare (opens browser)...")
        print("  Select the domain you want to use.\n")
        try:
            r = subprocess.run([cf, "tunnel", "login"],
                               creationflags=_WIN_FLAGS)
            if r.returncode != 0:
                print("Login failed. Try again after fixing the issue.")
                return 1
        except KeyboardInterrupt:
            print("\nSetup cancelled.")
            return 0
        print("  ✓ Login successful.\n")
    else:
        print("  ✓ Cloudflare credentials found, skipping login.\n")

    # Ask for tunnel name
    cfg = _load_tunnel_config()
    default_name = cfg.get("tunnel_name", "neuron-mcp")
    tunnel_name = input(f"  Tunnel name [{default_name}]: ").strip() or default_name

    # Create the tunnel
    print(f"\nStep 2: Creating tunnel '{tunnel_name}'...")
    try:
        r = subprocess.run([cf, "tunnel", "create", tunnel_name],
                           capture_output=True, text=True, creationflags=_WIN_FLAGS)
        if r.returncode != 0 and "already exists" not in (r.stderr or ""):
            print(f"  Failed: {r.stderr or r.stdout}")
            print("  If the tunnel already exists, that's fine — we'll use it.")
        else:
            print(f"  ✓ Tunnel '{tunnel_name}' created.\n")
    except KeyboardInterrupt:
        print("\nSetup cancelled.")
        return 0

    # Ask for domain route
    print("Step 3: Route your domain to the tunnel.")
    domain = input("  Domain (e.g. mcp.yourdomain.com) or Enter to skip: ").strip()
    if domain:
        print(f"  Routing {domain} → {tunnel_name}...")
        try:
            r = subprocess.run([cf, "tunnel", "route", "dns", tunnel_name, domain],
                               capture_output=True, text=True, creationflags=_WIN_FLAGS)
            if r.returncode == 0 or "already exists" in (r.stderr or "").lower():
                print(f"  ✓ DNS route configured.\n")
            else:
                print(f"  ⚠ DNS route failed: {r.stderr or r.stdout}")
                print(f"  You can configure it manually later:")
                print(f"    cloudflared tunnel route dns {tunnel_name} {domain}\n")
        except KeyboardInterrupt:
            print("\nSetup partial — tunnel exists but DNS not routed.")
            print(f"  Run later: cloudflared tunnel route dns {tunnel_name} {domain}")

    # Save config
    cfg["tunnel_name"] = tunnel_name
    if domain:
        cfg["domain"] = domain
    _save_tunnel_config(cfg)
    print(f"  ✓ Config saved to {_tunnel_config_path()}")
    print(f"\n  Next: neuron tunnel --named {tunnel_name} --port <PORT>\n")
    return 0


# --- Backend: Ngrok ---

def _run_ngrok(url: str, *, on_url=None) -> int:
    """Run ngrok to expose a local port."""
    ngrok = shutil.which("ngrok")
    if not ngrok:
        print(
            "ngrok not found. Install it, then re-run:\n"
            "  Windows : winget install --id Ngrok.Ngrok\n"
            "  macOS   : brew install ngrok\n"
            "  Linux   : https://ngrok.com/download\n",
            file=sys.stderr)
        return 2

    print(f"Opening an ngrok tunnel to {url}...", flush=True)
    print("Add the printed https://… URL + '/mcp' as your MCP connector.\n", flush=True)

    proc = subprocess.Popen(
        [ngrok, "http", url],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        encoding="utf-8", errors="replace", creationflags=_WIN_FLAGS)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            m = _NGROK_RE.search(line)
            if m:
                public = m.group(0)
                print(f"\n  ==> MCP connector URL: {public}/mcp\n", flush=True)
                if on_url:
                    on_url(public)
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        raise


# --- Watchdog (shared) ---

def _watchdog(run_fn, url: str, *, max_restarts: int = 0, on_url=None) -> int:
    """Supervise a tunnel process: restart on exit, exponential backoff."""
    restarts = 0
    delay = 2.0
    while True:
        started = time.monotonic()
        try:
            rc = run_fn(url, on_url=on_url)
        except KeyboardInterrupt:
            print("\n[tunnel] stopped by user.")
            return 0
        uptime = time.monotonic() - started
        restarts += 1
        if max_restarts and restarts > max_restarts:
            print(f"[tunnel] exited (rc={rc}) — max restarts reached, giving up.",
                  file=sys.stderr)
            return rc or 1
        delay = 2.0 if uptime > 120 else min(delay * 2, 60.0)
        print(f"[tunnel] exited (rc={rc}, up {uptime:.0f}s) — "
              f"reopening in {delay:.0f}s (restart #{restarts})…", flush=True)
        try:
            time.sleep(delay)
        except KeyboardInterrupt:
            print("\n[tunnel] stopped by user.")
            return 0


# --- Helpers ---

def _print_install_cf():
    print(
        "cloudflared not found. Install it, then re-run:\n"
        "  Windows : winget install --id Cloudflare.cloudflared\n"
        "  macOS   : brew install cloudflared\n"
        "  Linux   : https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/",
        file=sys.stderr)


def _auto_detect_backend(args) -> str:
    """Choose the best tunnel backend automatically.

    Priority: explicit flag → named tunnel configured → cloudflared available → ngrok.
    """
    if args.quick:
        return "cf-quick"
    if args.ngrok:
        return "ngrok"
    if args.named:
        return "cf-named"
    if args.setup:
        return "setup"

    # Auto-detect
    cfg = _load_tunnel_config()
    if cfg.get("tunnel_name") and _has_cf_credentials():
        return "cf-named"
    if shutil.which("cloudflared"):
        return "cf-quick"
    if shutil.which("ngrok"):
        return "ngrok"
    return "none"


# --- Main ---

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="neuron tunnel",
        description="Expose a local port over public HTTPS (Cloudflare or ngrok).")
    ap.add_argument("--port", type=int, default=8000,
                    help="local port to expose (default 8000).")
    ap.add_argument("--host", default="127.0.0.1",
                    help="local host (default 127.0.0.1).")
    ap.add_argument("--once", action="store_true",
                    help="run the tunnel a single time (no watchdog).")
    ap.add_argument("--max-restarts", type=int, default=0,
                    help="watchdog: stop after N restarts (0 = unlimited).")
    # Backend selection
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--quick", action="store_true",
                       help="force Cloudflare quick tunnel (ephemeral URL).")
    group.add_argument("--named", type=str, metavar="TUNNEL",
                       help="use a named Cloudflare tunnel (persistent URL).")
    group.add_argument("--ngrok", action="store_true",
                       help="force ngrok backend.")
    group.add_argument("--setup", action="store_true",
                       help="interactive first-time named tunnel setup.")
    a = ap.parse_args(argv)

    # Setup flow
    if a.setup:
        return _setup_cf_named()

    backend = _auto_detect_backend(a)
    url = f"http://{a.host}:{a.port}"

    if backend == "none":
        print("No tunnel backend found. Install one of:\n"
              "  • cloudflared (recommended): winget install --id Cloudflare.cloudflared\n"
              "  • ngrok: https://ngrok.com/download\n",
              file=sys.stderr)
        return 2

    # Header
    print(f"Neuron tunnel → {url}", flush=True)
    print("Add the printed https://… URL + '/mcp' as your MCP connector "
          "(Streamable HTTP, not /sse).\n", flush=True)

    if backend == "cf-named":
        cfg = _load_tunnel_config()
        tunnel_name = a.named or cfg.get("tunnel_name", "neuron-mcp")
        domain = cfg.get("domain", "")
        if domain:
            print(f"Using named tunnel '{tunnel_name}' → {domain}\n", flush=True)
        else:
            print(f"Using named tunnel '{tunnel_name}'\n", flush=True)
        run_fn = lambda url, on_url=None: _run_cf_named(tunnel_name, url, on_url=on_url)
    elif backend == "cf-quick":
        print("Quick tunnel — no uptime guarantee. URL changes on restart.\n", flush=True)
        run_fn = _run_cf_quick
    elif backend == "ngrok":
        run_fn = _run_ngrok
    else:
        print(f"Unknown backend: {backend}", file=sys.stderr)
        return 2

    if a.once:
        try:
            return run_fn(url)
        except KeyboardInterrupt:
            return 0

    return _watchdog(run_fn, url, max_restarts=a.max_restarts)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
