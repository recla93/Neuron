"""Central MCP client registration engine — ``neuron register`` / ``neuron doctor``.

Piano 05 / Parte B (B1, B2, B3, B4, B6, B7). Single source of truth for HOW and
WHERE Neuron gets registered in every supported AI client. The PowerShell
installers (``install.ps1``, ``scripts/configuration.ps1``) call into this module
instead of carrying their own parallel (and drifting) implementations.

Design rules (stdlib-only, like ``init.py``):
- Never destructive: non-destructive merge, ``.neuron-bak`` backup before every
  write, verify-after-write with rollback on failure.
- JSONC (comments / trailing commas) is READ for diagnosis but never rewritten:
  we'd lose the user's comments. In that case we print a *valid* manual snippet
  (produced by ``json.dumps``, so backslashes are properly escaped — the raw
  string interpolation of the old installer printed invalid JSON).
- Claude Code: prefer the official ``claude mcp add`` CLI. ``~/.claude.json`` is
  Claude Code's LIVE state file — editing it directly can be silently overwritten
  when the app exits (entry "disappears after restart"). Direct edit is only the
  fallback when the CLI is not on PATH.
- Claude Desktop: the config may live under %APPDATA%\\Claude (classic install)
  OR under the MSIX/Store package LocalCache
  (%LOCALAPPDATA%\\Packages\\Claude_*\\LocalCache\\Roaming\\Claude). Both are probed.
- Every write is recorded in the install manifest
  (<install_dir>/install-manifest.json) so uninstall/doctor know exactly what to
  undo instead of guessing.
"""

from __future__ import annotations

import glob as _glob
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Callable

log = logging.getLogger("neuron.clients")

__all__ = [
    "Result", "register", "register_all", "deregister", "deregister_all",
    "doctor", "process_doctor", "default_server_python", "cli", "KNOWN_SLUGS",
]

# ---------------------------------------------------------------------------
# Helpers: tolerant read, strict write
# ---------------------------------------------------------------------------


def read_text(path: str) -> str:
    """Read a text file tolerating a UTF-8 BOM."""
    with open(path, "r", encoding="utf-8-sig") as fh:
        return fh.read()


def strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments and trailing commas — for READING only.

    String-aware: comment markers inside JSON strings are preserved.
    """
    out: list[str] = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    cleaned = "".join(out)
    # trailing commas:  , }   , ]
    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
    return cleaned


def load_config(path: str) -> tuple[Any, str]:
    """Return ``(data, kind)`` where kind is 'json' | 'jsonc' | 'invalid' | 'missing'.

    'json'  → plain JSON, safe to rewrite programmatically.
    'jsonc' → parseable only after comment/trailing-comma stripping: read-only
              (rewriting would destroy the user's comments).
    """
    if not os.path.exists(path):
        return None, "missing"
    raw = read_text(path)
    if not raw.strip():
        return {}, "json"
    try:
        return json.loads(raw), "json"
    except ValueError:
        pass
    try:
        return json.loads(strip_jsonc(raw)), "jsonc"
    except ValueError:
        return None, "invalid"


def save_json(path: str, data: Any) -> None:
    """Strict JSON write: UTF-8 without BOM (Claude Code's JSON.parse chokes on
    a BOM), 2-space indent, atomic-ish via temp file + replace."""
    tmp = path + ".neuron-tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def backup(path: str) -> str | None:
    if os.path.exists(path):
        bak = path + ".neuron-bak"
        shutil.copyfile(path, bak)
        return bak
    return None


def manual_snippet(nested_keys: list[str], key: str, entry: dict) -> str:
    """A hand-paste snippet that is ALWAYS valid JSON (json.dumps escapes the
    backslashes — the old installer printed raw Windows paths, invalid JSON)."""
    inner: Any = {key: entry}
    for k in reversed(nested_keys):
        inner = {k: inner}
    return json.dumps(inner, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Client registry (B1/B2) — single source of truth, mirrored by _neuron_paths.ps1
# ---------------------------------------------------------------------------


def _env(name: str) -> str:
    return os.environ.get(name, "")


def claude_desktop_candidates() -> list[str]:
    """B2: classic %APPDATA% install AND the Microsoft Store (MSIX) package.
    T63: plus the macOS and Linux locations, so `neuron setup` is universal."""
    cands = []
    appdata = _env("APPDATA")
    if appdata:
        cands.append(os.path.join(appdata, "Claude", "claude_desktop_config.json"))
    localapp = _env("LOCALAPPDATA")
    if localapp:
        cands.extend(
            os.path.join(p, "LocalCache", "Roaming", "Claude", "claude_desktop_config.json")
            for p in sorted(_glob.glob(os.path.join(localapp, "Packages", "Claude_*")))
        )
    if sys.platform == "darwin":
        cands.append(_home("Library", "Application Support", "Claude",
                           "claude_desktop_config.json"))
    elif os.name != "nt":
        cands.append(_home(".config", "Claude", "claude_desktop_config.json"))
    return cands


def pick_existing(candidates: list[str]) -> tuple[str | None, list[str]]:
    """Return (chosen, all_existing). Multiple hits → most recently modified wins
    (the caller should surface a warning listing the others)."""
    existing = [p for p in candidates if os.path.exists(p)]
    if not existing:
        return None, []
    chosen = max(existing, key=lambda p: os.path.getmtime(p))
    return chosen, existing


def _home(*parts: str) -> str:
    return os.path.join(os.path.expanduser("~"), *parts)


# Each spec: candidates() -> list[str] (first existing wins unless noted),
# keys = nested path to the server map, entry(python_exe) -> dict, format.
CLIENTS: dict[str, dict[str, Any]] = {
    "claude-desktop": {
        "label": "Claude Desktop",
        "candidates": claude_desktop_candidates,
        "keys": ["mcpServers"],
        "entry": lambda py: {"command": py, "args": ["-m", "neuron"]},
        "format": "json",
        "create_if_missing": False,
    },
    "claude-code": {
        "label": "Claude Code",
        "candidates": lambda: [_home(".claude.json")],
        "keys": ["mcpServers"],
        "entry": lambda py: {"command": py, "args": ["-m", "neuron"]},
        "format": "json",
        "create_if_missing": False,
        "live_state_file": True,   # B3: prefer `claude mcp add`
    },
    "cursor": {
        "label": "Cursor",
        "candidates": lambda: [_home(".cursor", "mcp.json")],
        "keys": ["mcpServers"],
        "entry": lambda py: {"command": py, "args": ["-m", "neuron"]},
        "format": "json",
        "create_if_missing": True,
    },
    "vscode": {
        "label": "VS Code",
        "candidates": lambda: (
            [os.path.join(_env("APPDATA"), "Code", "User", "settings.json")]
            if _env("APPDATA")
            else [_home("Library", "Application Support", "Code", "User", "settings.json")]
            if sys.platform == "darwin"
            else [_home(".config", "Code", "User", "settings.json")]
        ),
        "keys": ["mcp", "servers"],
        "entry": lambda py: {"type": "stdio", "command": py, "args": ["-m", "neuron"]},
        "format": "json",   # frequently JSONC in the wild → manual snippet path
        "create_if_missing": False,
    },
    "opencode": {
        "label": "OpenCode",
        "candidates": lambda: [_home(".config", "opencode", "opencode.json")],
        "keys": ["mcp"],
        "entry": lambda py: {"command": [py, "-m", "neuron"], "type": "local"},
        "format": "json",
        "create_if_missing": True,
    },
    "zed": {
        "label": "Zed",
        "candidates": lambda: (
            [os.path.join(_env("APPDATA"), "Zed", "settings.json")]
            if _env("APPDATA") else [_home(".config", "zed", "settings.json")]
        ),
        "keys": ["context_servers"],
        "entry": lambda py: {"command": py, "args": ["-m", "neuron"]},
        "format": "json",
        "create_if_missing": False,
    },
    "codex": {
        "label": "Codex CLI",
        "candidates": lambda: [_home(".codex", "config.toml")],
        "keys": ["mcp_servers"],
        "entry": lambda py: {"command": py, "args": ["-m", "neuron"]},
        "format": "toml",
        "create_if_missing": True,
    },
}


# ---------------------------------------------------------------------------
# TOML (Codex) — targeted section replace/append, never whole-file overwrite (B5)
# ---------------------------------------------------------------------------


def toml_upsert_section(text: str, section: str, body_lines: list[str]) -> str:
    """Replace the ``[section]`` block if present, else append it. Everything
    else in the file is preserved byte-for-byte (the old installer overwrote the
    ENTIRE config.toml, destroying other servers)."""
    new_block = f"[{section}]\n" + "\n".join(body_lines) + "\n"
    pattern = re.compile(
        r"(?ms)^\[" + re.escape(section) + r"\]\s*?\n.*?(?=^\[|\Z)"
    )
    if pattern.search(text):
        # lambda replacement: the block contains Windows backslashes that
        # re.sub would otherwise interpret as escape sequences.
        return pattern.sub(lambda _m: new_block, text, count=1)
    if text and not text.endswith("\n"):
        text += "\n"
    return text + ("\n" if text.strip() else "") + new_block


def codex_entry_lines(python_exe: str) -> list[str]:
    return [
        "command = " + json.dumps(python_exe),   # json string == valid TOML basic string
        "args = [\"-m\", \"neuron\"]",
    ]


# ---------------------------------------------------------------------------
# Install manifest (B7)
# ---------------------------------------------------------------------------


def manifest_path(install_dir: str) -> str:
    return os.path.join(install_dir, "install-manifest.json")


def load_manifest(install_dir: str) -> dict:
    data, kind = load_config(manifest_path(install_dir))
    return data if isinstance(data, dict) else {}


def record_registration(install_dir: str, slug: str, python_exe: str,
                        client: str, path: str, keys: list[str]) -> None:
    if not install_dir:
        return
    try:
        m = load_manifest(install_dir)
        m.setdefault("slug", slug)
        m["python"] = python_exe
        m["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        regs = m.setdefault("registrations", {})
        regs[client] = {"path": path, "keys": keys + [slug]}
        os.makedirs(install_dir, exist_ok=True)
        save_json(manifest_path(install_dir), m)
    except OSError:
        pass   # manifest is best-effort bookkeeping, never fatal


# ---------------------------------------------------------------------------
# Registration (B1/B3/B4)
# ---------------------------------------------------------------------------


class Result:
    def __init__(self, client: str, ok: bool, action: str, detail: str = "",
                 snippet: str = "", path: str = ""):
        self.client, self.ok, self.action = client, ok, action
        self.detail, self.snippet, self.path = detail, snippet, path

    def line(self) -> str:
        mark = "[OK]" if self.ok else ("[--]" if self.action == "skipped" else "[!!]")
        s = f"  {mark} {self.client}: {self.action}"
        if self.detail:
            s += f" — {self.detail}"
        if self.snippet:
            s += "\n       Add this by hand to " + (self.path or "the config") + ":\n"
            s += "\n".join("         " + ln for ln in self.snippet.splitlines())
        return s


def _claude_cli() -> str | None:
    return shutil.which("claude")


def register_claude_code_via_cli(slug: str, python_exe: str,
                                 runner: Callable | None = None) -> bool:
    """B3: `claude mcp add --scope user <slug> <python> -- -m neuron`.
    Returns True when the CLI reported success."""
    run = runner or subprocess.run
    try:
        r = run([_claude_cli() or "claude", "mcp", "add", "--scope", "user",
                 slug, python_exe, "--", "-m", "neuron"],
                capture_output=True, text=True, timeout=60)
        return getattr(r, "returncode", 1) == 0
    except Exception as e:
        log.debug("`claude mcp add` failed: %s", e)
        return False


def register(client: str, slug: str, python_exe: str,
             install_dir: str = "", dry_run: bool = False) -> Result:
    spec = CLIENTS.get(client)
    if spec is None:
        return Result(client, False, "unknown client",
                      f"known: {', '.join(sorted(CLIENTS))}")
    entry = spec["entry"](python_exe)
    keys: list[str] = spec["keys"]

    # -- B3: Claude Code goes through the official CLI when available ----------
    if spec.get("live_state_file") and _claude_cli() and not dry_run:
        if register_claude_code_via_cli(slug, python_exe):
            record_registration(install_dir, slug, python_exe, client,
                                "claude mcp add (CLI)", keys)
            return Result(client, True, "registered via `claude mcp add`",
                          "official CLI — safe against the live state file")
        # CLI present but failed → fall through to the file path with a warning.

    chosen, existing = pick_existing(list(spec["candidates"]()))
    if chosen is None:
        if not spec.get("create_if_missing"):
            return Result(client, True, "skipped", "config not found (app not installed?)")
        chosen = spec["candidates"]()[0]
        os.makedirs(os.path.dirname(chosen), exist_ok=True)

    multi_note = ""
    if len(existing) > 1:
        multi_note = ("multiple configs found, using most recent: "
                      + chosen + " (also: "
                      + ", ".join(p for p in existing if p != chosen) + ")")

    # -- TOML (Codex): targeted section upsert (B5 fix) ------------------------
    if spec["format"] == "toml":
        old = read_text(chosen) if os.path.exists(chosen) else ""
        new = toml_upsert_section(old, f"{keys[0]}.{slug}", codex_entry_lines(python_exe))
        if dry_run:
            return Result(client, True, "would write (dry-run)", multi_note, path=chosen)
        bak = backup(chosen)
        with open(chosen, "w", encoding="utf-8") as fh:
            fh.write(new)
        # verify: our section must be present and the rest preserved
        after = read_text(chosen)
        if f"[{keys[0]}.{slug}]" not in after:
            if bak:
                shutil.copyfile(bak, chosen)
            return Result(client, False, "write verification failed, rolled back", path=chosen)
        record_registration(install_dir, slug, python_exe, client, chosen, keys)
        return Result(client, True, "registered (TOML section upsert)", multi_note, path=chosen)

    # -- JSON ------------------------------------------------------------------
    data, kind = load_config(chosen)
    if kind in ("jsonc", "invalid"):
        # B4: never rewrite JSONC/broken files; hand the user a VALID snippet.
        snip = manual_snippet(keys, slug, entry)
        why = ("config uses comments/trailing commas (JSONC)" if kind == "jsonc"
               else "config is not parseable JSON")
        return Result(client, False, "manual step required", why, snippet=snip, path=chosen)
    if kind == "missing":
        data = {}
    if not isinstance(data, dict):
        return Result(client, False, "manual step required",
                      "config root is not a JSON object",
                      snippet=manual_snippet(keys, slug, entry), path=chosen)

    if dry_run:
        return Result(client, True, "would write (dry-run)", multi_note, path=chosen)

    bak = backup(chosen)
    node = data
    for k in keys:
        nxt = node.get(k)
        if not isinstance(nxt, dict):
            nxt = {}
            node[k] = nxt
        node = nxt
    node[slug] = entry
    save_json(chosen, data)

    # verify-after-write + rollback (the old Register-McpNested skipped this)
    reread, rkind = load_config(chosen)
    ok = rkind == "json" and isinstance(reread, dict)
    n = reread if ok else None
    for k in keys:
        n = n.get(k) if isinstance(n, dict) else None
    if not (isinstance(n, dict) and slug in n):
        if bak:
            shutil.copyfile(bak, chosen)
        return Result(client, False, "write verification failed, rolled back", path=chosen)

    warn = multi_note
    if spec.get("live_state_file"):
        warn = ((warn + "; ") if warn else "") + \
            "edited Claude Code's live state file (CLI not found) — restart the " \
            "app; if the entry disappears, install the `claude` CLI and re-run"
    record_registration(install_dir, slug, python_exe, client, chosen, keys)
    return Result(client, True, "registered", warn, path=chosen)


def register_all(slug: str, python_exe: str, install_dir: str = "",
                 dry_run: bool = False) -> list[Result]:
    return [register(c, slug, python_exe, install_dir, dry_run) for c in CLIENTS]


def deregister(client: str, slug: str) -> Result:
    """T63: remove OUR slug entry from a client config (uninstall path).
    Non-destructive: JSON only (JSONC never rewritten), backup, verify.
    Codex TOML: our [mcp_servers.<slug>] section is emptied via regex."""
    spec = CLIENTS.get(client)
    if spec is None:
        return Result(client, False, "unknown client")
    chosen, _ = pick_existing(list(spec["candidates"]()))
    if chosen is None:
        return Result(client, True, "skipped", "config not found")
    if spec["format"] == "toml":
        old = read_text(chosen)
        pattern = re.compile(r"(?ms)^\[mcp_servers\." + re.escape(slug) + r"\]\s*?\r?\n.*?(?=^\[|\Z)")
        if not pattern.search(old):
            return Result(client, True, "skipped", "not registered")
        backup(chosen)
        with open(chosen, "w", encoding="utf-8") as fh:
            fh.write(pattern.sub("", old))
        return Result(client, True, "deregistered", path=chosen)
    data, kind = load_config(chosen)
    if kind in ("jsonc", "invalid"):
        return Result(client, False, "manual step required",
                      f"config is {kind}: remove the '{slug}' entry by hand", path=chosen)
    node = data
    for k in spec["keys"]:
        node = node.get(k) if isinstance(node, dict) else None
    if not isinstance(node, dict) or slug not in node:
        return Result(client, True, "skipped", "not registered")
    node.pop(slug, None)
    backup(chosen)
    save_json(chosen, data)
    return Result(client, True, "deregistered", path=chosen)


def deregister_all(slug: str) -> list[Result]:
    return [deregister(c, slug) for c in CLIENTS]


# ---------------------------------------------------------------------------
# Doctor (B6): scan, diagnose, repair
# ---------------------------------------------------------------------------

KNOWN_SLUGS = ("neuron", "neuron5")


def _entry_command(entry: Any) -> str:
    """Extract the executable from a server entry across client conventions."""
    if isinstance(entry, dict):
        cmd = entry.get("command")
        if isinstance(cmd, list):
            return cmd[0] if cmd else ""
        return cmd or ""
    return ""


def doctor(slug: str, python_exe: str, install_dir: str = "",
           fix: bool = False) -> tuple[list[str], int]:
    """Scan every known client config. Returns (report_lines, n_problems).

    Checks per entry under any known Neuron slug:
      a) config parseable (JSON or JSONC-for-read);
      b) the command executable exists on disk;
      c) the entry under OUR slug points at the CURRENT install's python;
      d) duplicate identities (both 'neuron' and 'neuron5') flagged;
      e) cruft: entries whose venv no longer exists (colleague's log case).
    With fix=True: (c) is repaired and (e) removed — plain-JSON files only,
    always with a backup. JSONC files are never rewritten (manual snippet)."""
    lines: list[str] = []
    problems = 0
    for cid, spec in CLIENTS.items():
        chosen, existing = pick_existing(list(spec["candidates"]()))
        if chosen is None:
            continue
        label = spec["label"]
        if len(existing) > 1:
            lines.append(f"  [i] {label}: multiple configs — checking {chosen}")
        if spec["format"] == "toml":
            text = read_text(chosen)
            for s in KNOWN_SLUGS:
                m = re.search(r"(?ms)^\[mcp_servers\." + re.escape(s) +
                              r"\]\s*?\n(.*?)(?=^\[|\Z)", text)
                if not m:
                    continue
                cm = re.search(r'command\s*=\s*"((?:[^"\\]|\\.)*)"', m.group(1))
                cmd = (cm.group(1).encode().decode("unicode_escape") if cm else "")
                lines.extend(_check_entry(label, chosen, s, cmd, slug,
                                          python_exe, None, None, fix))
            continue
        data, kind = load_config(chosen)
        if kind == "invalid":
            problems += 1
            lines.append(f"  [!!] {label}: {chosen} is not parseable — fix it by hand")
            continue
        node = data
        for k in spec["keys"]:
            node = node.get(k) if isinstance(node, dict) else None
        if not isinstance(node, dict):
            continue
        present = [s for s in KNOWN_SLUGS if s in node]
        if len(present) > 1:
            problems += 1
            lines.append(f"  [!!] {label}: BOTH {' and '.join(present)} registered — "
                         "the client runs two Neuron servers (duplicate tools). "
                         "Remove the one you don't use.")
        for s in present:
            cmd = _entry_command(node[s])
            ls = _check_entry(label, chosen, s, cmd, slug, python_exe,
                              (data if kind == "json" else None),
                              (node if kind == "json" else None), fix)
            lines.extend(ls)
    # count problems flagged inside _check_entry
    problems += sum(1 for ln in lines if ln.lstrip().startswith("[!!]"))
    # B6b: live-process section — coupled with the config scan, so a duplicate
    # key found above explains a double server found here (and vice versa).
    plines, pproblems = process_doctor(slug, python_exe, fix=fix)
    lines.extend(plines)
    problems += pproblems
    return lines, problems


def _check_entry(label: str, path: str, entry_slug: str, cmd: str,
                 our_slug: str, python_exe: str,
                 json_root: Any, node: Any, fix: bool) -> list[str]:
    out: list[str] = []
    if not cmd:
        out.append(f"  [!!] {label}: '{entry_slug}' has no command")
        return out
    if not os.path.exists(cmd):
        # (e) cruft: venv is gone
        if fix and node is not None:
            node.pop(entry_slug, None)
            backup(path)
            save_json(path, json_root)
            out.append(f"  [FIXED] {label}: removed stale '{entry_slug}' "
                       f"(command not on disk: {cmd})")
        else:
            out.append(f"  [!!] {label}: '{entry_slug}' points at a missing "
                       f"executable: {cmd}" + ("" if node is not None else
                       " (JSONC/TOML — remove by hand)"))
        return out
    if entry_slug == our_slug and os.path.normcase(cmd) != os.path.normcase(python_exe):
        if fix and node is not None:
            node[entry_slug]["command"] = python_exe
            backup(path)
            save_json(path, json_root)
            out.append(f"  [FIXED] {label}: '{entry_slug}' repointed to {python_exe}")
        else:
            out.append(f"  [!!] {label}: '{entry_slug}' points at a DIFFERENT install: "
                       f"{cmd} (current: {python_exe})")
        return out
    out.append(f"  [ok] {label}: '{entry_slug}' → {cmd}")
    return out


# ---------------------------------------------------------------------------
# Process doctor (B6b): who is running `python -m neuron`, and why?
# ---------------------------------------------------------------------------
# Every MCP client spawns its OWN stdio server (one per app — and one per
# session/project for Claude Code/Cowork), so several processes are normal.
# What is NOT normal, and what this section detects:
#   - orphans: the parent process is gone (client crashed/restarted) but the
#     server survived — safe to kill, and `--fix` does;
#   - stale installs: a server running from a DIFFERENT venv than the current
#     install (old version still in RAM) — the owning app needs a restart;
#   - duplicates: two servers spawned by the same parent (e.g. both 'neuron'
#     and 'neuron5' registered in that client's config).

_SERVER_RE = re.compile(r"-m\s+neuron\b")
_CLI_WORDS = ("doctor", "register", "init", "consolidate")


def _list_processes() -> list[dict]:
    """[{pid, ppid, name, cmd}] — PowerShell/CIM on Windows, ps on POSIX."""
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process | "
                 "Select-Object ProcessId,ParentProcessId,Name,CommandLine | "
                 "ConvertTo-Json -Compress"],
                capture_output=True, text=True, timeout=30).stdout
            data = json.loads(out or "[]")
            if isinstance(data, dict):
                data = [data]
            return [{"pid": d.get("ProcessId"), "ppid": d.get("ParentProcessId"),
                     "name": d.get("Name") or "", "cmd": d.get("CommandLine") or ""}
                    for d in data]
        out = subprocess.run(["ps", "-eo", "pid=,ppid=,comm=,args="],
                             capture_output=True, text=True, timeout=30).stdout
        procs = []
        for ln in out.splitlines():
            parts = ln.split(None, 3)
            if len(parts) >= 4:
                procs.append({"pid": int(parts[0]), "ppid": int(parts[1]),
                              "name": parts[2], "cmd": parts[3]})
        return procs
    except Exception as e:
        log.debug("process listing failed: %s", e)
        return []


def _default_killer(pid: int) -> bool:
    try:
        if os.name == "nt":
            r = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True, text=True, timeout=30)
            return r.returncode == 0
        os.kill(pid, 15)
        return True
    except Exception:
        return False


def process_doctor(slug: str, python_exe: str, fix: bool = False,
                   lister: Callable | None = None,
                   killer: Callable | None = None,
                   self_pid: int | None = None) -> tuple[list[str], int]:
    procs = (lister or _list_processes)()
    by_pid = {p["pid"]: p for p in procs}
    me = os.getpid() if self_pid is None else self_pid
    # my own ancestry (the doctor itself runs as `python -m neuron doctor`,
    # often under the installer's PowerShell) must never be flagged/killed
    mine = set()
    cur = me
    for _ in range(10):
        mine.add(cur)
        cur = (by_pid.get(cur) or {}).get("ppid")
        if cur is None or cur in mine:
            break

    servers = []
    for p in procs:
        cmd = p.get("cmd") or ""
        if p["pid"] in mine or not _SERVER_RE.search(cmd):
            continue
        tail = cmd.split("-m", 1)[-1]
        if any(w in tail.split() for w in _CLI_WORDS):
            continue   # a CLI invocation (register/doctor/...), not a server
        servers.append(p)

    lines: list[str] = []
    problems = 0
    if not servers:
        lines.append("  [ok] processes: no `python -m neuron` servers running")
        return lines, 0

    lines.append(f"  [i] processes: {len(servers)} Neuron server(s) running")
    parent_count: dict[int, int] = {}
    for p in servers:
        parent_count[p.get("ppid")] = parent_count.get(p.get("ppid"), 0) + 1
    for p in servers:
        pid, ppid, cmd = p["pid"], p.get("ppid"), p.get("cmd") or ""
        parent = by_pid.get(ppid)
        # "stale" only within OUR install identity: a server running from
        # Programs\<slug>\... but NOT from the current venv python. A server
        # from another slug (v4 'neuron' next to v5 'neuron5') is a supported
        # side-by-side install (T39), not a problem.
        # separator- and case-agnostic: Windows cmdlines carry backslashes and
        # mixed case; os.path.normcase is a no-op on POSIX (tests/CI), so use
        # explicit .lower() for a platform-independent comparison.
        ncmd = cmd.lower().replace("\\", "/")
        ours = f"programs/{slug.lower()}/" in ncmd
        stale = ours and (python_exe.lower().replace("\\", "/") not in ncmd)
        if parent is None:
            problems += 1
            if fix and (killer or _default_killer)(pid):
                lines.append(f"  [FIXED] pid {pid}: orphan (parent gone) — killed")
            else:
                lines.append(f"  [!!] pid {pid}: ORPHAN — parent process is gone "
                             f"(client crashed/restarted). Safe to kill"
                             + ("" if fix else " (doctor --fix does it)") + ".")
            continue
        owner = f"{parent.get('name') or 'pid ' + str(ppid)}"
        note = ""
        if stale:
            problems += 1
            note = " [!!] running from a DIFFERENT install than the current one — restart this app to load the new version"
        if parent_count.get(ppid, 0) > 1:
            note += (" [!!] this app spawned "
                     f"{parent_count[ppid]} Neuron servers — check its config for "
                     "duplicate keys (neuron AND neuron5)")
            problems += 0   # already counted via config scan; report only
        lines.append(f"  {'[!!]' if note else '[ok]'} pid {pid}: launched by {owner}{note}")
    return lines, problems


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def default_server_python(slug: "str | None" = None) -> str:
    """The python that SHOULD run the server: the installed venv's, when it
    exists — NOT sys.executable. `doctor` run from a system python was flagging
    every correct registration as 'DIFFERENT install' (and --fix would have
    repointed them to the system python, breaking everything). Falls back to
    sys.executable only when no install venv is found (pipx/pip installs,
    where the running interpreter IS the install)."""
    slug = slug or os.environ.get("NEURON_SLUG", "neuron5")
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        cand = os.path.join(base, "Programs", slug, ".venv", "Scripts", "python.exe")
    else:
        cand = os.path.join(os.path.expanduser("~"), ".local", "share", slug,
                            ".venv", "bin", "python")
    return cand if os.path.exists(cand) else sys.executable


def cli(argv: list[str]) -> int:
    # Self-safe UTF-8 guard (same pattern as the T17 scripts): doctor/register
    # output contains → and ⚠-style glyphs, and a default Windows console
    # (cp1252) makes print() crash with UnicodeEncodeError when this runs
    # under a python other than the UTF-8-configured venv one. Best-effort.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    import argparse
    ap = argparse.ArgumentParser(prog="neuron register|doctor")
    ap.add_argument("cmd", choices=["register", "doctor"])
    ap.add_argument("--client", default="all",
                    help="one of: " + ", ".join(sorted(CLIENTS)) + ", or 'all'")
    ap.add_argument("--slug", default=os.environ.get("NEURON_SLUG", "neuron5"))
    ap.add_argument("--python", dest="python_exe", default=None,
                    help="server python (default: the installed venv's, NOT this one)")
    ap.add_argument("--install-dir", default=os.environ.get("NEURON_INSTALL_DIR", ""))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fix", action="store_true", help="doctor: apply repairs")
    args = ap.parse_args(argv)
    if not args.python_exe:
        args.python_exe = default_server_python(args.slug)

    if args.cmd == "register":
        results = (register_all(args.slug, args.python_exe, args.install_dir, args.dry_run)
                   if args.client == "all"
                   else [register(args.client, args.slug, args.python_exe,
                                  args.install_dir, args.dry_run)])
        for r in results:
            print(r.line())
        return 0 if all(r.ok or r.action == "skipped" for r in results) else 1

    lines, problems = doctor(args.slug, args.python_exe, args.install_dir, args.fix)
    print("Neuron doctor" + (" (fix mode)" if args.fix else "") + ":")
    for ln in lines:
        print(ln)
    if not lines:
        print("  no client configs found")
    print(f"{problems} problem(s) found." if problems else "All good.")
    return 1 if problems else 0
