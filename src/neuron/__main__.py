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
    ap.add_argument("--source", default="", help="Cartella sorgente di Neuron (repo)")
    args = ap.parse_args(argv)
    d = _paths.record_self(args.source or None)
    print(f"Neuron paths registrati in {_paths._self_registry()}")
    print(f"  source: {d.get('source', _paths.source_dir())}")
    return 0


def _repair_cli(argv) -> int:
    """Reinstall pulito SOLO di Neuron: opzionale wipe della memoria (grafi),
    poi promemoria del reinstall forzato. Scope Neuron — non tocca NeuRAG/GM."""
    import argparse, os, shutil
    from neuron import config as _cfg
    ap = argparse.ArgumentParser(prog="neuron repair",
                                 description="Reinstall pulito di Neuron (scope: solo Neuron).")
    ap.add_argument("--wipe-memory", action="store_true",
                    help="cancella la memoria di Neuron (grafi). Default: la tiene.")
    ap.add_argument("--reinstall", action="store_true",
                    help="lancia subito il PROPRIO installer con --force (dai path registrati)")
    ap.add_argument("--dry-run", action="store_true", help="mostra, non tocca nulla")
    ap.add_argument("--json", action="store_true",
                    help="elenca le superfici cancellabili in JSON (usato dal control center)")
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
    print("Neuron repair — scope: SOLO Neuron.")
    if args.wipe_memory:
        if args.dry_run:
            print(f"[dry-run] cancellerei la memoria: {gd}")
        else:
            try:
                if os.path.isdir(gd):
                    shutil.rmtree(gd, ignore_errors=True)
                print(f"[ok] memoria Neuron cancellata: {gd}")
            except OSError as exc:
                print(f"[!] impossibile cancellare {gd}: {exc}")
    else:
        print(f"  memoria TENUTA: {gd}   (usa --wipe-memory per cancellarla)")
    # Auto-repair standalone (2026-07-22): Neuron conosce i PROPRI path — il
    # comando stampato (o lanciato con --reinstall) punta all'installer VERO.
    inst, argv_inst = _own_installer()
    if inst is None:
        print("Reinstall forzato del codice (bypassa il check versione):")
        print("  Windows:   install.ps1 -Force        mac/Linux: ./install.sh --force")
        print("  (sorgente non registrato: lancia `neuron record-paths --source <repo>`)")
        return 0
    if args.reinstall and not args.dry_run:
        import subprocess
        print(f"Reinstall forzato: {inst}")
        return subprocess.call(argv_inst)
    print("Reinstall forzato del codice (bypassa il check versione):")
    print("  " + " ".join(f'"{a}"' if " " in a else a for a in argv_inst))
    print("  (oppure: neuron repair --reinstall)")
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
    ap.add_argument("--dry-run", action="store_true", help="mostra, non scrive nulla")
    args = ap.parse_args(argv)
    slug = os.environ.get("NEURON_SLUG", "neuron")
    py = _clients.default_server_python(slug)
    print("Neuron go-standalone" + (" (dry-run)" if args.dry_run else "") + ":")
    for r in _clients.register_all(slug, py, dry_run=args.dry_run):
        print(r.line())
    if args.dry_run:
        print("  [dry-run] non chiedo a GM di rilasciare Neuron.")
        return 0
    try:
        from gray_matter import clients as _gm_clients
        for line in _gm_clients.release_tool("neuron"):
            print("  " + line)
    except ImportError:
        print("  Gray Matter non installato: Neuron era già standalone.")
    print("Fatto. Riavvia le app AI. Per tornare al gateway: gray-matter register --gateway")
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
    # Wheel d'emergenza vendorato NEL package (viaggia nel wheel di Neuron): GM
    # ha solo `mcp` come dep, già presente qui → install completamente OFFLINE,
    # nessuna dipendenza da rete/PyPI/GitHub.
    vendor = Path(__file__).resolve().parent / "_gm_vendor"
    if vendor.is_dir() and any(vendor.glob("gray_matter-*.whl")):
        candidates.append(("wheel vendorato (offline)",
                           [py, "-m", "pip", "install", "--find-links", str(vendor),
                            "gray-matter"]))
    candidates.append(("indice pip", [py, "-m", "pip", "install", "gray-matter>=1.0"]))
    import shutil
    if shutil.which("git"):
        candidates.append(("GitHub", [py, "-m", "pip", "install",
                                      "git+https://github.com/recla93/gray-matter"]))
    for label, argv in candidates:
        print(f"[gui] Gray Matter non è installato: lo installo ({label})…")
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
            print("Installa Gray Matter a mano (install.ps1/install.sh), poi rilancia `neuron gui`.")
            return 1
        try:
            from gray_matter.webgui import main as gui_main
        except ImportError as exc:
            print(f"[gui] Gray Matter installato ma non importabile: {exc}")
            return 1
    # GM ora è presente: lascia un'icona desktop "Neuron" → doppio click d'ora in
    # poi (punta a `neuron gui`, che riapre il control center condiviso).
    _neuron_shortcut()
    return int(gui_main() or 0)


