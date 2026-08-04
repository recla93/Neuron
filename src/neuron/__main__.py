"""Entry point for ``python -m neuron`` and the ``neuron`` console script.

Default (no subcommand) runs the MCP stdio server, so existing launchers that call
``python -m neuron`` (e.g. bridge.py) keep working unchanged. Subcommands:
  ``neuron init ...``        — client wiring (no heavy server import).
  ``neuron register ...``    — register the MCP server in AI clients (Piano 05 B1).
  ``neuron doctor ...``      — diagnose/repair client registrations (Piano 05 B6).
  ``neuron consolidate ...`` — merge near-duplicates + archive orphans (E1.4).
  ``neuron setup / manage``  — lifecycle + day-to-day management (ADR-007).
  ``neuron bridge ...``      — expose the stdio server over HTTP (remote connectors).
  ``neuron connect ...``     — connect & test a Turso Cloud DB, then save to .env.
  ``neuron console ...``     — read-only graph diagnostics (--watch to follow).
  ``neuron tunnel ...``      — public HTTPS via cloudflared (pairs with bridge).
  ``neuron gui``             — control center web condiviso (gray_matter.webgui);
                               si bootstrappa Gray Matter da solo se manca.
"""

import sys


def _record_paths_cli(argv) -> int:
    """Neuron registra la propria cartella sorgente (repo) nel suo registro.
    La chiama l'installer; GM la scopre poi via `neuron.paths.source_dir()`."""
    import argparse
    from neuron import paths as _paths
    ap = argparse.ArgumentParser(prog="neuron record-paths")
    ap.add_argument("--source", default="", help="Neuron's source folder (the repo)")
    args = ap.parse_args(argv)
    d = _paths.record_self(args.source or None)
    print(f"Neuron paths recorded in {_paths._self_registry()}")
    print(f"  source: {d.get('source', _paths.source_dir())}")
    return 0


def _repair_cli(argv) -> int:
    """Reinstall pulito SOLO di Neuron: opzionale wipe della memoria (grafi),
    poi promemoria del reinstall forzato. Scope Neuron — non tocca NeuRAG/GM."""
    import argparse, os, shutil
    from neuron import config as _cfg
    ap = argparse.ArgumentParser(prog="neuron repair",
                                 description="Clean reinstall of Neuron (scope: Neuron only).")
    ap.add_argument("--wipe-memory", action="store_true",
                    help="delete Neuron's memory (graphs). Default: keep it.")
    ap.add_argument("--reinstall", action="store_true",
                    help="run Neuron's OWN installer right away with --force (from the recorded paths)")
    ap.add_argument("--dry-run", action="store_true", help="show what would happen, change nothing")
    ap.add_argument("--json", action="store_true",
                    help="list the removable surfaces as JSON (used by the control center)")
    args = ap.parse_args(argv)
    gd = _cfg.graphs_dir()
    if args.json:
        import json, os as _os
        inst, _ = _own_installer()
        print(json.dumps({
            "scope": "neuron",
            "targets": [{"key": "--wipe-memory", "label": "Memoria Neuron (grafi)",
                         "path": str(gd), "exists": _os.path.isdir(gd)}],
            "reinstall": "neuron (installer -Force)",
            "installer": inst is not None}))
        return 0
    print("Neuron repair - scope: Neuron ONLY.")
    if args.wipe_memory:
        if args.dry_run:
            print(f"[dry-run] would delete the memory: {gd}")
        else:
            try:
                if os.path.isdir(gd):
                    shutil.rmtree(gd, ignore_errors=True)
                print(f"[ok] Neuron memory deleted: {gd}")
            except OSError as exc:
                print(f"[!] could not delete {gd}: {exc}")
    else:
        print(f"  memory KEPT: {gd}   (use --wipe-memory to delete it)")
    # Auto-repair standalone (2026-07-22): Neuron conosce i PROPRI path — il
    # comando stampato (o lanciato con --reinstall) punta all'installer VERO.
    inst, argv_inst = _own_installer()
    if inst is None:
        print("Force-reinstall the code (bypasses the version check):")
        print("  Windows:   install.ps1 -Force        mac/Linux: ./install.sh --force")
        print("  (source not recorded: run `neuron record-paths --source <repo>`)")
        return 0
    if args.reinstall and not args.dry_run:
        import subprocess
        print(f"Force-reinstalling: {inst}")
        return subprocess.call(argv_inst)
    print("Force-reinstall the code (bypasses the version check):")
    print("  " + " ".join(f'"{a}"' if " " in a else a for a in argv_inst))
    print("  (or: neuron repair --reinstall)")
    return 0


