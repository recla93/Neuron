# Neuron "bomba" — Backlog Agile / SCRUM

**Stato:** Proposed
**Data:** 2026-07-07
**Owner / Product Owner:** recla93
**Base:** `docs/design/00-neuron-bomb-roadmap.md` + ADR 01–04

Backlog di implementazione della roadmap. Struttura: **Epic → Story → Task**, con acceptance
criteria, changes (file), dipendenze e stime. Ogni Epic corrisponde a una Fase/ADR.

Legenda stime (story points, scala Fibonacci): 1 = triviale · 2 = mezza giornata · 3 = 1 giorno
· 5 = 2 giorni · 8 = 3+ giorni. Priorità: P0 (bloccante) · P1 · P2.

---

## Strategia di branching (git flow)

Obiettivo: `master` resta **sempre** completo, verde in CI, deployabile. Il lavoro rischioso vive
su branch.

```
master            ← sempre verificato, CI verde, taggabile (release)
  └─ feat/neuron-bomb   ← branch d'integrazione della roadmap (long-lived)
       ├─ feat/adr-001-embedding     ← branch per story/epic, mergiate quando verdi
       ├─ feat/adr-002-consolidation
       ├─ feat/adr-003-stimulus
       └─ feat/adr-004-drift-sleep
```

Regole:
1. Ogni Epic si apre da `feat/neuron-bomb` con un branch `feat/adr-00X-...`.
2. Merge su `feat/neuron-bomb` **solo** con: test verdi (`pytest tests/`), `compileall src/` ok,
   CI verde. Merge su `master` solo a fine Fase, con tag.
3. Nessun commit diretto su `master` per il lavoro "bomba".
4. Il branch `feat/neuron-bomb` è già creato (parte da master HEAD).

Comandi tipici (da lanciare in locale / via Code):
```bash
git switch feat/neuron-bomb
git switch -c feat/adr-001-embedding        # apre lo story-branch
# ... lavoro + test ...
git switch feat/neuron-bomb && git merge --no-ff feat/adr-001-embedding
# a fine Fase 0, quando tutto verde:
git switch master && git merge --no-ff feat/neuron-bomb && git tag v4.1.0-alpha.0
```

---

