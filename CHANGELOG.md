# Changelog — Neuron

## 6.4.3 (2026-08-19)
- **La riga del trail si salva quando nasce, non al checkpoint successivo.**
  `store_turn` chiude con `_g.save(ctx)`, ma `save(ctx)` non toccava il trail:
  `_save_cross_links()` viveva solo dentro `save_all()`. Fra uno switch e il
  checkpoint successivo la riga stava solo in memoria, quindi una morte sporca
  del processo lasciava i turni salvati in due grafi e perso il collegamento fra
  le due meta' della sessione -- esattamente il guasto per cui il trail esiste.
  Misurato su una copia dei grafi veri: switch ai->veicoli senza save_all,
  `_cross_links.json` fermo a `[]`. Ora `add_cross_link` scrive appena la riga
  esiste; la dedup che sta sopra fa uscire in anticipo i passaggi gia' noti,
  quindi si scrive solo a un cambio di contesto mai visto.
- **Una nota su COME non decide piu' se c'e' un COSA.** `(vector fallback)` e
  `(from:...)` dicono per quale strada si e' risposto, ma finivano nella stessa
  lista dei concetti, e la riga finale e' `" | ".join(parts) if parts else "no
  context"`: bastava una delle due perche' `parts` non fosse vuota e l'ammissione
  onesta non uscisse mai. Misurato su un grafo vuoto: `_resolve_context` torna
  `fallback=True` con zero nodi e zero link, e pre_turn rispondeva
  "(vector fallback)" invece di "no context". Corretto in tutti e due i posti
  dove la lista viene costruita.

## 6.4.2 (2026-08-05)
- **L'handshake non e' mai partito sulle installazioni col layout nuovo.** Il
  SessionStart hook rispecchia `gray_matter.gme.gme_root()` senza importarlo,
  e il mirror era rimasto al layout PIATTO di prima che il registro scendesse
  in `registry/`: `installed_slugs()` globbava una cartella di sole
  sottocartelle, tornava vuoto, `owner()` dava None e l'hook usciva muto.
  Verificato su una macchina reale: 0 caratteri di handshake con i tre tool
  installati e registrati. Ogni altro test passava `installed` a mano, quindi
  `installed_slugs()` non era mai stato confrontato con un registro vero.
- **Il tool `auto` sollevava NameError a ogni chiamata.** Il blocco che emette
  `episode_lost` stava nel return di `_tool_auto`, ma `_episode_report` e' un
  locale di `_tool_store_turn`: l'espressione veniva valutata sempre. Nessun
  test chiamava `auto`. L'emissione ora vive dove il report esiste, cosi'
  `store_turn` dichiara davvero troncamenti e sfratti come promette lo schema.
- **Le entry SessionStart che puntano a un interprete sparito vengono
  riscritte.** Dopo lo spostamento alla radice GME il venv ha cambiato posto,
  ma il comando registrato no, ed entrambi i deployer vedevano "c'e' gia'" e
  non aggiornavano: lo stesso "gia' presente = non toccare" di `claude mcp add`.
- **L'installer chiede al codice, non alla targa.** A parita' di versione pip
  risponde "already satisfied" e non copia niente: un fix spedito senza bump
  non arrivava a chi reinstallava, e un install andato a meta' lascia il
  dist-info nuovo sui file vecchi (misurato: 64 file diversi a versione
  identica). Deriva rilevata, refresh forzato.
- **Link cross-context.** Nascevano da `add_link`: o self-link scartati in
  silenzio, o edge intra-graph verso un nodo di un altro context. Ora sono
  drift link, che `drift_links()` riporta nelle query profonde.
- **La suite non scrive piu' nella memoria reale.** `NS_GRAPHS_DIR` e' risolto
  una volta sola nel registry, all'import di `neuron.server`: il fixture
  arrivava sempre dopo, e una passata di test spostava lo store dell'utente.

## 6.4.1 (2026-08-05)
- **Il tier SQL vettoriale non ha mai girato.** La query usava `f32blob(...)`,
  funzione che nessun engine libSQL/pyturso espone: ogni chiamata sollevava
  "no such function", l'`except` la inghiottiva con un `log.debug` e tutto
  finiva nel fallback Python. `vector_distance_cos` accetta già il blob, il
  wrapper era di troppo — in `_search_embeddings` e in `_refine_domain`.