def _own_installer():
    """(path, argv) dell'installer di Neuron in modalità force, dai PROPRI path
    (paths.source_dir()); (None, None) se non trovato."""
    import os
    from neuron import paths as _paths
    src = _paths.source_dir()
    ps1, sh = src / "install.ps1", src / "install.sh"
    if os.name == "nt" and ps1.exists():
        return ps1, ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(ps1), "-Force"]
    if os.name != "nt" and sh.exists():
        return sh, ["sh", str(sh), "--force"]
    return None, None


def _go_standalone_cli(argv) -> int:
    """Neuron esce dal gateway GM: (a) si registra come MCP diretto nei client
    col PROPRIO engine (clients.register_all), (b) chiede a GM — se presente —
    di smettere di gestirlo (persistente + IPC best-effort). NON tocca l'entry
    `gray-matter` finché un peer resta gestito da GM: quel giudizio è di GM
    (gray_matter.clients.release_tool). Reversibile: gray-matter register --gateway.
    keep-in-sync con neurag/cli.py `_cmd_go_standalone`."""
    import argparse, os
    from neuron import clients as _clients
    ap = argparse.ArgumentParser(prog="neuron go-standalone")
    ap.add_argument("--dry-run", action="store_true", help="show what would happen, write nothing")
    args = ap.parse_args(argv)
    slug = os.environ.get("NEURON_SLUG", "neuron")
    py = _clients.default_server_python(slug)
    print("Neuron go-standalone" + (" (dry-run)" if args.dry_run else "") + ":")
    for r in _clients.register_all(slug, py, dry_run=args.dry_run):
        print(r.line())
    if args.dry_run:
        print("  [dry-run] not asking GM to release Neuron.")
        return 0
    try:
        from gray_matter import clients as _gm_clients
        for line in _gm_clients.release_tool("neuron"):
            print("  " + line)
    except ImportError:
        print("  Gray Matter not installed: Neuron was already standalone.")
    print("Done. Restart your AI apps. To go back to the gateway: gray-matter register --gateway")
    return 0


def _bootstrap_gray_matter() -> bool:
    """Installa gray-matter nello STESSO venv (extra ``[gui]``), streamando il
    progresso, e ritorna True se dopo diventa importabile. Prova in ordine:
    (1) la cartella sorella ``gray_matter`` del layout di sviluppo, (2) l'indice
    pip. L'output eredita lo stdout → visibile nel terminale da cui parte
    ``neuron gui`` (mai install muto)."""
    import subprocess, importlib, importlib.util
    from pathlib import Path
    from neuron import paths as _paths
    py = sys.executable or "python"
    candidates = []
    try:
        sib = _paths.source_dir().parent / "gray_matter"
        if (sib / "pyproject.toml").exists():
            argv = [py, "-m", "pip", "install", str(sib)]
            if (sib / "vendor").is_dir():
                argv += ["--find-links", str(sib / "vendor")]
            candidates.append(("cartella sorella", argv))
    except Exception:  # noqa: BLE001 — path non registrato
        pass
    candidates.append(("indice pip", [py, "-m", "pip", "install", "gray-matter>=1.0"]))
    import shutil
    if shutil.which("git"):
        candidates.append(("GitHub", [py, "-m", "pip", "install",
                                      "git+https://github.com/recla93/gray-matter"]))
    # Wheel d'emergenza vendorata NEL package (viaggia nel wheel di Neuron): GM ha
    # solo `mcp` come dep, già presente qui → install completamente OFFLINE.
    #
    # ULTIMA, non seconda. È un artefatto CONGELATO al momento della release di
    # Neuron — il pyproject stesso ammette "va ricostruito a ogni release di GM" —
    # e provandola prima di PyPI e GitHub una macchina con rete perfettamente
    # funzionante si ritrovava installata una Gray Matter vecchia. Da ultima
    # continua a fare il suo mestiere (l'unico caso in cui serve è quando la rete
    # NON c'è) senza poter più scavalcare una versione aggiornata.
    vendor = Path(__file__).resolve().parent / "_gm_vendor"
    if vendor.is_dir() and any(vendor.glob("gray_matter-*.whl")):
        candidates.append(("wheel vendorata (offline, ultima risorsa)",
                           [py, "-m", "pip", "install", "--no-index",
                            "--find-links", str(vendor), "gray-matter"]))
    for label, argv in candidates:
        print(f"[gui] Gray Matter is not installed: installing it ({label})...")
        try:
            subprocess.call(argv)
        except Exception as exc:  # noqa: BLE001
            print(f"[gui] install fallita ({label}): {exc}")
            continue
        importlib.invalidate_caches()
        if importlib.util.find_spec("gray_matter") is not None:
            print("[gui] Gray Matter installato.")
            return True
    return False