## Definition of Ready (una story è pronta se…)
- Acceptance criteria chiari e testabili.
- File impattati identificati (sezione "Changes").
- Dipendenze risolte o esplicitate.
- Nessuna decisione di design aperta (rimanda all'ADR).

## Definition of Done (una story è chiusa se…)
- Codice + test unitari nuovi, **verdi** (`pytest tests/`).
- `compileall src/` ok, CI verde sullo story-branch.
- Nessuna regressione sui test esistenti.
- Doc aggiornata se cambia comportamento utente (`docs/DEVELOPER.md`).
- Retrocompatibilità: DB/seed esistenti caricano senza crash (degradazione graziosa).

---

## Epics (overview)

| Epic | Titolo | ADR | Fase | Priorità | Punti |
|------|--------|-----|------|----------|-------|
| **E0** | Fondazione embedding (configurabile + multilingua) | 01 | 0 | P0 | 16 |
| **E1** | Fallback cheap + consolidation | 02 | 1 | P1 | 18 |
| **E2** | Motore di stimolo (spreading activation) | 03 | 2 | P1 | 21 |
| **E3** | Drift cross-contesto + sleep-mode | 04 | 3 | P2 | 16 |
| **EX** | Dev-infra trasversale (branch, CI gate, benchmark) | — | 0 | P0 | 6 |

Ordine di sprint (compounding): **EX+E0 → E1 → E2 → E3.**

| Sprint | Contenuto | Obiettivo dimostrabile |
|--------|-----------|------------------------|
| **S1** | EX + E0 | Modello scambiabile via env, re-embed funzionante, benchmark EN/IT che decide A vs A2 |
| **S2** | E1 | Fallback senza ri-embedding runtime; `consolidate` con `_graveyard`; ricerca ~costante |
| **S3** | E2 | Stimolo unico (Hebbian+salienza+spreading) su ogni tool response, cappato in token |
| **S4** | E3 | Drift cross-contesto + consolidation sleep-mode con pre-staging |

---

## EPIC EX — Dev-infra trasversale (P0, 6 pt)

Prerequisiti che sbloccano il resto e proteggono `master`.

### EX.1 — Branch d'integrazione + guardrail CI (P0, 2)
**Story:** Come owner, voglio un branch d'integrazione e un gate CI, così che `master` resti
sempre verde e ogni merge sia verificato.
**Acceptance:**
- Esiste `feat/neuron-bomb` (fatto).
- CI (`.github/workflows/ci.yml`) gira su push/PR dei branch `feat/**` (già `on: [push, pull_request]`).
- Un merge non entra se `pytest` o build falliscono.
**Changes:** eventuale branch-protection lato GitHub (manuale).
**Dipendenze:** nessuna.

### EX.2 — Harness di benchmark embedding EN/IT (P0, 3)
**Story:** Come owner, voglio misurare recall cross-lingua, RAM e ms/embedding, così da decidere il
modello (Opzione A vs A2) coi numeri, non a naso.
**Acceptance:**
- `scripts/bench_embed.py`: dato un set di coppie EN/IT (query→doc atteso), stampa recall@k,
  tempo medio per embedding, RSS del processo, per un dato `NS_EMBED_MODEL`.
- Confronta ≥2 modelli in un run e produce una tabella.
**Changes:** `scripts/bench_embed.py` (nuovo), `tests/fixtures/bench_pairs_en_it.jsonl` (nuovo).
**Dipendenze:** utile insieme a E0.1 (usa lo stesso hook di config).

### EX.3 — Baseline test suite verde su branch (P0, 1)
**Story:** Come dev, voglio la suite verde di partenza sul branch, per distinguere regressioni da
problemi preesistenti.
**Acceptance:** `pytest tests/ -v` verde su `feat/neuron-bomb` prima di iniziare E0.
**Dipendenze:** EX.1.

---

## EPIC E0 — Fondazione embedding (ADR-001, P0, 16 pt)

### E0.1 — Modello configurabile via env (P0, 5)
**Story:** Come dev, voglio scegliere il modello di embedding via `NS_EMBED_MODEL`, così da
scambiarlo senza toccare il codice.
**Acceptance:**
- `_get_embedder()` legge `NS_EMBED_MODEL` (default = modello multilingua 384-dim).
- `VECTOR_DIM` è **derivato** dal modello (non più costante hardcoded) e propagato a `models.py`.
- Se il modello ha dim ≠ 384, il codice non assume 384 da nessuna parte.
**Changes:** `src/neuron/server.py:749` (`_get_embedder`, `_get_embedding`),
`src/neuron/models.py` (`VECTOR_DIM` → derivato/iniettato), `pyproject.toml` (dep invariata).
**Dipendenze:** nessuna (apre l'Epic).

### E0.2 — Coerenza modello ↔ store (meta + guard all'avvio) (P0, 3)
**Story:** Come utente, voglio che Neuron rifiuti/segnali uno store embeddato con un modello diverso
da quello attivo, così da non mischiare spazi vettoriali incompatibili.
**Acceptance:**
- Il nome del modello è scritto nei `meta` del DB al primo save.
- All'avvio/caricamento, se `meta.embed_model != NS_EMBED_MODEL`, warning esplicito e i vettori
  vengono ignorati/ricalcolati invece di essere confrontati (nessun coseno tra spazi diversi).
**Changes:** `src/neuron/models.py` (save/load meta), `src/neuron/registry.py` (check al load).
**Dipendenze:** E0.1.

### E0.3 — Script di re-embed (P0, 3)
**Story:** Come dev, voglio rigenerare tutti i `node_vectors` quando cambio modello, così da
migrare lo store in modo pulito.
**Acceptance:**
- `scripts/reembed.py [--context X] [--all]` ricalcola i vettori per i contesti indicati (+ seed),
  aggiorna `meta.embed_model`, è idempotente e mostra un report (nodi ri-embeddati).
- Degrada con messaggio se `fastembed` assente (non crasha).
**Changes:** `scripts/reembed.py` (nuovo).
**Dipendenze:** E0.1, E0.2.

### E0.4 — Default multilingua + decisione A vs A2 (P0, 3)
**Story:** Come owner, voglio fissare il modello di default sulla base del benchmark, così da avere
la scelta migliore per EN+IT documentata.
**Acceptance:**
- Eseguito `bench_embed.py` (EX.2) su A (multilingua) e A2 (pivot LLM keyword EN).
- Default scelto e scritto in ADR-001 (aggiornato a "Accepted") con i numeri.
- `docs/DEVELOPER.md`: nuova sezione "Embedding model" (env, re-embed, cambio modello).
**Changes:** `docs/design/01-embedding-model.md` (stato→Accepted), `docs/DEVELOPER.md`.
**Dipendenze:** E0.1, E0.3, EX.2.

---

## EPIC E1 — Fallback cheap + consolidation (ADR-002, P1, 18 pt)

### E1.1 — Vettori sempre persistiti; via il ri-embedding a runtime (P1, 5)
**Story:** Come dev, voglio che il fallback non ri-embeddi mai a runtime, così che la ricerca sul
tier sqlite resti cheap.
**Acceptance:**
- Ogni nodo ha il vettore persistito (`add_node`→`_dirty_vectors`→`save_sqlite`) e ricaricato
  (`load_sqlite`).
- In `_search_embeddings` (`server.py:827`) il ramo `else _get_embedding(nd.keyword)` scatta solo
  come *ultima risorsa* e, quando scatta, **persiste** il vettore invece di ricalcolarlo ogni volta.
- Test: due ricerche consecutive sullo stesso grafo non ricalcolano embeddings (contatore mock).
**Changes:** `src/neuron/server.py` (`_search_embeddings`), `src/neuron/models.py` (load vettori).
**Dipendenze:** E0 (dim/model coerenti).

### E1.2 — `consolidate()`: merge near-duplicati con `_graveyard` (P1, 5)
**Story:** Come utente, voglio che i concetti quasi-duplicati vengano fusi, così che il grafo non
si gonfi.
**Acceptance:**
- `consolidate(merge=True)`: coppie con `cos > 0.85` fuse (somma salienza, unione link, tiene il
  nome più corto); riga originale archiviata in `_graveyard` (recuperabile).
- **Non** fonde nodi ad alta salienza anche se matchano (salience-aware; soglia da E2.2).
- Idempotente e safe sotto save incrementale (T11: no wipe cieco).
**Changes:** `src/neuron/models.py` (`consolidate`, tabella `_graveyard`), `src/neuron/registry.py`.
**Dipendenze:** E0 (soglia `cos>0.85` affidabile solo con buon embedding); soft-dep E2.2 per il gate salienza.

### E1.3 — `consolidate()`: drop orfani + archiviazione (P1, 3)
**Story:** Come utente, voglio che i nodi morti vengano archiviati, così da tenere il grafo fresco.
**Acceptance:**
- Nodi con `salience < 2` e nessun link attivo da 10+ turni → spostati in `_graveyard`.
- Recuperabili; nessun hard-delete.
**Changes:** `src/neuron/models.py`.
**Dipendenze:** E1.2 (`_graveyard`).

### E1.4 — Comando `neuron consolidate` (+ `--auto`) (P1, 3)
**Story:** Come utente, voglio lanciare la consolidation a mano o farla girare ogni ~20 turni.
**Acceptance:**
- Tool/CLI `consolidate` con report; toggle `--auto` (gira se `turn_count % 20 == 0`), configurabile.
- `--auto` off di default.
**Changes:** `src/neuron/server.py` (tool + help), `src/neuron/__main__.py` (CLI).
**Dipendenze:** E1.2, E1.3.

### E1.5 — Doc soglia ANN (quando introdurlo) (P2, 2)
**Story:** Come dev, voglio sapere quando passare a un indice ANN, così da non over-ingegnerizzare ora.
**Acceptance:** sezione in `docs/DEVELOPER.md`: scan O(N) ok sotto ~N nodi/contesto; oltre → DiskANN/`libsql_vector_idx`.
**Changes:** `docs/DEVELOPER.md`.
**Dipendenze:** nessuna.

---

## EPIC E2 — Motore di stimolo (ADR-003, P1, 21 pt)

### E2.1 — Pesi Hebbian sui link (P1, 5)
**Story:** Come sistema, voglio che i link co-attivati si rinforzino, così che le associazioni usate
diventino forti.
**Acceptance:**
- Nuova colonna `co_activation_count` sui link; su co-occorrenza A&B nello stesso turno: +1 con
  **cooldown ≥2 turni**.
- Upgrade peso `tangential→medium` a 3, `medium→strong` a 8 (riusa il path atomico monotòno T11).
- Migrazione schema idempotente; DB legacy senza colonna → default 0.
**Changes:** `src/neuron/models.py` (schema link + logica), `src/neuron/server.py` (store_turn/auto co-activation).
**Dipendenze:** nessuna (ma va prima di E2.3).

### E2.2 — Ranking composito salience-aware in `get_context` (P1, 3)
**Story:** Come modello, voglio che il recupero privilegi ciò che conta, non solo ciò che matcha.
**Acceptance:**
- Ordinamento risultati: `score = cos_sim*0.5 + (salience/MAX)*0.3 + (1/inactive_turns)*0.2` (pesi in costante).
- Un nodo ad alta salienza ma match più lasco risale sopra un match stretto ma poco saliente.
**Changes:** `src/neuron/server.py` (`_build_context_window`/`get_context` ordering).
**Dipendenze:** nessuna. Abilita il gate salienza di E1.2.

### E2.3 — Spreading activation (P1, 8)
**Story:** Come sistema, voglio propagare attivazione lungo il grafo, così da far emergere
associazioni non-dirette ma sensate.
**Acceptance:**
- `spreading_activation(seed_keywords, k≤2-3, weights)`: attivazione = forza-link (Hebbian) ×
  salienza × decadimento/hop; ritorna top-M nodi.
- Costo limitato a k-hop e top-M (non dipende dalla dimensione del grafo → costo token costante).
- Test: nodo a 2 hop ad alta attivazione emerge anche senza match vettoriale diretto.
**Changes:** `src/neuron/server.py` / `src/neuron/models.py` (traversal).
**Dipendenze:** E2.1 (pesi), E2.2 (salienza).

### E2.4 — Unificare i 3 flash sotto il motore (P1, 3)
**Story:** Come dev, voglio un solo motore invece di 3 euristiche scollegate, per stimoli coerenti.
**Acceptance:**
- Dormant pulse / cross-domain spark / creative leap diventano viste del motore di attivazione;
  selezione **top-1/top-2** per compattezza (non un dump).
- Nessuna regressione sui test flash esistenti (`TestSemanticFlashes`).
**Changes:** `src/neuron/server.py` (`_build_context_window`).
**Dipendenze:** E2.3.

### E2.5 — Piggyback stimolo + token budget (P1, 2)
**Story:** Come modello, voglio ricevere uno stimolo compatto su ogni tool response, così da essere
stimolato di continuo (MCP non fa push).
**Acceptance:**
- `store_turn`/`auto` (e simili) includono 1 riga-stimolo (il top del motore), **cap ~40 token**.
- Budget rispettato e misurato in test (lunghezza max).
**Changes:** `src/neuron/server.py` (return handlers store_turn/auto).
**Dipendenze:** E2.3, E2.4.

### E2.6 — Sezione "Token budget & accounting" in ADR-003 (P2, 1)
**Story:** Come owner, voglio il conteggio token documentato accanto alla decisione.
**Acceptance:** tabella compute-vs-token + cap, aggiunta a `docs/design/03-stimulus-engine.md`.
**Changes:** `docs/design/03-stimulus-engine.md`.
**Dipendenze:** nessuna.

---

## EPIC E3 — Drift cross-contesto + sleep-mode (ADR-004, P2, 16 pt)

### E3.1 — Drift link cross-contesto (P2, 5)
**Story:** Come sistema, voglio formare associazioni implicite tra contesti visitati, così da
scoprire ponti cross-dominio senza rationale esplicito.
**Acceptance:**
- Su co-occorrenza (o entro N turni via flash) di A@ctxX e B@ctxY: drift link `tangential` con
  **cooldown 5 turni**, **solo tra contesti visitati**, prune a **3 turni** inattivi.
- Non forma drift verso contesti mai aperti.
**Changes:** `src/neuron/models.py` (link cross-ctx + regole), `src/neuron/server.py`, `src/neuron/registry.py`.
**Dipendenze:** E2.1 (pesi Hebbian riusati).

### E3.2 — Drift in `get_context(depth≥3)` (P2, 3)
**Story:** Come modello, voglio che i drift affiorino solo su richiesta profonda, per non pagarli sempre.
**Acceptance:** i drift link compaiono in `get_context` solo con `depth≥3` (spesa opt-in, ~+15 token quando attivo).
**Changes:** `src/neuron/server.py` (`get_context` depth handling).
**Dipendenze:** E3.1.

### E3.3 — Trigger sleep-mode (scheduler / avvio-se-inattivo) (P2, 5)
**Story:** Come utente, voglio che il grafo si consolidi mentre sono via, per ritrovarlo pulito.
**Acceptance:**
- Traccia `last_active_timestamp` nei `meta`; dopo soglia inattività (es. 30 min o tra sessioni)
  esegue `consolidate` (E1) in background.
- Degrada a "consolidation all'avvio se inattivo da > soglia" se lo scheduler non è disponibile.
**Changes:** `src/neuron/server.py` (startup hook), integrazione scheduler.
**Dipendenze:** E1 (consolidate).

### E3.4 — Pre-staging degli stimoli (P2, 3)
**Story:** Come modello, voglio ricevere stimoli già "caldi" al primo turno di sessione, aggirando
il no-push di MCP.
**Acceptance:**
- Lo sleep-mode precalcola i top stimoli (via E2.3) e li salva nei `meta`.
- `pre_turn` restituisce lo stimolo pre-caricato se presente e **fresco**; lo invalida se stale.
**Changes:** `src/neuron/server.py` (`pre_turn`, meta), `src/neuron/models.py`.
**Dipendenze:** E2.3, E3.3.

---

## Grafo delle dipendenze (ordine di implementazione)

```
EX.1 ─┬─ EX.3 ──────────────────────────────────────────────► (baseline verde)
      └─ EX.2 (benchmark) ───────────────┐
                                          ▼
E0.1 ─► E0.2 ─► E0.3 ─► E0.4 (decide A/A2, ADR-001 Accepted)
                          │
                          ▼
E1.1 ─► E1.2 ─► E1.3 ─► E1.4      (E1.5 indipendente)
          ▲
          └── soft-dep ── E2.2 (gate salienza per il merge)
                          │
E2.1 ─┬─► E2.3 ─► E2.4 ─► E2.5     (E2.6 indipendente)
E2.2 ─┘
                          │
                          ▼
E3.1 ─► E3.2
E1(consolidate) ─► E3.3 ─► E3.4 ◄── E2.3
```

Regole d'oro:
- **E0 prima di tutto**: la soglia `cos>0.85` (E1.2) e lo scoring (E2.2/E2.3) mentono con embedding
  deboli.
- **E2.1 + E2.2 prima di E2.3**: lo spreading activation usa pesi Hebbian e salienza.
- **E1 prima di E3.3**: lo sleep-mode invoca `consolidate`.
- **E2.3 prima di E3.4**: il pre-staging usa il motore di attivazione.

---

## Changes per file (mappa d'impatto)

| File | Epic/Story | Tipo |
|------|------------|------|
| `src/neuron/server.py` | E0.1, E1.1, E1.4, E2.1-2.5, E3.1-3.4 | modifica (cuore) |
| `src/neuron/models.py` | E0.1-0.3, E1.1-1.3, E2.1, E2.3, E3.1, E3.4 | modifica (schema+logica) |
| `src/neuron/registry.py` | E0.2, E1.2, E3.1 | modifica (load/seed/consolidate) |
| `src/neuron/__main__.py` | E1.4 | modifica (CLI consolidate) |
| `scripts/reembed.py` | E0.3 | nuovo |
| `scripts/bench_embed.py` | EX.2 | nuovo |
| `tests/fixtures/bench_pairs_en_it.jsonl` | EX.2 | nuovo |
| `tests/test_core.py` | E1.*, E2.*, E3.* | nuovi test |
| `docs/DEVELOPER.md` | E0.4, E1.5 | doc |
| `docs/design/01..03` | E0.4, E2.6 | doc (ADR→Accepted / accounting) |
| `pyproject.toml` | E0.1 | dep invariata (verifica) |
| `.github/workflows/ci.yml` | EX.1 | gate (già `on: push/pr`) |

Migrazioni schema (idempotenti, retrocompatibili — pattern T11/T12):
- `links.co_activation_count` (E2.1)
- tabella `_graveyard` (E1.2)
- `meta.embed_model`, `meta.last_active_timestamp`, `meta.staged_stimulus` (E0.2, E3.3, E3.4)
- drift link = riga `links` con marcatura cross-context (E3.1)

---

## Rischi & mitigazioni

| Rischio | Epic | Mitigazione |
|---------|------|-------------|
| Re-embed rompe store esistenti | E0.3 | script idempotente + `meta.embed_model` + guard al load (E0.2) |
| Merge fonde concetti distinti | E1.2 | soglia alta (0.85) + gate salienza + `_graveyard` recuperabile |
| Spreading activation troppo rumoroso | E2.3 | k≤2-3 hop, top-M, soglia minima, cooldown Hebbian |
| Stimolo gonfia i token | E2.5 | cap ~40 token, top-1, test sulla lunghezza |
| Drift link riempiono il grafo di rumore | E3.1 | solo contesti visitati, cooldown 5, prune a 3 turni |
| Scheduler assente | E3.3 | fallback a consolidation all'avvio-se-inattivo |
| Git mount instabile (troncamenti) | tutti | lavorare in locale/Code; `git diff` prima di ogni commit |

---

## Riepilogo velocità

Totale ~77 punti. Con 4 sprint (uno per Epic + EX in S1), obiettivo ~1 Epic/sprint. Le story P0 di
E0 e la baseline (EX) sono il percorso critico: senza fondazione embedding, E1/E2/E3 amplificano
errori invece di valore.
