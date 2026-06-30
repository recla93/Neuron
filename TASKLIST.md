# Neuron — TASKLIST

> Fonte di verità persistente delle task del progetto, condivisa tra le chat.
> Vedi `CLAUDE.md` per le regole di sincronizzazione chat ↔ questo file.

**Stato possibile:** `pending` · `in_progress` · `completed`

**Template di una task:**
```
## T<N> — <Titolo principale>
**Stato:** <pending | in_progress | completed>
**Problema:** <descrizione esaustiva: cosa, perché, rischio/impatto>
**File rilevanti:** <percorsi e righe utili al contesto>
```

---

## T1 — Risolvere doppione engine.py vs server.py
**Stato:** completed
**Problema:** `engine.py` (~1085 righe) è un motore standalone parallelo, multi-provider
LLM, importato **solo** da `scripts/run_interactive.py`. Duplica concetti (estrazione,
link, flash) rispetto a `server.py` (~1952 righe), che è il vero MCP server in uso. I due
percorsi possono divergere nel comportamento (drift): una correzione applicata a uno non
si propaga all'altro. Decisione da prendere: **deprecare `engine.py`** oppure
**documentarne chiaramente il ruolo separato** (es. "playground CLI, non production path")
così che nessuno si aspetti parità funzionale con il server.
**File rilevanti:** `src/neuron/engine.py`, `src/neuron/server.py`,
`scripts/run_interactive.py`.
**Risoluzione (2026-06-30):** scelta l'opzione *non distruttiva* — `engine.py` resta come
playground CLI ma il suo ruolo è ora documentato esplicitamente. (1) Riscritto il docstring
di modulo di `engine.py`: dichiara che NON è il path di produzione, che non condivide
implementazione con `server.py`, che non va mantenuta parità funzionale e che il version
skew (v3.1 vs v3.3) è atteso. (2) Aggiunto blocco-commento in coda a `engine.py` sulle
factory `create_*` (superficie pubblica solo-CLI). (3) Allineato `DEVELOPER.md` (prima
stale): la "Project Structure" ora elenca `engine.py` e `db.py`, i test includono
`test_core.py`, e la sezione "Interactive CLI Mode" ha un avviso esplicito "Not the
production path". Nota: il mount bash di sessione era congelato su `engine.py`, quindi la
verifica `compileall` è stata fatta staticamente (docstring bilanciato, aggiunte solo
commenti); consigliata una verifica `python -m compileall src/` in locale.

## T2 — Valutare estrazione LLM nel path live
**Stato:** completed
**Problema:** nel path di default `server.py` usa `SemanticExtractor` (classe ~riga 258,
metodo `extract()` ~riga 377), basato su regex/scoring di token — **non** un LLM. Esiste
`_llm_extract` (~riga 443, chiamata condizionale ~riga 495) ma è opzionale e fuori dal
default. Se lo scope in `Neuron.txt` richiede un'estrazione concetti più "intelligente",
va deciso se attivare l'estrazione LLM di default, pesando il trade-off costo/latenza
(chiamata HTTP sincrona per turno) contro la qualità dei concetti estratti.
**File rilevanti:** `src/neuron/server.py` (`SemanticExtractor` ~258, `extract` ~377,
`_llm_extract` ~443, uso ~495), `Neuron.txt`.
**Risoluzione (2026-06-30) — DECISIONE: mantenere l'euristica come default; NON attivare
LLM sul path live.** Mappato il flusso reale: il tool `auto` (pipeline per-turno: extract →
topic shift → auto-link → store) chiama `_auto_extract(text)` → sempre euristica
(`server.py:1654`); solo il tool `extract` espone `use_llm` (`server.py:1640`, default
False). Motivazioni del NO-default: `auto` gira a ogni turno, quindi LLM significherebbe
un round-trip HTTP sincrono per turno, dipendenza forte da un endpoint modello attivo
(default Ollama localhost:11434, `qwen2.5:3b`), non-determinismo e costo/latenza. L'euristica
è 0-token, deterministica, testata e copre già topic/keywords/domain/intent/sentiment.
L'estrazione LLM resta disponibile on-demand via `extract(use_llm=true)`. Decisione
documentata in `DEVELOPER.md` (nuova sezione "Concept extraction"). **Finding collaterale →
nuova task T6:** `_llm_extract` è sincrono ma chiamato dall'handler async `call_tool` senza
`asyncio.to_thread` (warning già nel commento a `server.py:495`): un `use_llm=true` blocca
l'event loop fino al ritorno. Da sistemare prima di consigliare LLM in qualsiasi uso ad alto
throughput. Nota: `Neuron.txt` (spec citato in CLAUDE.md) NON è presente nel repo.