def _neuron_shortcut() -> None:
    """Crea/aggiorna l'icona desktop 'Neuron' (best-effort, idempotente). Usa la
    copia tool-local `neuron.shortcut`: funziona anche SENZA Gray Matter (lo usa
    l'installer standalone via `neuron gui --shortcut-only`)."""
    try:
        from neuron.shortcut import ensure_desktop_shortcut
        ensure_desktop_shortcut("neuron", "Neuron", ["-m", "neuron", "gui"],
                                "Neuron — control center")
    except Exception:  # noqa: BLE001 — un'icona non deve mai bloccare nulla
        pass


def _gui_cli(argv) -> int:
    """GUI universale (2026-07-22): il control center è UNO (gray_matter.webgui)
    e ogni tool lo apre. Se Gray Matter manca, lo bootstrappa nello stesso venv e
    rilancia — niente più GUI Tkinter separata. `--shortcut-only`: crea solo
    l'icona desktop e esce (usato dall'installer, non apre la GUI, non serve GM)."""
    if "--shortcut-only" in argv:
        _neuron_shortcut()
        return 0
    try:
        from gray_matter.webgui import main as gui_main
    except ImportError:
        if not _bootstrap_gray_matter():
            print("Install Gray Matter manually (install.ps1/install.sh), then run `neuron gui` again.")
            return 1
        try:
            from gray_matter.webgui import main as gui_main
        except ImportError as exc:
            print(f"[gui] Gray Matter is installed but cannot be imported: {exc}")
            return 1
    # GM ora è presente: lascia un'icona desktop "Neuron" → doppio click d'ora in
    # poi (punta a `neuron gui`, che riapre il control center condiviso).
    _neuron_shortcut()
    return int(gui_main() or 0)


def _consolidate_cli(argv) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="neuron consolidate",
                                 description="Consolidate the store: merge near-duplicates + archive orphans.")
    ap.add_argument("--context", help="this context only (default: all)")
    ap.add_argument("--no-merge", action="store_true", help="do not merge near-duplicates")
    ap.add_argument("--no-drop-orphans", action="store_true", help="do not archive orphans")
    ap.add_argument("--sim-threshold", type=float, default=0.85, help="cosine threshold for merging")
    args = ap.parse_args(argv)

    from neuron.server import _g  # registry con l'embedder già registrato
    contexts = [args.context] if args.context else [c["context"] for c in _g.list_contexts()]
    if not contexts:
        print("No context to consolidate.")
        return 0

    tot_m = tot_d = 0
    for ctx in contexts:
        g = _g.get(ctx)
        rep = g.consolidate(
            sim_threshold=(2.0 if args.no_merge else args.sim_threshold),
            drop_orphans=not args.no_drop_orphans,
        )
        _g.save(ctx)
        merged = sum(1 for r in rep if "kept" in r)
        dropped = sum(1 for r in rep if "dropped" in r)
        tot_m += merged; tot_d += dropped
        print(f"  {ctx}: merged={merged} dropped={dropped} nodes={len(g.nodes)} links={len(g.links)}")
    print(f"Totale: merged={tot_m} dropped={tot_d}")
    return 0