- **Conseguenza vera, oltre alla latenza: il seed non veniva mai cercato.** Il
  fallback Python itera solo `graph.nodes`, cioè il grafo in memoria; il seed
  `base_knowledge.db` è consultato *solo* dal tier SQL. Con il tier morto, le
  139 keyword del seed erano peso morto e `_refine_domain` ripiegava sui grafi
  caricati invece che sulla tassonomia dei domini del seed.
- **`_vector_sql_ok`**: latch di processo su "no such function". È una
  incapacità permanente (sqlite3 puro, o il guard L2 che degrada un handle
  pyturso bloccato), e ritentarla ogni volta costava la query fallita *più* la
  riapertura del seed che `_drop_seed_connection` forzava al giro dopo — 2.8 MB
  riaperti per un errore che si sarebbe ripresentato identico. Gli errori
  transitori (lock, handle corrotto) mantengono il drop-and-retry di prima.
- **Misure** (5 ricerche/turno = 4 `_auto_link` + 1 `_context_window`), grafo
  al cap di 500 nodi: 124 ms → 27 ms per turno, e il costo diventa piatto
  rispetto alla dimensione del grafo. Con il seed conteso da un altro processo
  (server MCP vivo + una seconda sessione) il percorso rotto costava ~1.5 s per
  turno: il retry-loop del guard L2 su ogni riapertura.

## 6.4.0 (2026-08-03)
- **Quattro modalità di retrieval, zero tool nuovi.** La modalità è *stato*,
  non superficie: `semantic` (default, invariato), `focus` (pesa i nodi vicini
  al compito attivo), `brainstorm` (penalizza la somiglianza con la query, così
  emergono i nodi lontani), `pattern` (suggerisce il prossimo passo da sequenze
  ricorrenti). La strategia si applica in `_resolve_context` **prima** del sort
  e arriva come parametro: standalone la passa il chiamante, con Gray Matter la
  inietta il proxy dal blackboard (`cervello/mode`, `cervello/focus`). Neuron
  non legge mai il DB di GM.
- **`modes.py`**: strategie pure con self-check eseguibile. Il materiale di
  `pattern` è il log append-only `turns.jsonl`, non il grafo: il grafo non
  preserva lo storico dei turni, e `nd.turn` è il turno di *creazione* del
  nodo, non l'ultimo tocco — indicizzarci una cache sarebbe stato sbagliato.
- **`pre_turn` non consegna più i fatti di un nodo solo.** Prima
  `recent_episodes(nodes_pt[0][0], 2)`: gli episodi del primo in classifica,
  con entrambi i numeri costanti letterali. Conseguenza, ogni concetto in più
  era un concorrente in più per l'unico posto, e alzare `max_tokens` non
  aumentava i fatti. Ora i fatti vengono dai primi `fact_nodes` nodi
  (default **3**, parametro dichiarato), ognuno attribuito al suo nodo;
  `fact_nodes=1` riproduce il comportamento precedente. `files:` aveva lo
  stesso taglio a rango 1 ed è allineato.
- **Allineamento versione**: `__version__` era rimasto a `6.2.0` mentre
  `pyproject.toml` diceva `6.3.0` — la 6.3.0 è uscita con il valore sbagliato.
- **Una release non esce più con i test rossi.** Il tag andava dritto alla
  compilazione delle cinque wheel pyturso e alla pubblicazione: `ci.yml` girava
  su ogni push ma niente collegava le due cose. Ora `release.yml` ha un job
  `test` da cui il resto dipende.
- **Il numero di versione è controllato, non ripetuto a mano** — `pyproject`,
  `__version__`, il badge del README e la testa del CHANGELOG devono
  concordare. Avrebbe preso da solo il `__version__` fermo a `6.2.0` che la
  6.3.0 ha spedito dentro la wheel.
- **La scrittura dichiara cosa ha perso.** `add_episode` troncava a
  `EPISODE_MAX_CHARS` e sfrattava i più vecchi oltre `EPISODES_PER_NODE` in
  silenzio, e `store_turn` non guardava nemmeno il valore di ritorno. Ora la
  risposta porta `episode_lost` con i caratteri persi e i turni sfrattati, e i
  due env var che alzano i limiti sono documentati.
- **`pre_turn` dichiara attraverso cosa risponde** (`db=turso-local`,
  `db=sqlite!degraded`). Il degrado L2 cambia il motore che serve la chiamata e
  si annunciava solo su stderr, dove nessun chiamante guarda.
