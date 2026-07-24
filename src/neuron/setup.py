"""`neuron setup` — universal lifecycle CLI (T63 / ADR-007, v5.4).

The cross-platform install/repair/uninstall entry point. Windows keeps its
PowerShell Configuration Center as a friendly wrapper; macOS and Linux users
get the same lifecycle with:

    pipx install neuron        # or: pip install neuron (in a venv)
    neuron setup               # interactive menu
    neuron setup --register-all --yes      # non-interactive install
    neuron setup --repair                  # doctor --fix
    neuron setup --status                  # doctor, read-only (CI-friendly)
    neuron setup --uninstall [--purge-data] --yes

Everything reuses the tested registration engine (neuron.clients): register,
doctor, deregister. Stdlib-only, numbered prompts (no curses), never destroys
data unless --purge-data/opt-in is explicit.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

from neuron import clients as C

__all__ = ["do_install", "do_repair", "do_status", "do_uninstall", "main"]


def _graphs_dir() -> str:
    # Delegates to neuron.config (single source of truth, P0 #3).
    from neuron.config import graphs_dir
    return graphs_dir()


def _ask(prompt: str, yes: bool) -> bool:
    if yes:
        return True
    try:
        return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes", "s", "si", "sì")
    except EOFError:
        return False


def do_install(slug: str, python_exe: str, yes: bool) -> int:
    print(f"\nRegistering Neuron (slug '{slug}', python: {python_exe}) in every detected client...\n")
    results = C.register_all(slug, python_exe)
    for r in results:
        print(r.line())
    print("\nHealth check:")
    lines, problems = C.doctor(slug, python_exe)
    for ln in lines:
        print(ln)
    # Embedding model pre-warm (mandatory, ~380MB one-time download)
    print("\nPre-downloading the embedding model (~380MB, one-time)...")
    try:
        from neuron.server import _get_embedder   # heavy import, on purpose here only
        _get_embedder()
        print("  [OK] model cached.")
    except Exception as e:
        print(f"  [!] pre-warm failed ({e}) — it will retry on first use.")
    print("\nDone. Restart your AI apps to load Neuron.")
    return 0 if problems == 0 else 1


def do_repair(slug: str, python_exe: str) -> int:
    lines, problems = C.doctor(slug, python_exe, fix=True)
    print("Repair (doctor --fix):")
    for ln in lines:
        print(ln)
    print(f"{problems} problem(s) left." if problems else "All good.")
    return 1 if problems else 0


def do_status(slug: str, python_exe: str) -> int:
    lines, problems = C.doctor(slug, python_exe)
    print(f"Neuron status (slug '{slug}'):")
    for ln in lines:
        print(ln)
    print(f"{problems} problem(s)." if problems else "All good.")
    return 1 if problems else 0


def do_uninstall(slug: str, purge_data: bool, yes: bool) -> int:
    if not _ask(f"Remove the '{slug}' registration from every AI client?", yes):
        print("Aborted."); return 1
    for r in C.deregister_all(slug):
        print(r.line())
    gd = _graphs_dir()
    if purge_data or (os.path.isdir(gd) and _ask(
            f"ALSO DELETE the memory store at {gd}? (irreversible)", yes=False if not purge_data else yes)):
        if purge_data and not _ask(f"Confirm DELETING {gd}?", yes):
            print("Data kept."); return 0
        try:
            shutil.rmtree(gd)
            print(f"  [OK] removed {gd}")
        except OSError as e:
            print(f"  [!] could not remove {gd}: {e}")
    else:
        print(f"  Memory store kept: {gd}")
    print("Done. Uninstall the package itself with: pipx uninstall neuron (or pip uninstall neuron)")
    return 0


def _menu(slug: str, python_exe: str) -> int:
    while True:
        print(f"\n=== Neuron setup (slug '{slug}') ===\n"
              "  1) Install / register in my AI clients\n"
              "  2) Repair (scan configs + running servers, fix problems)\n"
              "  3) Status (read-only health check)\n"
              "  4) Uninstall (de-register; data wipe is opt-in)\n"
              "  5) Exit")
        try:
            choice = input("> ").strip()
        except EOFError:
            return 0
        if choice == "1":
            do_install(slug, python_exe, yes=False)
        elif choice == "2":
            do_repair(slug, python_exe)
        elif choice == "3":
            do_status(slug, python_exe)
        elif choice == "4":
            do_uninstall(slug, purge_data=False, yes=False)
        elif choice == "5" or choice == "":
            return 0


def main(argv: list[str]) -> int:
    # UTF-8 self-guard (arrows/glyphs vs cp1252 consoles), same as clients.cli
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="neuron setup")
    ap.add_argument("--slug", default=os.environ.get("NEURON_SLUG", "neuron"))
    ap.add_argument("--python", dest="python_exe", default=None,
                    help="python that runs the server (default: the installed venv's)")
    ap.add_argument("--register-all", action="store_true")
    ap.add_argument("--repair", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--purge-data", action="store_true")
    ap.add_argument("--yes", action="store_true", help="non-interactive: assume yes")
    a = ap.parse_args(argv)
    if not a.python_exe:
        a.python_exe = C.default_server_python(a.slug)

    if a.register_all:
        return do_install(a.slug, a.python_exe, a.yes)
    if a.repair:
        return do_repair(a.slug, a.python_exe)
    if a.status:
        return do_status(a.slug, a.python_exe)
    if a.uninstall:
        return do_uninstall(a.slug, a.purge_data, a.yes)
    return _menu(a.slug, a.python_exe)