# --------------------------------------------------------------------------
# Catalogo comandi — SSOT
# --------------------------------------------------------------------------
# Questa tabella È l'elenco dei subcomandi: la usa il dispatch qui sotto E la
# legge Gray Matter per costruire la GUI (gray_matter/catalog.py). Aggiungere
# una riga qui basta: compare nella CLI e nel control center, senza toccare
# nient'altro. Prima erano dieci `if sys.argv[1] == ...` quasi identici, con
# l'elenco vero sparso fra il docstring e la catena di if.
#
#   nome: (modulo, funzione, gruppo, descrizione, passa_argv0)
# gruppo = come si ordina nella GUI, dal più grande al più piccolo:
#   lifecycle (accendi/spegni/installa) · maintenance (manutieni) ·
#   inspect (guarda, sola lettura) · tuning (configura)
#
# Le descrizioni sono in INGLESE perché finiscono sotto gli occhi dell'utente
# (`neuron --help` e le etichette del control center): l'output resta inglese,
# l'italiano vive nei doc `.it.md`. I commenti restano in italiano.
COMMANDS: "dict[str, tuple]" = {
    "setup":       ("neuron.setup",   "main", "lifecycle",  "Lifecycle: install, update, repair", False),
    "register":    ("neuron.clients", "cli",  "lifecycle",  "Register the MCP server in your AI clients", True),
    "gui":         (None,             None,   "lifecycle",  "Shared web control center (Gray Matter): installs it on first run, then opens", False),
    "start":       (None,             None,   "lifecycle",  "Start the Neuron server in the background (HTTP bridge)", False),
    "stop":        (None,             None,   "lifecycle",  "Stop the Neuron server", False),
    "bridge":      ("neuron.bridge",  "main", "lifecycle",  "Expose the stdio server over HTTP (remote connectors)", False),
    "tunnel":      ("neuron.tunnel",  "main", "lifecycle",  "Public HTTPS via cloudflared (bridge included)", False),
    "manage":      ("neuron.manage",  "main", "maintenance", "Day-to-day graph management", False),
    "consolidate": (None,             None,   "maintenance", "Merge near-duplicates and archive orphans", False),
    "repair":      (None,             None,   "lifecycle",  "Clean reinstall of Neuron ONLY: choose whether to wipe the memory, then force-reinstall", False),
    "record-paths":(None,             None,   "lifecycle",  "Record Neuron's source folder (used by the installer)", False),
    "go-standalone":(None,            None,   "lifecycle",  "Leave the GM gateway: Neuron registers directly in your clients (undo with gray-matter register --gateway)", False),
    "migrate":     (None,             None,   "maintenance", "Migrate graphs from the old slug (neuron5) to the new one (neuron)", False),
    "doctor":      ("neuron.clients", "cli",  "inspect",    "Diagnose and repair the client registrations", True),
    "console":     ("neuron.console", "main", "inspect",    "Read-only graph diagnostics (--watch)", False),
    "init":        ("neuron.init",    "main", "tuning",     "Wire up the clients (without importing the server)", False),
    "connect":     ("neuron.connect", "main", "tuning",     "Connect and test a Turso Cloud DB, then save it", False),
}


