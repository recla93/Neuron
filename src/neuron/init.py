"""``neuron init`` — wire Neuron's guidance into an MCP client's always-on prompt.

Why this exists
---------------
An MCP server cannot *push* a prompt at startup: the protocol is pull-based, and its
only server-side hook is the ``instructions`` field returned at initialize. Some
clients (notably **OpenCode**) do not surface that field to the model, so the skills
never load. Those clients DO have a native always-on hook — a client-side
``instructions`` list of files appended to the system prompt every session.

``neuron init`` copies a compact *opener* skill to a stable location and adds it to
the client's instructions list, so Neuron's usage contract loads on every session.
The opener stays small (token-friendly); the full playbook remains available on
demand via the ``help`` tool and the ``neuron://skill/...`` MCP resources.

Design
------
- **stdlib only** (no fastembed/mcp import) so it is fast and testable anywhere.
- **idempotent**: re-running never duplicates entries; ``--force`` refreshes the file.
- **non-destructive**: backs up an existing config; refuses to touch invalid JSON.
- **extensible**: OpenCode is the v1 target; add more clients to ``CLIENTS`` later.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

__all__ = ["init_opencode", "CLIENTS", "main"]

# The opener lives inside the package (see pyproject package-data) and mirrors the
# repo-root skills/ source.
_OPENER_PARTS = ("skills", "neuron-opener.md")


def _read_opener_text() -> str:
    """Return the opener markdown, from the wheel or a source checkout."""
    try:
        from importlib.resources import files
        return files("neuron").joinpath(*_OPENER_PARTS).read_text(encoding="utf-8")
    except Exception:
        # src/neuron/init.py -> parents[2] == repo root
        root = Path(__file__).resolve().parents[2]
        return root.joinpath(*_OPENER_PARTS).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# OpenCode
# ---------------------------------------------------------------------------

def _opencode_paths(home: Path) -> tuple[Path, Path]:
    """(config file, opener destination) for OpenCode under a given HOME."""
    base = home / ".config" / "opencode"
    return base / "opencode.json", base / "skills" / "neuron" / "neuron-opener.md"


def init_opencode(home: Path | None = None, dry_run: bool = False, force: bool = False) -> dict:
    """Copy the opener and wire it into OpenCode's ``instructions[]``.

    Returns a report dict: ``{"actions": [str, ...], "ok": bool}``. ``ok`` is False
    only when we deliberately did NOT finish (e.g. unparseable config) so the caller
    can print manual steps and exit non-zero."""
    home = home or Path.home()
    cfg_path, skill_dest = _opencode_paths(home)
    actions: list[str] = []

    # 1) Copy the opener to a stable, client-local path.
    if skill_dest.exists() and not force:
        actions.append(f"skill already present: {skill_dest}")
    elif dry_run:
        actions.append(f"would write skill: {skill_dest}")
    else:
        skill_dest.parent.mkdir(parents=True, exist_ok=True)
        skill_dest.write_text(_read_opener_text(), encoding="utf-8")
        actions.append(f"wrote skill: {skill_dest}")

    # 2) Merge the path into opencode.json instructions[] (dedup, backup).
    data: dict = {}
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            actions.append(
                f"[skip] {cfg_path} is not valid JSON — add this to its instructions[] "
                f"by hand: {skill_dest}"
            )
            return {"actions": actions, "ok": False}

    instr = data.get("instructions")
    if not isinstance(instr, list):
        instr = [] if instr is None else [instr]

    dest_str = str(skill_dest)
    if dest_str in instr:
        actions.append("instructions already wired")
    elif dry_run:
        actions.append(f"would add to instructions[]: {dest_str}")
    else:
        instr.append(dest_str)
        data["instructions"] = instr
        if cfg_path.exists():
            shutil.copy2(cfg_path, str(cfg_path) + ".neuron-init.bak")
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        actions.append(f"added to instructions[]: {dest_str}")

    return {"actions": actions, "ok": True}


# Registry of supported clients (v1: OpenCode only).
CLIENTS = {
    "opencode": init_opencode,
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="neuron init",
        description="Load Neuron's usage guidance into an MCP client's always-on prompt.",
    )
    ap.add_argument("--client", default="opencode", choices=sorted(CLIENTS),
                    help="Target client (v1: opencode).")
    ap.add_argument("--dry-run", action="store_true", help="Print what would change; write nothing.")
    ap.add_argument("--force", action="store_true", help="Overwrite the opener file if it exists.")
    args = ap.parse_args(argv)

    report = CLIENTS[args.client](dry_run=args.dry_run, force=args.force)
    for a in report["actions"]:
        print("  " + a)
    if report["ok"]:
        tail = " (dry run — nothing written)" if args.dry_run else ""
        print(f"Done: {args.client} is wired to load Neuron every session{tail}.")
        return 0
    print("Finished with manual steps needed (see above).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