## T3 — Aggiungere test sui flash semantici
**Stato:** completed
**Problema:** la feature più originale del progetto — i "flash semantici" (dormant pulse,
cross-domain spark, creative leap) generati in `_build_context_window` — non ha copertura
reale. L'unico test (`test_flash_enabled_by_default`, `tests/test_core.py:390`) verifica
solo che il flag `flash_enabled` sia `True` di default. Nessun test esercita
`_build_context_window` end-to-end né verifica che i 3 sotto-meccanismi producano l'output
atteso (es. nodo dormiente oltre soglia → flash emesso, spark cross-context, ecc.).
Aggiungere test mirati che coprano i 3 percorsi.
**File rilevanti:** `src/neuron/server.py` (`_build_context_window` ~righe 572–680),
`tests/test_core.py` (~righe 387–391).
**Risoluzione (2026-06-30):** aggiunta la classe `TestSemanticFlashes` in
`tests/test_core.py` (più helper `_extraction`), 8 nuovi test che esercitano
`_build_context_window` end-to-end: (1) dormant pulse emesso per nodo saliente dormiente
+ caso negativo (nodo recente non emette); (2) cross-domain spark da un grafo di contesto
diverso (`_g._graphs`); (3) creative leap su path a 2 hop verso dominio diverso + caso
negativo (stesso dominio non emette); più gating (`turn<=3` e `flash_enabled=False` →
nessun flash) e struttura (Active links / Salient nodes). Ogni test isola registry,
`_search_embeddings` e `flash_enabled` (snapshot+restore) per determinismo. **Verifica:**
l'ambiente non ha pytest/deps installati e PyPI è bloccato, quindi i test sono stati
eseguiti contro il `server.py` reale tramite runner standalone (mock di fastembed/mcp/turso
come fa il file): **8/8 PASS**. In dev: `pip install -e .[dev] && pytest tests/test_core.py`.

