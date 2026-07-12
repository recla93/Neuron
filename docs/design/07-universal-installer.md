# ADR-007 — Installer universale (macOS / Linux / Windows) e separazione dei ruoli

Stato: **Proposed** (target: 5.4). Decidere prima della prossima major di installer.

## Problema
L'installazione oggi è PowerShell-only (`install.ps1`, `Neuron5Config.bat`,
`configuration.ps1` ~2700 righe): macOS e Linux sono di fatto esclusi, e il
Configuration Center mescola due ruoli — *ciclo di vita* (install/repair/
uninstall) e *gestione* (bridge, console, visualizer, test, cloud).

## Decisione proposta
1. **Distribuzione: `pipx install neuron`** (o `pip install` in venv). Il wheel
   esiste già (CI T13), le dipendenze pure-Python + fastembed/pyturso hanno
   wheel per le tre piattaforme. pipx dà: PATH, isolamento, `pipx upgrade
   neuron` = update. Niente compilatori, niente admin.
2. **Ciclo di vita: `neuron setup`** — nuovo sottocomando interattivo
   cross-platform (stdlib TUI a numeri, come il fallback del menu PS):
   *install* (= register nei client via `clients.py` + prewarm modello +
   doctor), *repair* (= `doctor --fix`), *uninstall* (= de-register +
   rimozione dati opt-in, port di `uninstall.ps1` livelli -Data/-Secrets/
   -Cache). Riusa al 100% il motore già esistente e già testato (17 test).
3. **Gestione: `neuron manage`** — porta del Configuration Center per le
   feature quotidiane (bridge+tunnel, console live, visualizer, test, cloud
   connect, embedding model switch). Fase 2: si può fare a pezzi, il
   visualizer e connect_turso sono GIÀ script Python cross-platform.
4. **Windows resta com'è, sopra il core Python**: `install.ps1` e
   `configuration.ps1` diventano progressivamente wrapper UI che chiamano
   `neuron setup/manage` (come già fanno per register/doctor) — nessuna
   feature persa, un solo motore.

## Contro l'.exe (PyInstaller/Nuitka onefile)
Valutato e SCONSIGLIATO come via primaria: 3 build per OS da firmare,
falsi positivi antivirus cronici su PyInstaller, ~150MB per bundlare
fastembed/onnx, e il server MCP gira comunque su Python del sistema — l'exe
duplicherebbe il runtime senza eliminare il prerequisito. Un eventuale
launcher .exe può arrivare DOPO, come sottile wrapper che invoca pipx.

## Percorsi client non-Windows (già pronti in clients.py, da estendere)
- Claude Desktop macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Claude Code / Cursor / Codex: già home-relative (funzionano ovunque)
- VS Code macOS: `~/Library/Application Support/Code/User/settings.json`;
  Linux: `~/.config/Code/User/settings.json` (fallback già presente)
- Zed Linux/macOS: `~/.config/zed/settings.json` (fallback già presente)

## Piano (T63)
1. `neuron setup` MVP: register/doctor/uninstall interattivi (riuso clients.py,
   + port dei path macOS sopra). 2. `pyproject`: console script `neuron` già
   esistente → documentare pipx in README/INSTALL. 3. Smoke su macOS/Linux
   (CI: job ubuntu+macos che fa pipx install dal wheel e `neuron setup
   --dry-run`). 4. `neuron manage` fase 2. 5. PS diventa wrapper.