- **Il handshake parla una volta per sessione**, chiunque lo registri: il
  plugin Cowork e l'installer lo caricano da percorsi diversi, quindi girava
  due volte. `claim(session_id)` con `O_EXCL`; fail-open, perché un handshake
  mai detto costa più di uno detto due volte.
- **Via la modalità `brainstorm`**: era un no-op. Toglieva `0.3*sim` mentre la
  similarità pesa `0.5`, e ri-ordinava per anti-rilevanza un pool già filtrato
  per rilevanza. I candidati inattesi richiedono il grafo intero più i chunk:
  è `gray_matter_brainstorm`, e sta in GM. Restano `semantic`, `focus`,
  `pattern`.
- Fix: la freccia unicode nel riepilogo di `reembed.py` crashava la console
  Windows cp1252.

## 6.3.0 (2026-08-02)
- **Seed knowledge rigenerato dall'intero ecosistema** (`base_knowledge.db`:
  27 → 1130 nodi, 1525 link). Il seed precedente (10/07) conteneva solo la
  documentazione interna dei tool e copriva una Neuron che non esiste più:
  mancavano `dismiss`, `recall`, `introspect`. Ora il seed è la mappa completa
  di Gray Matter Environment — architettura, flussi, ADR, bug noti e risoluzioni
  di gray_matter, neurag e Neuron stesso — costruito da
  `scripts/import_vault.py` sul workspace dev con i grafi graphify dei tre repo.
  Conseguenza pratica: un grafo nuovo fa warm-start dal seed e un client "sa" chi
  è l'ecosistema senza aver mai letto i README.
- **Embedding del seed allineati al runtime (ADR-001)**. `import_vault.py`
  generava i vettori con `all-MiniLM-L6-v2` (solo EN) mentre il server usa
  `paraphrase-multilingual-MiniLM-L12-v2` (EN+IT): il seed nasceva con vettori
  di un modello diverso, scartati e ricalcolati a ogni avvio (spazi vettoriali
  non confrontabili). Lo script ora legge `NS_EMBED_MODEL` con lo stesso default
  del runtime — una sola env governa la suite, come già fatto per NeuRAG.
- **`import_vault.py` ignora i contenitori tecnici** (`.venv`, `build`, `vendor`,
  `handoff`, caches): prima scannerizzava anche il `.venv` del repo, portando
  nel seed nodi da `site-packages` estranei.


- **Un archivio corrotto ora dice cosa fare** (`db.corrupt_store_hint`). Un
  `graph.db` malformato arrivava come `DatabaseError: file is not a database` in
  cima a un traceback: il sintomo, senza il file e senza un rimedio. È lo stesso
  vicolo cieco che NeuRAG ha chiuso nella 1.1.1 e che questo lato non ha mai
  ricevuto, pur essendo il gemello keep-in-sync di quel `db.py`.
  `corrupt_store_hint` riconosce le varie grafie della corruzione — quale ti
  tocca dipende dal tier che ha aperto il file (pyturso o sqlite3) e da quanto
  header è stato letto — e restituisce una frase con causa e recupero;
  `server.call_tool` la mette **prima** del traceback, che resta sotto perché è
  l'unica telemetria che attraversa worker → GM → client.
  Un hint e non una classe di eccezione: un `Graph` viene caricato e salvato da
  molti punti, e `call_tool` incanala già ogni errore in testo, quindi
  classificare al confine cambia un posto invece di trenta. Il classificatore è
  volutamente stretto: dire "archivio corrotto" a chi ha solo una cartella
  mancante lo manda a cancellare la cosa sbagliata.
  Verificato end-to-end su un file davvero rotto, non solo sulle stringhe
  (`tests/test_corrupt_store.py`).
  Nota: `save_sqlite` **alza** su un archivio corrotto — controllato apposta,
  perché un salvataggio che riporta successo senza scrivere sarebbe perdita di
  memoria silenziosa. Il `return` anticipato che si vede a mano è il guard
  "graph non dirty", non un errore ingoiato.
