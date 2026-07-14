"""`neuron tunnel` — expose a local port over public HTTPS via cloudflared.

Pairs with `neuron bridge` (which serves the stdio server over local HTTP): run
the bridge, then this, and add the printed ``https://…/mcp`` URL as an MCP
connector (ChatGPT Dev Mode, Perplexity, …). cloudflared is cross-platform, so
this replaces the Windows-only tunnel wrapper that used to live in
scripts/configuration.ps1.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys

__all__ = ["main"]


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(
        prog="neuron tunnel",
        description="Expose a local port over public HTTPS using cloudflared.")
    ap.add_argument("--port", type=int, default=8000, help="local port to expose (default 8000).")
    ap.add_argument("--host", default="127.0.0.1", help="local host (default 127.0.0.1).")
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
    print(f"Opening a Cloudflare tunnel to {url} (start `neuron bridge` first if you haven't).")
    print("Add the printed https://… URL + '/mcp' as your MCP connector (Streamable HTTP, not /sse).\n")
    try:
        return subprocess.call([cf, "tunnel", "--url", url])
    except KeyboardInterrupt:
        return 0
