# Piano 05 — Core efficiente + Installer centralizzato auto-riparante

Data: 2026-07-10 · Branch: FiveFix · Analisi condotta via Graphify (`graphify-out/graph.json`,
1418 nodi / 2806 link) + review diretta di `server.py`, `models.py`, `install.ps1`,
`scripts/configuration.ps1`, `scripts/_neuron_paths.ps1` + log d'installazione del PC di un
collega (giuse).

Obiettivo: (A) core veloce, cost/token-effective e corretto; (B) installer unico,
auto-riparante, preciso su path e posizioni di server/plugin, massima compatibilità.

---

## Parte A — Core: efficienza, costo, token, correttezza

### A1 — Link writes da O(L) a O(attivi) per turno  ⭐ il taglio più grande
`Graph.increment_inactivity` (`models.py:649-668`) tocca `inactive_turns` di OGNI link a ogni
turno → ogni link finisce in `_dirty_links` e viene ri-upsertato a ogni save. In locale è
rumore; su Turso Cloud è O(L) righe di rete per turno. È il residuo dichiarato di T12/Fase 2.
**Fix:** smettere di persistere `inactive_turns`; derivarlo al load come
`turn_count - last_active_turn` (invariante già mantenuta dal loop). Solo i link *attivi* del
turno (reset + `last_active_turn`) richiedono scrittura. Migrazione: la colonna può restare
(ignorata in scrittura) → nessun breaking change di schema.
File: `src/neuron/models.py` (increment_inactivity, load_sqlite, _save_delta), test dedicato.

### A2 — Pipeline per-turno single-pass (cache intra-call)
In un singolo `store_turn` la catena `auto_link → _search_embeddings` e
`_build_context_window → _search_embeddings` ripete ricerche/embedding sulle stesse keyword;
`pre_turn` ricostruisce la sim-map da capo via `_resolve_context`. La cache embedding (T49) copre
i vettori ma non le query DB né la costruzione dei risultati.
**Fix:** memo per-call (dict passato lungo la catena o cache keyed su `(turn, frozenset(kws))`)
dei risultati di `_search_embeddings`; aprire la connessione al DB attivo una volta per tool-call
invece che per funzione (il seed è già cached, T49).
File: `src/neuron/server.py` (`_search_embeddings` ~840, `_auto_link` ~524,
`_build_context_window` ~564, `_resolve_context` ~1627).

### A3 — Pre-warm embedder in background all'avvio
L'embedder è lazy (`_get_embedder`, server.py:741): il PRIMO `pre_turn` della sessione paga il
load del modello (~2.8s da cache, di più a freddo). Il grafo memoria stesso registra il tema
("startup latency, fastembed, warm-up").
**Fix:** in `main()`, dopo l'handshake, `asyncio.create_task(asyncio.to_thread(_get_embedder))`
best-effort (try/except, mai fatale). Latency del primo turno ≈ zero senza costo di startup
bloccante.
File: `src/neuron/server.py` (`main` ~2435).

### A4 — Dieta token sulle risposte per-turno
Posture già buona (pre_turn default 200 tk, stimulus cap ~40 tk, signpost 626 char). Restano:
1. `_LOOP_HINT` (~15 tk) viene appeso a OGNI tool non-core per tutta la sessione → renderlo
   one-shot o ogni N call (flag di processo): dopo il primo nudge il modello l'ha visto.
2. `pre_turn` può emettere insieme `staged` + `stimulus` + riga "→ next" → dedup quando
   coesistono (una sola riga di guida).
File: `src/neuron/server.py` (`call_tool` ~1795, handler `pre_turn` ~2307).

### A5 — Coseno esplicito nel fallback Python (correttezza)
Follow-up noto (E1.x/T49): il fallback usa similarità che coincide col coseno SOLO per vettori
unit-norm. T49 ha già normalizzato `_search_embeddings`/`_refine_domain`; verificare che ogni
path residuo (es. `models._cos`, spreading seeds) usi coseno normalizzato, e aggiungere un test
con vettori non normalizzati.

### A6 — Coerenza identità server
`main()` dichiara `server_name="neuron"` mentre l'identità v5 è `neuron5` (slug, store, chiave
MCP; T39 indicava `Server("neuron5")`). Allineare a `_resolve_slug()` — cosmetico ma è
"precisione e coerenza".
File: `src/neuron/server.py` (`_resolve_slug` ~51, `main` ~2442).