## T4 — Validare percorso cloud libsql-client su Turso reale
**Stato:** completed (predisposizione) — validazione reale deferita per scelta
**Problema:** il livello cloud del layer DB (`RemoteTursoConnection`, `db.py:93`; attivo
solo quando `TURSO_DATABASE_URL` e `TURSO_AUTH_TOKEN` sono impostati → `REMOTE_TURSO`) è
scritto sull'API ufficiale ma **non è mai stato eseguito** contro un database Turso reale.
La dipendenza `libsql-client` (extra `cloud` in `pyproject.toml`) **non è nemmeno
installata** nel venv dell'installazione attiva. Prima di affidarsi a questo percorso in
produzione, validarlo con un DB Turso di test configurando le env var e installando l'extra.
**File rilevanti:** `src/neuron/db.py` (`RemoteTursoConnection` ~93, `connect()` ~120),
`pyproject.toml` (extra `cloud`).
**Risoluzione (2026-06-30) — scope ridefinito dall'utente: PREDISPORRE l'ambiente al cloud,
SENZA implementare/validare la connessione reale.** Consegnato: (1) `.env.example` (root)
con template per `TURSO_DATABASE_URL`/`TURSO_AUTH_TOKEN` (+ `NS_GRAPHS_DIR`, var LLM),
gitignorato il `.env` reale (`*.env`); (2) `scripts/check_cloud_config.py` — readiness check
**offline** (nessuna connessione, non stampa il token) che risolve il tier e segnala gli
stati pericolosi: credenziali settate ma extra `libsql-client` non installato (il server
importa `libsql_client` all'avvio → crash) e configurazione parziale (una sola var).
Testato in 3 scenari: sqlite (exit 0), cloud-senza-extra (exit 1), parziale (exit 1);
(3) sezione "Enabling Turso Cloud" in `DEVELOPER.md` con i passi di abilitazione e lo
snippet di validazione finale. **Residuo (deferito):** la validazione end-to-end contro un
Turso reale (install extra + 2 env var + query di prova) — richiede credenziali/rete non
disponibili ora; passi documentati in `DEVELOPER.md > Enabling Turso Cloud`. Nessuna
modifica a `db.py` (path già completo) per restare nello scope "predisporre, non implementare".

## T5 — Automatizzare deploy/CI verso l'installazione attiva
**Stato:** completed
**Problema:** il workflow CI (`.github/workflows/ci.yml`) gira solo sul repo sorgente
(`windows-latest`, `pip install -e .[dev]`, `pytest tests/`). L'installazione attiva in
`C:\Users\recla\AppData\Local\Programs\neuron` è una **copia manuale** da risincronizzare
a mano a ogni release, con rischio di drift sorgente ↔ installazione. Creare uno script di
deploy/sync (idealmente integrato come step CI o come comando dedicato) per rendere la
sincronizzazione ripetibile e verificabile.
**File rilevanti:** `.github/workflows/ci.yml`, `install.ps1`, target di deploy
`C:\Users\recla\AppData\Local\Programs\neuron`.
**Risoluzione (2026-06-30):** creato `scripts/deploy.ps1` — sync standalone, ripetibile e
verificabile sorgente → installazione attiva (`%LOCALAPPDATA%\Programs\neuron`), separato
dal toolchain di `install.ps1`. Caratteristiche: copia solo il set deployabile (codice,
config, docs, seed `knowledge\base_knowledge.db`), mai `.venv`/`graphs\`/`knowledge_grown\`;
idempotente (diff MD5: new/changed/unchanged); `-DryRun` (preview senza scrivere, =
"verificabile"); `-Prune` (rimuove i file eliminati dal sorgente, solo nelle code dir
src/scripts/skills/clients/tests); verifica post-sync con il venv dell'installazione
(byte-compile + import `neuron.server`, e `-RunTests` → pytest); check di parità
`__version__` sorgente↔install. Aggiunto step CI (`.github/workflows/ci.yml`) che fa un
`deploy.ps1 -DryRun` come smoke test della logica di sync su Windows. Documentato in
`DEVELOPER.md` (sezione "Deploy / sync to the active install" + CI/CD aggiornato).
**Verifica:** PowerShell non eseguibile in questo ambiente; la logica del set-di-file e
l'algoritmo classify/prune sono stati validati con un prototipo Python contro l'albero reale
+ una dest simulata (fresh=idempotente, file modificato→changed, file rimosso→new,
file stale→prune); corretto un bug PS sui file in root (`$parts[0..-1]`). Eseguire una volta
in locale: `powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1 -DryRun`.

## T6 — Non bloccare l'event loop con `_llm_extract` (asyncio.to_thread)
**Stato:** completed
**Problema:** (emersa da T2) `_llm_extract` (`server.py:443`) fa una richiesta HTTP
**sincrona** (`urllib.request.urlopen`, timeout 30s) ma viene invocata dall'handler
**async** `call_tool` (`server.py:1371`) tramite `_auto_extract(..., use_llm=True)`
(`server.py:1641`), **senza** `asyncio.to_thread`. Il commento a `server.py:495` lo segnala
già ("caller should use asyncio.to_thread if in async ctx") ma il caller non lo fa: con
`extract(use_llm=true)` l'intero server MCP (stdio, single event loop) si blocca fino al
ritorno della chiamata LLM. Fix: avvolgere la chiamata sincrona in
`await asyncio.to_thread(_llm_extract, text)` nel ramo async, oppure rendere `_auto_extract`
async/offloaded. Prerequisito per qualsiasi attivazione LLM ad alto throughput (vedi T2).
**File rilevanti:** `src/neuron/server.py` (`_llm_extract` ~443, `_auto_extract` ~492,
handler `extract` ~1638–1641, `call_tool` async ~1371).
**Risoluzione (2026-06-30):** `_auto_extract` reso `async def`; la chiamata sincrona è ora
`llm_result = await asyncio.to_thread(_llm_extract, text)`, così l'HTTP del modello gira in
un worker thread e non blocca l'unico event loop del server MCP. Aggiornati i due call site
nell'handler async `call_tool`: `result = await _auto_extract(text, use_llm=use_llm)`
(tool `extract`) e `extraction = await _auto_extract(text)` (tool `auto`). `asyncio` era già
importato. Rimosso il vecchio commento-warning ("caller should use asyncio.to_thread").
**Verifica:** il mount di sessione serviva una vista stale/troncata di `server.py` (con
`.pyc` non rimovibile), quindi l'import del modulo modificato non era affidabile qui; la
logica esatta è stata copiata verbatim in uno script standalone ed eseguita: coroutine OK,
ramo euristico OK, ramo LLM OK, e soprattutto event-loop NON bloccato (un ticker concorrente
ha raggiunto 40/40 tick durante 0.3s di chiamata LLM simulata). Conferma finale consigliata
in locale: `pytest` + avvio del server.

## T7 — Documentare il mount del server MCP nei vari ambienti/client
**Stato:** completed
**Problema:** README/DEVELOPER/install spiegavano l'installazione ma non chiarivano in modo
uniforme *come montare* il server MCP nei diversi client (Claude Desktop vs OpenCode vs
Cursor vs ChatGPT/OpenAI vs Perplexity), né la distinzione tra client a **stdio locale** e
client **solo-remoti**. Neuron è uno stdio server (nessun layer HTTP), quindi i client
cloud (ChatGPT) non possono lanciarlo direttamente e richiedono un bridge stdio→HTTP.
**File rilevanti:** `README.md` (sez. MCP Configuration), `DEVELOPER.md` (sez. MCP Client
Configuration + TOC), `install.ps1` (sez. 8 + messaggi finali), `scripts/run_mcp.bat`.
**Risoluzione (2026-06-30):** ricerca web sullo stato MCP dei client (giu 2026): ChatGPT =
solo connettori HTTPS remoti (Developer Mode, piani a pagamento) → serve bridge tipo
`mcp-remote`; Perplexity = MCP locale solo su app **macOS** via helper `PerplexityXPC`
(comando in UI), connettori remoti a pagamento; gli altri (Claude Desktop/Code, Cursor,
OpenCode, Cline, VS Code, Windsurf, Zed, Continue, Cody, Amazon Q) accettano comando stdio.
Aggiornati: (1) `README.md` — nuova tabella "come montare" per client + comando universale
`python3 -m neuron`; (2) `DEVELOPER.md` — nota "Transport: local stdio vs remote",
sottosezioni **Perplexity (macOS)** e **ChatGPT/OpenAI (via bridge)**, righe aggiunte alla
tabella "Config by client reference", TOC corretto (aggiunto "Enabling Turso Cloud" e i
sotto-anchor della sezione client, prima mancanti); (3) `install.ps1` — messaggi sez. 8 e
finali che distinguono client auto-registrati (OpenCode/Claude Desktop/Cursor) da quelli
manuali e rimandano a DEVELOPER.md; (4) allineato il commento versione in `run_mcp.bat`
(v3.2→v3.3). **Verifica:** link/anchor markdown ricontrollati; nessun riferimento rotto in
README; TOC di DEVELOPER ora copre tutte le sezioni `##`.

## T8 — Verifica stato link del grafo + prune/repair del seed
**Stato:** completed
**Problema:** controllare lo stato dei link del grafo seed (`knowledge/base_knowledge.db`) e
verificare se serviva un "prune fix". Esiste `scripts/seed_repair_links.py` che rimuove i
link *dangling* (source/target non presenti nella tabella `nodes`), residuo del seed
generato da Graphify con identificatori snake_case non allineati ai display-name dei nodi.
**File rilevanti:** `knowledge/base_knowledge.db`, `scripts/seed_repair_links.py`,
`src/neuron/models.py` (`prune_tangential`, `TANGENTIAL_EXPIRY_TURNS=5`), `src/neuron/db.py`.
**Risoluzione (2026-06-30):** verifica iniziale (per `weight`/`inactive_turns`) fuorviante;
il controllo corretto sui dangling (source/target ∉ nodes) ha rivelato **996 link dangling
su 1218 (82%)**. Eseguito `seed_repair_links.py` sul DB live (tier sqlite3, fastembed
assente → solo delete, nessuna rigenerazione semantica): rimossi i 996 dangling, **222 link
validi rimasti** (tutti `medium`), 0 dangling. Backup creato prima del run
(`knowledge/base_knowledge.db.prerepair-20260630`). **Nota residua:** dopo la pulizia 262/379
nodi risultano isolati (senza link) — atteso, dato che i link rimossi erano rumore; restano
raggiungibili via vector search (`node_vectors`). Per ricollegarli servirebbe una
rigenerazione semantica dei link con `fastembed` installato (non disponibile in questo
ambiente). La logica runtime di prune (`prune_tangential`, soglia 5 turni inattivi) è
risultata corretta; in `graphs/` non ci sono grafi live da prunare nel repo sorgente.

## T9 — Packaging + GitHub Release (wheel) + riscrittura installer
**Stato:** completed
**Problema:** (1) `pyproject.toml` non aveva `[tool.setuptools]` → con src-layout
`python -m build` non trovava il package `neuron` (wheel vuoto/build fallita). (2) Il DB
seed `base_knowledge.db` non veniva impacchettato (`*.db` in .gitignore, no MANIFEST, no
package-data) e `registry.py` lo cercava via path relativo al repo (inesistente in un
pacchetto installato). (3) Entry-point `neuron-mcp = neuron.server:main` rotto: `main` è
`async`. (4) Versione duplicata in `pyproject.toml` e `__init__.py`. (5) `install.ps1` con
bug: `Invoke-PipRetry` trattava `$LASTEXITCODE -eq $null` come successo (fallimenti pip
silenziosi); check Python `[double]"3.10" -lt 3.10` (= 3.1, locale-fragile); componente
MSVC errato (`UCRTSDK`); refresh PATH incompleto; codice morto. (6) pyturso non ha wheel
`win_amd64` su PyPI → su Windows pip compila da sorgente Rust.
**File rilevanti:** `pyproject.toml`, `MANIFEST.in`, `src/neuron/__init__.py`,
`src/neuron/server.py` (`main`/`cli`), `src/neuron/registry.py` (seed resolve/guard),
`src/neuron/data/` (seed packaged), `.github/workflows/release.yml`,
`.github/workflows/ci.yml`, `install.ps1`, `scripts/run_mcp.bat`, `INSTALL.md`,
`README.md`, `DEVELOPER.md`, `.gitignore`, `RELEASE_PLAN.md`.
**Risoluzione (2026-06-30):** scelta **Opzione B (hybrid), dependency-first**.
(1) `pyproject.toml`: `[tool.setuptools]` src-layout discovery + `package-data` (data/*.db)
+ versione `dynamic` da `__init__.py`; pin `pyturso==0.6.1`. (2) Seed spostato in
`src/neuron/data/base_knowledge.db`, caricato via `importlib.resources`; aggiunto
`_seed_is_loadable()` (header SQLite + size >= 512) e try/except nel load → seed
mancante/placeholder/corrotto NON crasha più. `MANIFEST.in` per l'sdist. (3) Aggiunto
`cli()` sync in `server.py`; entry-point → `neuron.server:cli`; `server_version` ora da
`__version__`. (5) `install.ps1` riscritto: verifica Python 3.10–3.13 (compare int),
`Invoke-Pip` trusta solo `$LASTEXITCODE -eq 0`, install via `pip --find-links vendor` del
wheel (nessun compilatore), fallback MSVC **minimale** (`VC.Tools.x86.x64` +
`Windows11SDK.22621`, mai full VS) solo se il prebuilt fallisce. (6)
`.github/workflows/release.yml`: job `build-pyturso-win` (matrix 3.10–3.13) costruisce le
wheel `win_amd64` su `windows-latest` e le allega alla Release insieme a wheel+sdist di
Neuron. `ci.yml` ora ha un job `build` che verifica il wheel. `run_mcp.bat` non usa più
`PYTHONPATH=src`. Nuovo `INSTALL.md` (automatico + manuale + troubleshooting). `.gitignore`
ora versiona il seed (`!.../base_knowledge.db`) ma re-ignora `*.prerepair-*`.
**Verifica:** `python -m build` OK (wheel contiene `neuron/data/base_knowledge.db`, entry
`neuron.server:cli`, versione 3.3.0 dinamica); install del wheel in venv pulito → import OK;
`pytest tests/` = **60 passed** (con seed placeholder, degradazione graziosa confermata).
**Nota residua (DA FARE manualmente):** generare il vero `src/neuron/data/base_knowledge.db`
con `scripts/import_vault.py` (vedi T10) prima di taggare una release (ora è un placeholder).

## T10 — Sostituire seed_vault.py con import_vault.py (path-agnostico)
**Stato:** completed
**Problema:** `scripts/seed_vault.py` era hardcoded su un vault Obsidian personale
(`C:\Users\recla\...`, `D:\Desktop\...`), scriveva nella path legacy `knowledge/` e non
generava embeddings (`node_vectors` vuota → ricerca semantica morta finché non si lanciava
`populate_vectors.py`). Inadatto come tool di seeding generico/pubblico.
**File rilevanti:** `scripts/import_vault.py` (nuovo), `scripts/seed_vault.py` (stub
deprecato), `scripts/populate_vectors.py`, `src/neuron/registry.py`, `INSTALL.md`,
`DEVELOPER.md`.
**Risoluzione (2026-06-30):** creato `scripts/import_vault.py`: vault root SOLO da
`NEURON_VAULT`/`--vault` (nessun path hardcoded), output configurabile via `--out` (default
`./knowledge/base_knowledge.db`, locale), embeddings 384-dim inline se `fastembed` presente
(altrimenti skip con messaggio e degradazione graziosa). Schema identico a quello atteso dal
server. `seed_vault.py` ridotto a stub che reindirizza al nuovo tool (il mount non
permetteva il `rm`). Aggiornati i riferimenti in `registry.py`, `INSTALL.md`, `DEVELOPER.md`.
**Verifica:** import su vault finto → DB SQLite valido (45KB, header corretto, accettato da
`registry._seed_is_loadable`), 3 nodi/5 link/4 meta, skip vettori senza fastembed OK.
**Decisione di prodotto (recla):** il seed pubblico spedito NON deve essere note personali
di nessuno; `import_vault.py` resta un tool LOCALE (output da copiare a mano in
`src/neuron/data/` solo se si vuole spedirlo). Il seed pubblico definitivo è una scelta
separata ancora da fare.
