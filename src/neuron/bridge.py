"""Neuron HTTP bridge launcher — expose the stdio server over HTTP for ChatGPT & co.

The hard parts of "run the bridge" are (1) launching the *right* Neuron, and
(2) getting `mcp-proxy` without a manual install. This script does both:

  * it launches Neuron with **this interpreter** (``sys.executable -m neuron``),
    so if you run this script with the venv where Neuron is installed, the child
    resolves correctly — no more bare ``python3 -m neuron`` hitting the wrong
    Python (the usual cause of mcp-proxy's "McpError: Connection closed": the
    child died with "No module named neuron");
  * it finds a way to run `mcp-proxy` — preferring ``uvx`` / ``uv`` / ``pipx``,
    which fetch it on demand, so nothing has to be pip-installed by hand;
  * it **preflights** the Neuron command (starts it briefly) and, if it dies,
    shows you the real error instead of a cryptic proxy stack trace.

Usage:

    # run with the SAME python where Neuron is installed (e.g. the install venv)
    python scripts/bridge.py                      # serves http://127.0.0.1:8000/mcp (+ /sse)
    python scripts/bridge.py --port 9000
    python scripts/bridge.py --print-cmd          # show what it would run, don't run
    python scripts/bridge.py -- <custom neuron launch command>   # override the child

Then expose the port over public HTTPS (remote connectors can't reach
localhost) — e.g.  ``cloudflared tunnel --url http://127.0.0.1:8000`` — and add
the resulting ``https://…/mcp`` URL as a connector. Use the ``/mcp`` (Streamable
HTTP) endpoint, NOT ``/sse``: Cloudflare buffers the legacy SSE handshake so the
``/sse`` URL times out behind a tunnel. See docs/BRIDGE.md.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import time

# Make Unicode output safe on legacy Windows consoles (cp1252): reconfigure
# stdout/stderr to UTF-8 so the glyphs printed below never raise
# UnicodeEncodeError. Best-effort and never fatal.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

WIN = os.name == "nt"


def resolve_neuron_cmd(override: list[str] | None) -> list[str]:
    """Return the command that launches Neuron's stdio server.

    Priority: explicit override → the installed venv venv (via slug + LOCALAPPDATA)
    → the Windows install's run_mcp.bat → this interpreter (preflight will then
    surface a clear 'No module named neuron' if Neuron isn't there)."""
    if override:
        return override
    if WIN:
        slug = os.environ.get("NEURON_SLUG", "neuron5")
        local = os.environ.get("LOCALAPPDATA", "")
        venv_py = os.path.join(local, "Programs", slug, ".venv", "Scripts", "python.exe")
        if os.path.isfile(venv_py):
            return [venv_py, "-m", "neuron"]
        bat = os.path.join(local, "Programs", slug, "scripts", "run_mcp.bat")
        if os.path.isfile(bat):
            return ["cmd", "/c", bat]
    if importlib.util.find_spec("neuron") is not None:
        return [sys.executable, "-m", "neuron"]
    return [sys.executable, "-m", "neuron"]


def resolve_proxy_runner() -> list[str] | None:
    """Find a way to run `mcp-proxy`, preferring on-demand runners so nothing
    needs a manual install."""
    if shutil.which("mcp-proxy"):
        return ["mcp-proxy"]
    if shutil.which("uvx"):
        return ["uvx", "mcp-proxy"]
    if shutil.which("uv"):
        return ["uv", "tool", "run", "mcp-proxy"]
    if shutil.which("pipx"):
        return ["pipx", "run", "mcp-proxy"]
    return None


def preflight(neuron_cmd: list[str], seconds: float = 3.0) -> bool:
    """Start the Neuron command briefly; if it exits immediately, show why.
    A healthy stdio MCP server stays alive waiting for input."""
    print(f"Preflight: starting Neuron → {' '.join(neuron_cmd)}")
    try:
        proc = subprocess.Popen(
            neuron_cmd, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        print(f"  ✗ cannot launch it: {exc}")
        return False
    time.sleep(seconds)
    if proc.poll() is None:
        proc.kill()
        print("  ✓ Neuron starts and stays alive.")
        return True
    err = (proc.stderr.read() or b"").decode(errors="replace").strip()
    print(f"  ✗ Neuron exited immediately (code {proc.returncode}). Its error:\n")
    print("    " + "\n    ".join((err or "(no stderr)").splitlines()[-15:]))
    print("\n  → Fix that first. Most often: run this script with the Python where")
    print("    Neuron is installed (its venv), or pass your launch command after '--'.")
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Launch the Neuron→HTTP bridge (mcp-proxy in server mode).")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port (default 8000).")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default 127.0.0.1).")
    parser.add_argument("--no-check", action="store_true", help="Skip the Neuron preflight.")
    parser.add_argument("--print-cmd", action="store_true",
                        help="Print the full command and exit (don't run).")
    parser.add_argument("neuron_cmd", nargs=argparse.REMAINDER,
                        help="Optional: everything after '--' is the Neuron launch command.")
    args = parser.parse_args(argv)

    override = args.neuron_cmd
    if override and override[0] == "--":
        override = override[1:]
    neuron_cmd = resolve_neuron_cmd(override or None)

    proxy = resolve_proxy_runner()
    if proxy is None:
        print("No way to run 'mcp-proxy' was found.\n"
              "Install a runner (any one):\n"
              "  • uv (recommended, no pip needed):\n"
              "      Windows : irm https://astral.sh/uv/install.ps1 | iex\n"
              "      macOS/Linux : curl -LsSf https://astral.sh/uv/install.sh | sh\n"
              "  • pipx : python -m pip install --user pipx\n"
              "Then re-run this script.", file=sys.stderr)
        return 2

    # Quick smoke test: can the proxy runner actually launch mcp-proxy?
    try:
        r = subprocess.run(proxy + ["--version"], capture_output=True, timeout=15)
        if r.returncode != 0:
            print(f"  [!] '{' '.join(proxy)}' did not return a valid mcp-proxy.",
                  file=sys.stderr)
            print("      It may still work at runtime, but check your network / runner install.",
                  file=sys.stderr)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print(f"  [!] '{' '.join(proxy)}' — {exc}", file=sys.stderr)

    full = proxy + [f"--port={args.port}", f"--host={args.host}", "--"] + neuron_cmd
    url = f"http://{args.host}:{args.port}/mcp"

    if args.print_cmd:
        print(" ".join(full))
        return 0

    if not args.no_check and not preflight(neuron_cmd):
        return 1

    print(f"\nStarting bridge via: {' '.join(proxy)}")
    print(f"  local endpoint : {url}")
    print(f"  next step      : expose it over public HTTPS, e.g.")
    print(f"                   cloudflared tunnel --url http://{args.host}:{args.port}")
    print(f"  then add the https://…/mcp URL as an MCP connector (Perplexity, ChatGPT Dev Mode).")
    print(f"  Use /mcp (Streamable HTTP), not /sse — Cloudflare buffers the SSE handshake.\n")
    try:
        return subprocess.call(full)
    except KeyboardInterrupt:
        return 0
    except FileNotFoundError as exc:
        print(f"Failed to start the proxy: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