def _consolidate_cli(argv) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="neuron consolidate",
                                 description="Consolida lo store: merge near-duplicati + archivio orfani.")
    ap.add_argument("--context", help="solo questo contesto (default: tutti)")
    ap.add_argument("--no-merge", action="store_true", help="non fondere i near-duplicati")
    ap.add_argument("--no-drop-orphans", action="store_true", help="non archiviare gli orfani")
    ap.add_argument("--sim-threshold", type=float, default=0.85, help="soglia coseno per il merge")
    args = ap.parse_args(argv)

    from neuron.server import _g  # registry con l'embedder già registrato
    contexts = [args.context] if args.context else [c["context"] for c in _g.list_contexts()]
    if not contexts:
        print("Nessun contesto da consolidare.")
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
COMMANDS: "dict[str, tuple]" = {
    "setup":       ("neuron.setup",   "main", "lifecycle",  "Ciclo di vita: installa, aggiorna, ripara", False),
    "register":    ("neuron.clients", "cli",  "lifecycle",  "Registra il server MCP nei client AI", True),
    "gui":         (None,             None,   "lifecycle",  "Control center web condiviso (Gray Matter): se manca, lo installa da solo e apre", False),
    "start":       (None,             None,   "lifecycle",  "Avvia il server Neuron in background (bridge HTTP)", False),
    "stop":        (None,             None,   "lifecycle",  "Ferma il server Neuron", False),
    "bridge":      ("neuron.bridge",  "main", "lifecycle",  "Espone lo stdio server su HTTP (connettori remoti)", False),
    "tunnel":      ("neuron.tunnel",  "main", "lifecycle",  "HTTPS pubblico via cloudflared (con bridge)", False),
    "manage":      ("neuron.manage",  "main", "maintenance", "Gestione quotidiana del grafo", False),
    "consolidate": (None,             None,   "maintenance", "Fonde i near-duplicati e archivia gli orfani", False),
    "repair":      (None,             None,   "lifecycle",  "Reinstall pulito SOLO di Neuron: scegli se cancellare la memoria, poi reinstalla forzato", False),
    "record-paths":(None,             None,   "lifecycle",  "Neuron registra la sua cartella sorgente (usato dall'installer)", False),
    "go-standalone":(None,            None,   "lifecycle",  "Esce dal gateway GM: Neuron si registra come MCP diretto nei client (reversibile con gray-matter register --gateway)", False),
    "migrate":     (None,             None,   "maintenance", "Migra i grafi dalla vecchia slug (neuron5) alla nuova (neuron)", False),
    "doctor":      ("neuron.clients", "cli",  "inspect",    "Diagnostica e ripara le registrazioni nei client", True),
    "console":     ("neuron.console", "main", "inspect",    "Diagnostica del grafo in sola lettura (--watch)", False),
    "init":        ("neuron.init",    "main", "tuning",     "Cablaggio dei client (senza importare il server)", False),
    "connect":     ("neuron.connect", "main", "tuning",     "Collega e testa un DB Turso Cloud, poi salva", False),
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
                                 description="Avvia il server Neuron in background (bridge HTTP).")
    ap.add_argument("--port", type=int, default=8000, help="porta HTTP (default 8000)")
    ap.add_argument("--host", default="127.0.0.1", help="host (default 127.0.0.1)")
    args = ap.parse_args(argv)

    pid_file = _paths.data_dir() / "neuron_server.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            if _is_alive(pid):
                print(f"Neuron server già in esecuzione (PID {pid}).")
                return 0
        except (ValueError, OSError):
            pass  # PID file corrotto: ignora, sovrascriverà

    neuron_cmd = [sys.executable, "-m", "neuron"]
    try:
        from neuron.bridge import resolve_neuron_cmd, resolve_proxy_runner
        proxy = resolve_proxy_runner()
        if proxy is None:
            print("mcp-proxy non trovato. Installa uv o pipx.", file=sys.stderr)
            return 1
        full = proxy + [f"--port={args.port}", f"--host={args.host}", "--"] + neuron_cmd
    except ImportError:
        print("Bridge non disponibile. Aggiorna Neuron.", file=sys.stderr)
        return 1

    flags = 0
    if os.name == "nt":
        flags = 0x08000000 | 0x00000008  # CREATE_NO_WINDOW | DETACHED_PROCESS
    try:
        proc = subprocess.Popen(
            full,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
    except FileNotFoundError as exc:
        print(f"Impossibile avviare: {exc}", file=sys.stderr)
        return 1

    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(proc.pid), encoding="utf-8")
    time.sleep(1.0)
    if proc.poll() is not None:
        print(f"Neuron server è fallito subito (exit {proc.returncode}).")
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
                                 description="Ferma il server Neuron.")
    args = ap.parse_args(argv)

    pid_file = _paths.data_dir() / "neuron_server.pid"
    if not pid_file.exists():
        print("Neuron server non in esecuzione (nessun file PID).")
        return 0
    try:
        pid = int(pid_file.read_text().strip())
    except (ValueError, OSError):
        print("File PID corrotto.")
        pid_file.unlink(missing_ok=True)
        return 1
    if not _is_alive(pid):
        print(f"Neuron server non attivo (PID {pid} non trovato).")
        pid_file.unlink(missing_ok=True)
        return 0
    try:
        if os.name == "nt":
            os.kill(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        print(f"Processo {pid} già terminato.")
    except PermissionError:
        print(f"Permesso negato per PID {pid}.")
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
                                 description="Migra i grafi dalla vecchia slug (neuron5) alla nuova (neuron).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Mostra cosa farebbe senza spostare nulla")
    args = ap.parse_args(argv)

    result = _paths.migrate_graphs(dry_run=args.dry_run)

    if result["error"]:
        print(f"Errore: {result['error']}")
        return 1

    if not result["migrated"]:
        print("Niente da migrare.")
        return 0

    if args.dry_run:
        print(f"Dry-run: sposterei {result['old_path']} → {result['new_path']}")
    else:
        print(f"Migrato: {result['old_path']} → {result['new_path']}")

    return 0


def cli() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    spec = COMMANDS.get(cmd)
    if spec is not None:
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
