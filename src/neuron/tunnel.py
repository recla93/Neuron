"""`neuron tunnel` — expose a local port over public HTTPS via cloudflared.

Pairs with `neuron bridge` (which serves the stdio server over local HTTP): run
the bridge, then this, and add the printed ``https://…/mcp`` URL as an MCP
connector (ChatGPT Dev Mode, Perplexity, …). cloudflared is cross-platform, so
this replaces the Windows-only tunnel wrapper that used to live in
scripts/configuration.ps1.

Watchdog (T75): Cloudflare *quick* tunnels (`cloudflared tunnel --url …`,
``*.trycloudflare.com``) come with **no uptime guarantee** — Cloudflare drops
them after idle periods or routine edge maintenance, which is why the connector
"randomly" dies after a while. ``--watch`` (the default) supervises the
process: when cloudflared exits, it is relaunched with exponential backoff and
the NEW public URL is printed (quick-tunnel URLs change on every restart — the
connector URL must be updated, which is also why a named tunnel is the real
long-term fix; see ``--named``'s help).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time

__all__ = ["main"]

_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def _run_once(cf: str, url: str, *, on_url=None) -> int:
    """Run cloudflared once, streaming output and surfacing the public URL."""
    # CREATE_NO_WINDOW (T81): when this runs under the GUI (a windowless
    # parent), a console child would otherwise pop up its own CMD window.
    flags = 0x08000000 if os.name == "nt" else 0
    proc = subprocess.Popen(
        [cf, "tunnel", "--url", url],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        encoding="utf-8", errors="replace", creationflags=flags)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            m = _URL_RE.search(line)
            if m:
                public = m.group(0)
                print(f"\n  ==> MCP connector URL: {public}/mcp\n", flush=True)
                if on_url:
                    on_url(public)
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        raise


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(
        prog="neuron tunnel",
        description="Expose a local port over public HTTPS using cloudflared.")
    ap.add_argument("--port", type=int, default=8000, help="local port to expose (default 8000).")
    ap.add_argument("--host", default="127.0.0.1", help="local host (default 127.0.0.1).")
    ap.add_argument("--once", action="store_true",
                    help="run cloudflared a single time (old behaviour, no watchdog).")
    ap.add_argument("--max-restarts", type=int, default=0,
                    help="watchdog: stop after N restarts (0 = unlimited).")
    a = ap.parse_args(argv)

    cf = shutil.which("cloudflared")
    if not cf:
        print(
            "cloudflared not found. Install it, then re-run:\n"
            "  Windows : winget install --id Cloudflare.cloudflared\n"
            "  macOS   : brew install cloudflared\n"
            "  Linux   : https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/",
            file=sys.stderr)
        return 2

    url = f"http://{a.host}:{a.port}"
    print(f"Opening a Cloudflare tunnel to {url} (start `neuron bridge` first if you haven't).", flush=True)
    print("Add the printed https://… URL + '/mcp' as your MCP connector (Streamable HTTP, not /sse).", flush=True)
    if not a.once:
        print("Watchdog ON: quick tunnels have no uptime guarantee — if Cloudflare "
              "drops the connection, it reopens automatically (NOTE: the public "
              "URL changes on every restart, update the connector).\n", flush=True)

    if a.once:
        try:
            return _run_once(cf, url)
        except KeyboardInterrupt:
            return 0

    restarts = 0
    delay = 2.0
    while True:
        started = time.monotonic()
        try:
            rc = _run_once(cf, url)
        except KeyboardInterrupt:
            print("\n[tunnel] stopped by user.")
            return 0
        uptime = time.monotonic() - started
        restarts += 1
        if a.max_restarts and restarts > a.max_restarts:
            print(f"[tunnel] exited (rc={rc}) — max restarts reached, giving up.",
                  file=sys.stderr)
            return rc or 1
        # Healthy long run → reset the backoff; rapid-fail loop → grow it.
        delay = 2.0 if uptime > 120 else min(delay * 2, 60.0)
        print(f"[tunnel] cloudflared exited (rc={rc}, up {uptime:.0f}s) — "
              f"reopening in {delay:.0f}s (restart #{restarts})…", flush=True)
        try:
            time.sleep(delay)
        except KeyboardInterrupt:
            print("\n[tunnel] stopped by user.")
            return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
