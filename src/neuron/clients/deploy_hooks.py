#!/usr/bin/env python3
"""Deploy the handshake assets for a STANDALONE install.

Gray Matter's installer deploys these through `gray_matter.executor`. A
standalone Neuron or NeuRAG has no Gray Matter, so without this the model got
no handshake at all on the one install shape where the MCP `instructions`
field is the only other channel -- and that field is host-optional.

Deliberately stdlib-only and idempotent: all three tools deploy the SAME files
to the SAME paths, so running it twice (or from two tools) is a no-op rather
than a double handshake. Ownership is resolved at runtime by the hook itself,
never here.

Usage:  python deploy_hooks.py [--dry-run]
Run from the directory that contains claude-code-hook/ (i.e. this file's dir).

KEEP IN SYNC: byte-identical copies live in neuron/src/neuron/clients/ and
neurag/clients/. `test_handshake.py` fails if they drift.
"""

import json
import os
import shutil
import sys
from pathlib import Path

HOOK = "claude-code-hook/neuron_sessionstart_hook.py"
OPENCODE = "opencode-plugin/neuron-handshake.mjs"
COWORK = "cowork-plugin/neuron-guard"

_MATCHER = "startup|resume|clear|compact"

# The second hook of the pair: the external reminder. The memory loop is
# self-referential (pre_turn says "then store_turn", store_turn says "then
# pre_turn"), so skipping one link also skips the reminder for the next one:
# UserPromptSubmit is the only trigger OUTSIDE the cycle. Shipped since 6.4.4
# and deployed by nobody. It imports neuron_sessionstart_hook, so it belongs in
# the SAME directory and never travels alone.
REMINDER = "claude-code-hook/neuron_reminder_hook.py"
# UserPromptSubmit takes no matcher in Claude Code; PreCompact does. The hook
# tells the two apart via `hook_event_name`, so both entries are needed.
_REMINDER_EVENTS = (("UserPromptSubmit", None), ("PreCompact", "manual|auto"))


def _upsert(hooks: dict, event: str, matcher, marker: str, cmd: str):
    """Upsert OUR entry for `event`. True when written, False when already fine,
    a string when `hooks` has a shape we refuse to write into.

    Same rule as always: match on the SCRIPT NAME (not on the whole command, or
    two deployers with different interpreters append two entries for the same
    hook) and rewrite an entry of ours that cannot run.
    """
    entries = hooks.setdefault(event, [])
    if not isinstance(entries, list):
        return f"SKIPPED: '{event}' is not a list"
    ours = [e for e in entries
            if isinstance(e, dict)
            and any(isinstance(h, dict) and marker in (h.get("command") or "")
                    for h in (e.get("hooks") or []))]
    dead = [e for e in ours
            if any(_dead_cmd(h.get("command") or "")
                   for h in (e.get("hooks") or []) if isinstance(h, dict))]
    if ours and not dead:
        return False
    for e in dead:
        entries.remove(e)
    fresh = {"hooks": [{"type": "command", "command": cmd, "timeout": 10}]}
    entries.append({"matcher": matcher, **fresh} if matcher else fresh)
    return True


def _load_json(p: Path):
    try:
        raw = p.read_text(encoding="utf-8-sig")
        return json.loads(raw) if raw.strip() else {}
    except (OSError, ValueError):
        return None            # missing OR unparseable: caller decides