def _start_cli(argv) -> int:
    """Avvia il server Neuron come processo background (bridge HTTP).

    DEPENDENCIES:
    - neuron.bridge.resolve_proxy_runner: mcp-proxy (uv, uvx, o pipx)
    - neuron.paths.data_dir(): cartella dati per PID file
    - subprocess.Popen con stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL

    SAFETY CHECKS:
    1. PID file esistente + processo vivo → return 0 (no-op)
    2. PID file corrotto (ValueError/OSError) → viene ignorato, sovrascritto
    3. mcp-proxy non trovato → return 1, messaggio stderr
    4. bridge import fallisce → return 1, messaggio stderr
    5. FileNotFoundError (exe non trovato) → return 1, messaggio stderr
    6. Processo fallisce subito (poll != None dopo 1s) → PID file rimosso, return 1
    7. Permessi insufficienti → PermissionError gestito, return 1

    FALLBACK:
    - Se PID file esistente ma processo morto → sovrascrive e avvia nuovo processo
    - Se PID file corrotto → viene ignorato, nuovo processo avviato
    - Se mcp-proxy mancante → return 1 con messaggio chiaro
    """
    import argparse, json, os, subprocess, sys, time
    from pathlib import Path
    from neuron import paths as _paths

    ap = argparse.ArgumentParser(prog="neuron start",
                                 description="Start the Neuron server in the background (HTTP bridge).")
    ap.add_argument("--port", type=int, default=8000, help="porta HTTP (default 8000)")
    ap.add_argument("--host", default="127.0.0.1", help="host (default 127.0.0.1)")
    args = ap.parse_args(argv)

    pid_file = _paths.data_dir() / "neuron_server.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            if _is_alive(pid):
                print(f"Neuron server already running (PID {pid}).")
                return 0
        except (ValueError, OSError):
            pass  # PID file corrotto: ignora, sovrascriverà

    neuron_cmd = [sys.executable, "-m", "neuron"]
    try:
        from neuron.bridge import resolve_neuron_cmd, resolve_proxy_runner
        proxy = resolve_proxy_runner()
        if proxy is None:
            print("mcp-proxy not found. Install uv or pipx.", file=sys.stderr)
            return 1
        full = proxy + [f"--port={args.port}", f"--host={args.host}", "--"] + neuron_cmd
    except ImportError:
        print("Bridge not available. Update Neuron.", file=sys.stderr)
        return 1

    flags = 0
    if os.name == "nt":
        # NOT DETACHED_PROCESS: Windows ignores CREATE_NO_WINDOW when combined
        # with DETACHED_PROCESS (or CREATE_NEW_CONSOLE), and the detached child
        # allocates its own console -> the empty CMD window this was meant to
        # avoid. CREATE_NEW_PROCESS_GROUP just keeps it out of this console's
        # Ctrl-C, same fix already proven in gray_matter/server.py's own spawn.
        flags = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    # NON DEVNULL: l'output del figlio è l'unico resoconto del perché è morto, e
    # buttarlo via rendeva la diagnosi impossibile dall'esterno. Il bridge muore
    # se `uvx mcp-proxy` non è disponibile, e l'utente vedeva solo "avviato"
    # seguito da "not running". keep-in-sync con neurag/cli.py `_cmd_start`.
    log = pid_file.parent / "neuron_server.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(log, "w", encoding="utf-8") as fh:
            proc = subprocess.Popen(
                full,
                stdin=subprocess.DEVNULL,
                stdout=fh,
                stderr=subprocess.STDOUT,
                creationflags=flags,
            )
    except FileNotFoundError as exc:
        print(f"Could not start: {exc}", file=sys.stderr)
        return 1

    pid_file.write_text(str(proc.pid), encoding="utf-8")
    # Un secondo fisso dormiva attraverso il fallimento che doveva cogliere.
    for _ in range(50):
        time.sleep(0.1)
        if proc.poll() is not None:
            break
    if proc.poll() is not None:
        print(f"Neuron server è fallito subito (exit {proc.returncode}).")
        tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-12:]
        if tail:
            print("--- " + str(log) + " ---", file=sys.stderr)
            print("\n".join(tail), file=sys.stderr)
        pid_file.unlink(missing_ok=True)
        return 1
    print(f"Neuron server avviato (PID {proc.pid}) su http://{args.host}:{args.port}/mcp")
    return 0