### A7 — Benchmark di regressione perf
Per validare A1/A2 con numeri: script `scripts/bench_turn.py` che genera un grafo sintetico
(1k/5k nodi, 3k/15k link) e misura ms/turno e n° statement SQL per `store_turn`/`pre_turn`
(prima/dopo). Gate manuale, non CI (dipende dall'hardware).

### A8 — (opzionale) escludere `engine.py` dal wheel
1119 righe di playground CLI non-production dentro il pacchetto installato. Escluderlo dal
wheel (resta nel repo per `run_interactive.py`) riduce superficie e confusione. Decisione owner.

**Non serve fare** (già a posto, verificato): upsert incrementali e atomici (T11/T12), split
sessione/conoscenza + transazioni batch + retry sul cloud (T50), cache embedding e seed
connection (T49), consolidation/ANN threshold documentati (E1), estrazione euristica 0-token
come default (T2).

---

## Parte B — Installer: centralizzato, auto-riparante, preciso

### Diagnosi dal log del collega
1. `~/.claude.json` "isn't plain JSON" → l'installer si ferma e stampa uno snippet manuale
   **con backslash non escapati** (`"C:\Users\giuse\..."` = JSON invalido se incollato).
2. Config Claude Desktop trovata sotto **MSIX**
   (`...\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude`) — l'installer guarda solo
   `%APPDATA%\Claude\`.
3. Entry residua `neuron5` che punta a un **venv diverso** (cruft di un test vecchio) — nessuno
   strumento la rileva/ripara.
4. `~/.claude.json` è lo **state file live** di Claude Code: l'edit diretto può essere
   sovrascritto dal processo al riavvio (entry che "sparisce").
5. Bug trovati in review: `Register-CodexMcp` (install.ps1:548-556) **sovrascrive l'intero**
   `~/.codex/config.toml` con la sola entry neuron (distruttivo per chi ha altri server);
   `Register-McpNested` non fa verify+rollback post-write (Register-Mcp sì).
6. Causa strutturale: **due implementazioni parallele** della registrazione
   (`install.ps1` Register-Mcp/Nested/Codex vs `configuration.ps1` Write-ClientConfig) → drift
   e fix applicati a una sola delle due.

### B1 — Motore di registrazione UNICO in Python (`neuron register` / `neuron doctor`)
Estendere `src/neuron/init.py` (già stdlib-only, idempotente, testato) a modulo `clients`:
- registry dati dei client (path candidati, chiavi annidate, formato json/jsonc/toml) in un
  unico file (porting di `_neuron_paths.ps1:190-196`), condiviso;
- `neuron register --client <x> [--all] [--dry-run]`: merge non distruttivo, backup, verify
  post-write + rollback, output "cosa ho fatto/cosa fare a mano";
- `install.ps1` e `configuration.ps1` diventano UI che invocano il motore (via venv python).
  Un solo posto da fixare, testabile con pytest su fixture reali (= precisione).
File: `src/neuron/init.py` → `src/neuron/clients.py`, `tests/test_clients.py`,
`install.ps1` (sez. 6), `scripts/configuration.ps1` (Write-ClientConfig).

### B2 — Path detection Claude Desktop (Store/MSIX)
Candidati in ordine: `%APPDATA%\Claude\claude_desktop_config.json` e
`%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\claude_desktop_config.json`.
Regola: se esiste uno solo → quello; entrambi → il più recentemente modificato + warning
esplicito. Stessa logica in uninstall/doctor.

### B3 — Claude Code via CLI ufficiale, mai edit diretto dello state file
Se `claude` è in PATH: `claude mcp add --scope user <slug> <venv-python> -- -m neuron`
(+ `claude mcp list` come verify). Risolve in un colpo: JSONC, file live sovrascritto al
riavvio, escaping. Fallback (CLI assente): edit attuale MA con avviso "chiudi Claude Code
prima" + verify post-riavvio documentata; snippet manuale SEMPRE con JSON valido
(backslash escapati) e path del file di destinazione.

### B4 — Lettura JSONC + snippet manuali corretti
Parser tollerante (strip di `//`, `/* */`, trailing comma) usato SOLO per LEGGERE lo stato
(doctor/verifiche), mai per riscrivere un file JSONC (si perderebbero i commenti): per
VS Code preferire `code --add-mcp '<json>'` se il CLI esiste; altrimenti snippet manuale.
Tutti gli snippet manuali generati da un serializzatore JSON reale, mai da string-interpolation.

### B5 — Fix bug distruttivi/incompleti (subito, indipendente dal resto)
- `Register-CodexMcp`: parse/merge del TOML esistente (o append mirato della sezione
  `[mcp_servers.<slug>]` se assente; replace solo di quella sezione se presente), backup.
- `Register-McpNested`: verify post-write + rollback (copiare il pattern di Register-Mcp).
File: `install.ps1:527-556`.

### B6 — `neuron doctor`: diagnosi e auto-riparazione
Comando (e voce menu "Check & repair") che scansiona tutti i client noti e per ogni entry
neuron/neuron5 verifica: (a) config parsabile; (b) `command` esiste su disco; (c) punta al
venv dell'install corrente (slug giusto); (d) niente doppioni neuron+neuron5 o entry verso
venv inesistenti (il "cruft" del log); (e) hook/plugin (Claude Code SessionStart, OpenCode
neuron-handshake) presenti e con path validi. Ogni anomalia → fix proposto (repair path /
rimuovi cruft / re-register) applicato solo con consenso, con backup. Exit code ≠ 0 se restano
anomalie. Eseguito automaticamente a fine install ("install = converge + verifica").

### B7 — Manifest d'installazione (stato esplicito, self-healing)
`<InstallDir>\install-manifest.json`: versione, slug, path venv, client registrati (path+chiave),
hook/plugin installati, timestamp. Install/update lo riconcilia con la realtà; uninstall e
doctor lo leggono invece di indovinare. Ogni scrittura su config di terzi viene annotata →
rimozione precisa e completa.

### B8 — Matrice compatibilità + test su fixture reali
Fixture in `tests/fixtures/clients/`: JSONC con commenti, BOM, `.claude.json` profondo/grande,
settings VS Code, TOML Codex con altri server, path MSIX. Test del motore B1 su tutte
(il caso del collega diventa un regression test permanente).

---

## Ordine consigliato
1. **B5** (bug distruttivi — subito)
2. **B2 + B3 + B4** (i tre problemi del log del collega)
3. **B1 + B7** (centralizzazione + manifest)
4. **B6 + B8** (doctor + fixture di regressione)
5. **A1** (link writes O(attivi))
6. **A2 + A3** (single-pass + pre-warm)
7. **A4 + A5 + A6** (token diet, coseno, identità)
8. **A7** (benchmark), **A8** (decisione owner)