def _save_json(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        shutil.copyfile(p, p.with_suffix(p.suffix + ".bak"))
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def _dead_cmd(cmd: str) -> bool:
    """La command line non puo' girare: interprete ASSOLUTO che non esiste."""
    cmd = (cmd or "").strip()
    exe = cmd[1:].split('"', 1)[0] if cmd.startswith('"') else cmd.split(" ", 1)[0]
    return bool(exe) and os.path.isabs(exe) and not os.path.exists(exe)


def _plugin_version(src: Path) -> str:
    """La versione la dichiara il plugin, non il deployer: hardcodarla significa
    che il primo bump di plugin.json deploya in una cartella che nessuno legge."""
    meta = _load_json(src / ".claude-plugin" / "plugin.json")
    v = (meta or {}).get("version") if isinstance(meta, dict) else None
    return str(v) if v else "0.1.0"


def _codex_enable(cfg: Path, plugin_name: str) -> bool:
    """`[plugins."<nome>@claude-cowork"] enabled = true` in ~/.codex/config.toml.

    Upsert sezionale: il resto del config dell'utente resta byte per byte, e il
    file non viene mai creato da zero se Codex non l'ha mai scritto — si scrive
    solo la nostra sezione. Torna False se non si e' potuto scrivere."""
    import re
    section = f'plugins."{plugin_name}@claude-cowork"'
    block = f"[{section}]\nenabled = true\n"
    try:
        text = cfg.read_text(encoding="utf-8-sig") if cfg.exists() else ""
    except OSError:
        return False
    pattern = re.compile(r"(?ms)^\[" + re.escape(section) + r"\]\s*?\n.*?(?=^\[|\Z)")
    if pattern.search(text):
        new_text = pattern.sub(lambda _m: block, text, count=1)   # lambda: i backslash Windows
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        new_text = text + ("\n" if text.strip() else "") + block
    if new_text == text:
        return True
    try:
        cfg.parent.mkdir(parents=True, exist_ok=True)
        if cfg.exists():
            shutil.copyfile(cfg, cfg.with_suffix(cfg.suffix + ".bak"))
        cfg.write_text(new_text, encoding="utf-8")
        return True
    except OSError:
        return False


def deploy_claude_code(root: Path, dry_run: bool) -> str:
    """Copy BOTH claude-code hooks and register them.

    SessionStart for the handshake, UserPromptSubmit + PreCompact for the
    reminder. The reminder imports the sessionstart hook, so the two travel
    together or not at all.
    """
    src = root / HOOK
    dst_dir = Path.home() / ".claude" / "hooks"
    dst = dst_dir / src.name
    settings = Path.home() / ".claude" / "settings.json"
    data = _load_json(settings)
    if data is None and settings.exists():
        # Never rewrite a config we cannot parse -- say so instead.
        return f"SKIPPED: {settings} is not parseable JSON, left untouched"
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return f"SKIPPED: {settings} root is not a JSON object"

    # Absolute interpreter (like Gray Matter's own deployer): a bare "python"
    # resolves via PATH and dies on machines where it is the Store stub or
    # another env without our deps.
    cmd = f'"{sys.executable}" "{dst}"'
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        return f"SKIPPED: {settings} 'hooks' is not an object"

    rem_src = root / REMINDER
    rem_dst = dst_dir / rem_src.name
    if dry_run:
        return f"[dry-run] would deploy {dst}" + (f" + {rem_dst.name}" if rem_src.exists() else "")
    dst_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    if rem_src.exists():
        shutil.copyfile(rem_src, rem_dst)

    written = []
    for event, matcher, marker, c in (
            [("SessionStart", _MATCHER, src.name, cmd)]
            + ([(e, m, rem_src.name, f'"{sys.executable}" "{rem_dst}"')
                for e, m in _REMINDER_EVENTS] if rem_src.exists() else [])):
        r = _upsert(hooks, event, matcher, marker, c)
        if isinstance(r, str):
            return f"SKIPPED: {settings} {r}"
        if r:
            written.append(event)
    if not written:
        return f"hooks refreshed ({dst_dir})"
    _save_json(settings, data)
    return f"hooks copied + {'/'.join(written)} registered ({dst_dir})"


def deploy_opencode(root: Path, dry_run: bool) -> str:
    """Copy the plugin next to opencode.json and list it in `plugin`."""
    cfg = Path.home() / ".config" / "opencode" / "opencode.json"
    if not cfg.exists():
        return "SKIPPED: OpenCode not configured on this machine"
    data = _load_json(cfg)
    if data is None:
        return f"SKIPPED: {cfg} is not parseable JSON, left untouched"
    dst = cfg.parent / "plugins" / (root / OPENCODE).name
    rel = f"./plugins/{dst.name}"
    plugins = data.setdefault("plugin", [])
    if not isinstance(plugins, list):
        return f"SKIPPED: {cfg} 'plugin' is not a list"
    if dry_run:
        return f"[dry-run] would deploy {dst}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(root / OPENCODE, dst)
    # Riconoscere l'entry dal NOME FILE, non dalla stringa esatta: GM la scrive
    # senza "./" (gray_matter/executor.py) e un match esatto non la vedeva, così
    # un'installazione GM seguita da una standalone appendeva un secondo
    # elemento per lo stesso plugin.
    if not any(dst.name in p for p in plugins if isinstance(p, str)):
        plugins.append(rel)
        _save_json(cfg, data)
        return f"plugin copied + registered ({dst})"
    return f"plugin refreshed ({dst})"


def deploy_cowork(root: Path, dry_run: bool) -> str:
    """Mirror the plugin directory into the Cowork/Codex plugin cache.

    A MIRROR, not a copy: files removed from the source are removed from the
    deployed copy too. A stale `neuron_handshake.py` survived here for months
    telling the model to call `mcp__neuron5__*` tools that no longer existed,
    precisely because deploy only ever added.
    """
    src = root / COWORK
    if not src.is_dir():
        return "SKIPPED: cowork plugin assets missing"
    codex_home = Path.home() / ".codex"
    # La presenza di ~/.codex e' la RILEVAZIONE (qui i deployer girano tutti,
    # senza la lista client di GM). La sottocartella cache invece si crea: su
    # una macchina con Codex ma senza cache ancora creata GM deployava e noi
    # saltavamo, stessa macchina ed esito diverso.
    if not codex_home.is_dir():
        return "SKIPPED: Cowork/Codex not present"
    base = codex_home / "plugins" / "cache" / "claude-cowork"
    dst = base / src.name / _plugin_version(src)
    if dry_run:
        return f"[dry-run] would mirror {dst}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    keep = set()
    for f in src.rglob("*"):
        if f.is_file():
            rel = f.relative_to(src)
            keep.add(rel)
            out = dst / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, out)
    removed = 0
    if dst.is_dir():
        for f in list(dst.rglob("*")):
            if f.is_file() and f.relative_to(dst) not in keep:
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass
    # Il mirror da solo non basta: Codex carica un plugin cowork solo se e'
    # elencato come abilitato in config.toml. Senza questo passo lo standalone
    # deployava un plugin che nessuno avrebbe mai caricato (GM lo fa in
    # gray_matter/executor.py::_deploy_codex).
    enabled = _codex_enable(codex_home / "config.toml", src.name)
    return (f"plugin mirrored ({dst})"
            + (f", {removed} stale file(s) removed" if removed else "")
            + ("" if enabled else ", WARNING: config.toml not writable, plugin not enabled"))


def main(argv) -> int:
    dry = "--dry-run" in argv
    root = Path(__file__).resolve().parent
    if not (root / HOOK).exists():
        print(f"deploy_hooks: assets not found under {root}", file=sys.stderr)
        return 1
    for name, fn in (("claude-code", deploy_claude_code),
                     ("opencode", deploy_opencode),
                     ("cowork", deploy_cowork)):
        try:
            print(f"  [{name}] {fn(root, dry)}")
        except Exception as exc:            # noqa: BLE001 — never fail an install
            print(f"  [{name}] SKIPPED: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