def _stop_cli(argv) -> int:
    """Ferma il server Neuron.

    DEPENDENCIES:
    - neuron.paths.data_dir(): cartella dati per PID file
    - os.kill(pid, 0): verifica processo vivo
    - os.kill(pid, SIGTERM/SIGKILL): terminazione

    SAFETY CHECKS:
    1. PID file non esistente → return 0 (nessuna azione)
    2. PID file corrotto (ValueError/OSError) → rimosso, return 1
    3. Processo non vivo (PID non trovato) → PID file rimosso, return 0
    4. PermissionError → PID file rimosso, return 1
    5. ProcessLookupError durante SIGTERM → già terminato, ignora
    6. SIGTERM non basta (dopo 2s) → SIGKILL come fallback

    FALLBACK:
    - Se SIGTERM fallisce (processo non risponde) → SIGKILL dopo 2s
    - Se PID file corrotto → viene rimosso
    - Se processo già morto → PID file rimosso, return 0
    """
    import argparse, os, signal
    from pathlib import Path
    from neuron import paths as _paths

    ap = argparse.ArgumentParser(prog="neuron stop",
                                 description="Stop the Neuron server.")
    args = ap.parse_args(argv)

    pid_file = _paths.data_dir() / "neuron_server.pid"
    if not pid_file.exists():
        print("Neuron server not running (no PID file).")
        return 0
    try:
        pid = int(pid_file.read_text().strip())
    except (ValueError, OSError):
        print("File PID corrotto.")
        pid_file.unlink(missing_ok=True)
        return 1
    if not _is_alive(pid):
        print(f"Neuron server not running (PID {pid} not found).")
        pid_file.unlink(missing_ok=True)
        return 0
    try:
        if os.name == "nt":
            os.kill(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        print(f"Process {pid} already exited.")
    except PermissionError:
        print(f"Permission denied for PID {pid}.")
        pid_file.unlink(missing_ok=True)
        return 1
    import time
    for _ in range(10):
        time.sleep(0.2)
        if not _is_alive(pid):
            break
    if _is_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    pid_file.unlink(missing_ok=True)
    print("Neuron server fermato.")
    return 0


def _is_alive(pid: int) -> bool:
    """True se il processo PID è vivo.

    DEPENDENCIES:
    - os.kill(pid, 0): signal 0 verifica esistenza senza inviare segnali

    SAFETY CHECKS:
    1. ProcessLookupError → processo non esiste, return False
    2. PermissionError → processo esiste ma non abbiamo permessi, return False
    3. OSError (WinError 87) → PID non valido su Windows, return False
    """
    import os, signal
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _migrate_cli(argv) -> int:
    """Migra i grafi dalla vecchia slug (neuron5) alla nuova (neuron).

    DEPENDENCIES:
    - neuron.paths.migrate_graphs(): funzione di migrazione
    - shutil.move: spostamento atomico quando possibile

    SAFETY CHECKS:
    1. NEURON_SLUG=neuron5 → skip (utente usa slug vecchio volutamente)
    2. Old path non esistente → skip (niente da migrare)
    3. New path già con dati → skip (non sovrascrivere)
    4. Idempotente: eseguire più volte è sicuro
    """
    import argparse
    from neuron import paths as _paths

    ap = argparse.ArgumentParser(prog="neuron migrate",
                                 description="Migrate graphs from the old slug (neuron5) to the new one (neuron).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would happen without moving anything")
    args = ap.parse_args(argv)

    result = _paths.migrate_graphs(dry_run=args.dry_run)

    if result["error"]:
        print(f"Error: {result['error']}")
        return 1

    moved, clash = result["migrated"], result["collisions"]
    if not moved and not clash:
        print("Nothing to migrate.")
        return 0

    verbo = "Sposterei" if args.dry_run else "Spostati"
    if moved:
        print(f"{verbo} {len(moved)} file: {result['old_path']} → {result['new_path']}")
        for name in moved:
            print(f"  {name}")

    # Not an error: two graphs for the same context are two memories, and only
    # the user can say which one wins. Say exactly where both are.
    if clash:
        print(f"\nLeft in {result['old_path']} (a file with the same name already exists there):")
        for name in clash:
            print(f"  {name}")
        print("Nothing was overwritten. Rename the file to import it as a separate context.")

    return 0


def _usage() -> str:
    """Aiuto costruito dalla tabella COMMANDS: una riga lì = una riga qui."""
    groups: "dict[str, list[tuple[str, str]]]" = {}
    for name, (_m, _f, group, help_, _a) in COMMANDS.items():
        groups.setdefault(group, []).append((name, help_))
    out = ["neuron - persistent semantic memory (MCP)",
           "",
           "usage: neuron <command> [options]",
           "       neuron                    run the MCP server on stdio (your AI client does this)",
           ""]
    for group in ("lifecycle", "maintenance", "inspect", "tuning"):
        if group not in groups:
            continue
        out.append(f"{group}:")
        width = max(len(n) for n, _ in groups[group])
        for name, help_ in groups[group]:
            out.append(f"  {name.ljust(width)}  {help_}")
        out.append("")
    out.append("isolation flags (server mode only): --graphs-dir PATH | --local | --slug NAME")
    return "\n".join(out)


def _console_safe() -> None:
    """Il grafo contiene testo utente arbitrario: su una console Windows cp1252
    una freccia o un'emoji uccideva il comando con UnicodeEncodeError.

    Va chiamata SOLO sui rami CLI: quando `neuron` gira come server MCP, stdout
    non è una console ma il canale del protocollo, e non si tocca.
    """
    for stream in (sys.stdout, sys.stderr):
        # Sotto pytest, in una GUI o dietro una pipe, stdout è spesso un
        # wrapper senza `.reconfigure` (o con l'attributo a None): si salta.
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(errors="replace")
        except (OSError, ValueError):
            pass


def cli() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd in ("-h", "--help", "help"):
        _console_safe()
        print(_usage())
        raise SystemExit(0)
    # `--version` starts with '-', so the unknown-command guard below waves it
    # through, no COMMANDS entry matches, and it fell into the "no command =>
    # run the MCP server" branch: `neuron --version` STARTED THE STDIO SERVER
    # and blocked on stdin forever. The installer's last line asks for the
    # version, so a finished install hung there — everything done, nothing said.
    if cmd in ("-V", "--version", "version"):
        _console_safe()
        from neuron import __version__
        print(__version__)
        raise SystemExit(0)
    # Senza questa guardia QUALSIASI parola sconosciuta cadeva nel ramo "avvia
    # il server MCP": `neuron --help` e ogni refuso partivano come server stdio
    # e restavano appesi in attesa su stdin, senza dire niente. I flag di
    # isolamento (--graphs-dir/--local/--slug) restano validi: sono per il
    # server, che è appunto il caso "nessun comando".
    if cmd and not cmd.startswith("-") and cmd not in COMMANDS:
        _console_safe()
        print(f"neuron: unknown command '{cmd}'\n", file=sys.stderr)
        print(_usage(), file=sys.stderr)
        raise SystemExit(2)
    spec = COMMANDS.get(cmd)
    if spec is not None:
        _console_safe()
        module, func, _group, _help, pass_argv0 = spec
        if module is None:                       # implementato qui nel modulo
            if cmd == "repair":
                raise SystemExit(_repair_cli(sys.argv[2:]))
            if cmd == "record-paths":
                raise SystemExit(_record_paths_cli(sys.argv[2:]))
            if cmd == "go-standalone":
                raise SystemExit(_go_standalone_cli(sys.argv[2:]))
            if cmd == "gui":
                raise SystemExit(_gui_cli(sys.argv[2:]))
            if cmd == "start":
                raise SystemExit(_start_cli(sys.argv[2:]))
            if cmd == "stop":
                raise SystemExit(_stop_cli(sys.argv[2:]))
            if cmd == "migrate":
                raise SystemExit(_migrate_cli(sys.argv[2:]))
            raise SystemExit(_consolidate_cli(sys.argv[2:]))
        import importlib
        entry = getattr(importlib.import_module(module), func)
        # `clients.cli` vuole anche il nome del comando (dispatcha register/doctor);
        # gli altri ricevono solo gli argomenti che seguono.
        raise SystemExit(entry(sys.argv[1:] if pass_argv0 else sys.argv[2:]))
    # T68: client-agnostic isolation flags. Some MCP hosts (OpenCode) don't
    # pass `env` to child processes at all, so a test/sandbox store couldn't be
    # isolated via NS_GRAPHS_DIR. Flags travel in the command array — which
    # EVERY client passes — and are applied BEFORE neuron.server is imported
    # (server reads NS_GRAPHS_DIR at import; db reads TURSO_* at its import).
    #   --graphs-dir PATH   store location (sets NS_GRAPHS_DIR)
    #   --local             force the local tier: drops TURSO_* creds
    #                       (wherever they came from, .env included)
    #   --slug NAME         identity override (sets NEURON_SLUG)
    import os
    args = sys.argv[1:]
    def _take(flag):
        if flag in args:
            i = args.index(flag)
            if i + 1 < len(args):
                v = args[i + 1]; del args[i:i + 2]; return v
            del args[i]
        return None
    _gd, _slug = _take("--graphs-dir"), _take("--slug")
    if "--local" in args:
        args.remove("--local")
        os.environ["NEURON_NO_DOTENV"] = "1"
        os.environ.pop("TURSO_DATABASE_URL", None)
        os.environ.pop("TURSO_AUTH_TOKEN", None)
    if _gd:
        os.environ["NS_GRAPHS_DIR"] = _gd
    if _slug:
        os.environ["NEURON_SLUG"] = _slug
    import asyncio
    from neuron.server import main
    asyncio.run(main())


if __name__ == "__main__":
    cli()