- **Un `;` dentro un commento SQL non può più troncare uno schema**
  (`db.py:_split_sql`). Il client remoto non ha `executescript`, quindi
  `RemoteTursoConnection` taglia lo script su `;` a mano: un punto e virgola
  dentro un `--` spezzava la statement che lo conteneva e il motore riceveva
  "incomplete input", con una tabella mancante in silenzio. Gli schemi di Neuron
  non hanno commenti SQL, quindi qui non era mai scattato — è scattato in NeuRAG,
  il cui `db.py` è la porta keep-in-sync di questo file, appena qualcuno ha
  commentato una colonna. Difetto latente, cioè in attesa di chi documenta una
  colonna: sistemato su entrambi i lati perché il prossimo che copia copi il fix.
  Test: `tests/test_sql_script_split.py`, che fa passare anche gli schemi veri
  di `models.py`/`engine.py` attraverso lo splitter vero.

## 6.1.2
- **GUI Tkinter ritirata**. Cancellato `src/neuron/gui.py` e le entry
  `neuron-gui` da `[project.scripts]`/`[project.gui-scripts]`: `neuron-gui.exe`
  non viene più generato. Il control center è UNO, la webgui condivisa di Gray
  Matter. Rimosso il fallback `from neuron.gui import main` in `__main__.py`.
- **`neuron gui` bootstrap reale**: se Gray Matter manca, lo installa nello
  stesso venv (cartella sorella in dev, poi indice pip) streamando il progresso,
  poi apre — niente più GUI separata né install muto.
- **`repair --json`**: elenca la superficie cancellabile (`--wipe-memory`) con
  path/stato, per il pannello Repair del control center.
- **Guard su `neuron register`**: se GM è presente e gestisce ancora Neuron (non è
  in `unmanaged`), il register DIRETTO si rifiuta (eviterebbe una doppia
  registrazione nei client) e indirizza a `neuron go-standalone` (register +
  release atomico) o `gray-matter deregister neuron`. Bypass: `--force`. Senza
  GM (standalone puro) nessun guard. `go-standalone` non passa dal guard.
- **Bootstrap GM — wheel d'emergenza OFFLINE**: `neuron gui` prova cartella
  sorella (dev) → **wheel GM vendorata nel package** (`neuron/_gm_vendor/*.whl`,
  install `--find-links` senza rete: GM ha solo `mcp` come dep, già presente) →
  indice pip → `git+https://github.com/recla93/gray-matter`. La wheel va
  ricostruita a ogni release di GM (vedi RELEASE-CHECKLIST).
- **Icona desktop "Neuron"** (launcher standalone): l'installer standalone la crea
  già a fine install (`neuron gui --shortcut-only`) e `neuron gui` la ri-assicura
  a ogni apertura. Logica in `neuron/shortcut.py` (copia tool-local cross-OS,
  keep-in-sync con `gray_matter/shortcut.py` — serve senza GM). L'icona punta a
  `neuron gui`, che bootstrappa GM al primo click. Idempotente (marker nel venv).

## 6.1.1
- **Fix flash CMD (Windows)**: `clients.py` (register/deregister via `claude` CLI,
  `_list_processes` PowerShell, `_default_killer` taskkill) e `bridge.py` (probe
  `mcp-proxy --version`) ora usano `CREATE_NO_WINDOW`. Il flag è nel runner di
  default, così i runner iniettati dai test non ricevono `creationflags` a forza.
- **Extra `[gui]`** = `gray-matter`: il control center è UNO (`gray_matter.webgui`);
  `neuron gui` lo bootstrappa se manca. Il runtime MCP resta indipendente da GM
  (import guardato) — verificato: Neuron importa e gira con gray_matter assente.

## 6.1.0
- **`neuron go-standalone`**: Neuron esce dal gateway GM — si registra come MCP
  diretto nei client col PROPRIO engine (`clients.register_all`) e chiede a GM
  (se presente) di non gestirlo più (`gray_matter.clients.release_tool`,
  persistente + IPC best-effort). L'entry `gray-matter` nei client resta finché
  un peer è ancora gestito da GM. Reversibile: `gray-matter register --gateway`.
- **Guardia autoregister**: il server NON si ri-registra al gateway se Neuron è
  in lista `unmanaged` (niente tool pubblicati due volte).
- **GUI universale**: `neuron gui` apre il control center condiviso
  (`gray_matter.webgui`) quando GM c'è; senza GM degrada alla Tkinter storica.
- **Repair puntuale**: `neuron repair` stampa (o lancia con `--reinstall`) il
  PROPRIO installer con `--force`, risolto dai path registrati
  (`paths.source_dir()`).
