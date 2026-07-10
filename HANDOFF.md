# Neuron — Handoff: passi da fare "in ambiente" (lato Windows)

Piccolo runbook riutilizzabile. Quando serve, passa questo a Claude: Claude fa le
modifiche nel repo di sviluppo e prepara i file; **tu** esegui i comandi numerati
sul tuo Windows (le cose che l'ambiente cloud/bridge non può fare).

## Mappa (dove sta cosa)

- **Repo di sviluppo:** `C:\Users\recla\Desktop\NEURON\Update\neuron-project`
- **Install attiva (server MCP):** `%LOCALAPPDATA%\Programs\neuron5` — copia deployata; va **risincronizzata a ogni release/bugfix** (il server MCP esegue questa copia, non il repo).
- **Git remote:** `https://github.com/recla93/Neuron.git` — branch di lavoro `FiveFix`, default `master`.
- **Slug/identità v5:** `neuron5` (chiave MCP, install dir, store `…\neuron5\graphs`). Il modulo Python resta `-m neuron`.

## Perché questi passi sono manuali (la divisione dei compiti)

Il bridge/cloud **non può**: (a) fare scritture git sul mount del device (manca `unlink` → l'index si corrompe), (b) lanciare l'installer PowerShell, (c) eseguire `pytest` (il `.venv` è Windows, la VM del bridge è Linux), (d) l'integrazione reale con Turso cloud. Quindi: **Claude** edita il repo di sviluppo + prepara CHANGELOG/versione; **tu** committi, installi, testi e tagghi.

## Flusso standard per un fix / rilascio

1. **(Claude)** applica i fix nel repo di sviluppo, aggiorna `CHANGELOG.md` e `src/neuron/__init__.py` (`__version__`).
2. **(Tu)** fine-riga, una volta sola (se il diff è sporco di CRLF):
   ```
   git add --renormalize .
   ```
3. **(Tu)** commit + merge + push:
   ```
   git add <file nuovi>            # es. HANDOFF.md, docs/releases/..., test nuovi
   git commit -m "<messaggio>"
   git push -u origin FiveFix
   git checkout master
   git merge --ff-only FiveFix
   git push -u origin master
   git checkout FiveFix
   ```
   Controllo: NON deve mai finire in git il file `.env` o i backup `*.neuron-bak*` (segreti Turso).
4. **(Tu)** reinstalla per aggiornare il server MCP live (porta il nuovo codice in `…\Programs\neuron5`). Poi riavvia i client MCP.
5. **(Tu)** test reali: `python -m pytest -q`; per il cloud condiviso lancia **una volta** `python scripts/init_cloud.py` prima che i colleghi si colleghino; verifica che tutti usino lo stesso `NS_EMBED_MODEL`.
6. **(Tu)** tag della release (bugfix = bump di patch):
   ```
   git tag -a vX.Y.Z -m "Neuron X.Y.Z"
   git push origin vX.Y.Z          # fa partire release.yml
   ```

## In sospeso ora → 5.1.1 (bugfix dopo la 5.1.0)

La 5.1.0 è già uscita. Le modifiche fatte **dopo** il finalize del CHANGELOG 5.1.0 sono bugfix, da rilasciare come **5.1.1**:

- **OpenCode handshake** (`clients/opencode-plugin/neuron-handshake.mjs`): era `export default` → OpenCode non lo caricava; ora export nominato.
- **Example config** (`clients/*.example.json`): chiave `neuron` → `neuron5` e path `…\Programs\neuron` → `…\Programs\neuron5` (il modulo `-m neuron` resta).
- **Installer Codex** (`scripts/configuration.ps1`): `config.toml` non viene più **sovrascritto** (merge non distruttivo) + `hooks.json` con schema corretto (`type`/`command`) + flag `[features] codex_hooks = true`.
- **Installer Zed** (`configuration.ps1` + `zed.example.json`): formato **piatto** `command`/`args` (non più annidato).

Per chiudere la 5.1.1: aggiungi una sezione `## [5.1.1] — <data>` in `CHANGELOG.md` con questi punti, bumpa `__version__` a `5.1.1`, poi esegui il flusso sopra (commit → merge → reinstalla → testa → tag `v5.1.1`). Chiedi a Claude di preparare CHANGELOG+bump se vuoi.

## Note / trappole

- **PowerShell non è testabile in cloud** (`pwsh` assente): dopo un fix a `configuration.ps1`, al reinstall tieni d'occhio errori di sintassi e, per Codex, testa partendo da un `~/.codex/config.toml` **già popolato** (deve preservarlo, non cancellarlo).
- Se l'index git si corrompe di nuovo lato bridge, non è dato perso: sposta (`mv`, non `rm`) `.git/index` e `.git/index.lock` e ricostruisci con `git read-tree HEAD` — ma le scritture git falle su Windows.