- **Installer `--force`**: `install.ps1 -Force` / `install.sh --force` —
  reinstall forzato del pacchetto Neuron anche a versione invariata (pattern di
  gray_matter, inoltrato anche al GM installer).

## 6.0.3
- **Path SSOT (Neuron possiede i suoi path)**: nuovo `neuron/paths.py` — fonte
  di verità delle location Neuron (`graphs_dir` delega a `config`, `data_dir`,
  `source_dir`). Gray Matter li SCOPRE via `neuron.paths` invece di hardcodarli.
- `neuron record-paths --source <dir>` + comando `repair` (reinstall pulito
  scope-Neuron): Neuron registra il proprio sorgente per repair/reinstall.
  Entrambi nascosti dal control center dove serve.

## 6.0.1
- Bump di release: la 6.0.0 installata prima del refactor `COMMANDS` in
  `__main__.py` esponeva 0 subcomandi al catalogo del control center (GUI con
  la sezione Neuron vuota). Nessun cambio di codice: serve solo a far
  reinstallare il pacchetto dall'installer, che salta le versioni identiche.

## Unreleased

### Fix da audit OpenCode (2026-07-21)
- `_env.py`: `.env` letto con `utf-8-sig` (BOM di PowerShell 5.1 corrompeva la
  prima chiave). Keep-in-sync con `gray_matter/_env.py` e `gray_matter/cloud.py`.
- `install.ps1`/`install.sh`: nel fallback PyPI, exit solo su successo di
  `gray-matter install` — un install del gateway fallito ora degrada a
  standalone (§6) invece di terminare (fix dell'audit su NeuRAG, specchiato).

### Installer — GM opt-out (consenso informato, DESIGN-CLOUD-MEMORY §6)
- `install.sh`/`install.ps1`: Gray Matter non è più forzato — prompt
  `Install Gray Matter (recommended)? [Y/n]` con il deficit esplicito (senza GM
  si perdono solo bridge cross-store e auto-surface dei vicini). Headless:
  `--no-gm` / `GM_OPTIN=0`. Rifiuto → install STANDALONE (venv proprio,
  `neuron register --client all`). GM non ottenibile (offline) → degrada a
  standalone invece di uscire. Reversibile ri-eseguendo senza `--no-gm`.

## v6.0.0 (2026-07-21)

Prima release pubblica dell'era gateway. Consolida il lavoro 5.5.x–5.6.0 (trust +
refs table, prune dry-run, gateway GM-only, installer unificato). Bump a major per
la prima distribuzione stabile e taggata; nessun cambiamento di comportamento
rispetto a 5.6.0.

## v5.6.0 (2026-07-20)

### Gateway flip
- `gray-matter register --gateway` evicts neuron/neurag from all clients, registers only GM
- Singleton daemon via exclusive bind on :9876 (`SO_EXCLUSIVEADDRUSE`)
- Stdio handshake fixed: `InitializationOptions` now includes capabilities + GM instructions
- GM serves 32 tools via pass-through with real schemas (F12)

### Trust system (B1-B3)
- `Node.trust: float` column (REAL DEFAULT 0) with atomic delta `MAX(0, trust + ?)`
- `confirm(confidence)` tool: boosts trust, propagated in merge/dedup
- Trust integrated into ranking weights

### Refs table (G2)
- New `refs` table (context, keyword, path, project_id, by) with natural PK
- `store_turn` canonical refs + merge on revisited nodes + `files:` line in `pre_turn`

### Project system (G3)
- `project.py`: `.neuron/project.json` marker, relative paths, provenance tracking

### Installer unification
- Canonical install via `install.ps1` / `install.sh` delegating to GM
- `uninstall.sh` simplified
- INSTALL-AI.md (EN) + INSTALL-AI.it.md (IT) added

### Fixes
- F3: `reset` requires `confirm=true` (v5.4.2)
- F4: `prune` now has `dry_run` support
- F5: `dedup` toggle with explicit enable option
- F10: POSIX bashisms fixed in installer scripts
- G1: File refs canonicalized in `store_turn`

### Cleanup
- Removed `MINIMAX-BRAINSTORM.md` and `install-gui.sh`

## v5.5.0 (2026-07-18)

- Optional GM autoregister (opt-out `NEURON_NO_GM`)
- Forgotten `near=` mid-band serendipity selector (flash v2)

## v5.4.2 (2026-07-18)

- Reset requires confirm + dedup explicit enable
- POSIX sh launcher + macOS pipx shortcut
