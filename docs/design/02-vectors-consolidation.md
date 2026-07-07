# ADR-002: Fallback cheap e crescita costante — vettori persistiti + consolidation

**Stato:** Proposed
**Data:** 2026-07-07
**Deciders:** recla93 (owner)
**Fase roadmap:** 1

## Contesto

Il "consumo che cresce pian piano" non dipende dal modello di embedding ma dal **numero di nodi**
e da come il motore di ricerca scala. Due criticità concrete nel codice attuale:

1. **Ricerca O(N) senza indice ANN.** `_search_embeddings` (`server.py:783`): sul tier Turso usa
   `vector_distance_cos` (SQL, ma comunque scan `ORDER BY sim LIMIT`), sul tier sqlite fa un
   **loop cosine Python** su tutti i nodi. In entrambi i casi il costo cresce linearmente col grafo.
2. **Ri-embedding a runtime nel fallback.** Sempre in `_search_embeddings` (`server.py:827`):
   `v = nd.vector if nd.vector is not None else _get_embedding(nd.keyword)`. Se un nodo non ha il
   vettore caricato in memoria, viene **ri-embeddato a ogni ricerca** — costoso e inutile.
3. **Rumore accumulato.** `neuron_prune` rimuove solo i link tangenziali scaduti; non tocca
   near-duplicati né nodi orfani. Il grafo si "ingrassa" nel tempo → N cresce → ricerca rallenta.

`FutureIdeas.md` #3 (consolidation) e #4 (sleep-mode) puntano qui, ma vanno inquadrati come
**controllo della crescita**, non solo pulizia estetica.

## Decisione

1. **Vettori sempre persistiti e caricati** → il fallback non ri-embedda mai a runtime.
   Garantire che `add_node` calcoli e `save_sqlite` scriva il vettore (già così per i nodi nuovi:
   `models.py` `_dirty_vectors`), e che `load_sqlite` li ricarichi tutti; se manca, calcolarlo una
   volta e persisterlo, non ricalcolarlo a ogni query.
2. **Consolidation pass** (`neuron consolidate`, opzionalmente `--auto` ogni ~20 turni):
   merge near-duplicati (`cos > 0.85`, somma salienza, unione link, tiene il nome più corto),
   drop orfani (salienza < 2, nessun link attivo da 10+ turni) con **archiviazione** in `_graveyard`
   (recuperabile, non hard-delete), prune dei link scaduti.
3. **Tenere N piccolo per contesto** sfruttando lo scoping già esistente: la ricerca lavora dentro
   un contesto, non sull'intero store → N effettivo molto minore.
4. **Indice ANN rimandato** (DiskANN / `libsql_vector_idx` su libSQL): si introduce solo quando i
   nodi per contesto superano ~decine di migliaia. Prima è over-engineering.

## Opzioni considerate

### Opzione A — Vettori persistiti + consolidation (scelta)
| Dimensione | Valutazione |
|-----------|-------------|
| Complessità | Media (un pass + archiviazione) |
| Costo | O(N) una tantum per pass, non per query |
| Scalabilità | N tenuto basso → ricerca ~costante nel tempo |

**Pro:** risolve sia lo spreco di ri-embedding sia la crescita; recuperabile via `_graveyard`.
**Contro:** merge sbagliati possibili se l'embedding è debole → dipende da ADR-001 (fatto prima).

### Opzione B — Indice ANN subito
**Pro:** ricerca sub-lineare. **Contro:** complessità e dipendenza dal supporto ANN del tier;
inutile ai volumi attuali; non risolve il rumore (near-duplicati restano). Prematuro.

### Opzione 0 — Status quo
**Pro:** zero lavoro. **Contro:** ricerca che degrada linearmente + ri-embedding a runtime +
grafo che si ingrassa. È esattamente il problema segnalato.

## Analisi dei trade-off

Il merge near-duplicati (`cos > 0.85`) è potente ma **rischioso con embedding deboli**: per questo
ADR-002 viene **dopo** ADR-001. La consolidation dà il massimo se combinata con la salienza (vedi
ADR-003, #5): non fondere/eliminare ciò che ha alta salienza anche se matcha — "preserva ciò che
conta". L'archiviazione in `_graveyard` rende ogni pass reversibile, abbassando il rischio.

## Conseguenze

- **Più facile:** ricerca veloce e prevedibile; fallback sqlite utilizzabile a lungo termine;
  grafo che resta "fresco".
- **Più difficile / da gestire:** una tabella `_graveyard` e una policy di retention; il pass va
  reso idempotente e sicuro sotto scrittori concorrenti (T11: upsert per-delta, niente wipe cieco).
- **Da rivedere:** soglie (`0.85`, salienza<2, 10 turni) da tarare sui dati reali; trigger `--auto`
  (ogni N turni vs sleep-mode di ADR-004).

## Action items

1. [ ] Garantire persistenza+load di tutti i vettori; rimuovere il ri-embedding a runtime dal
       fallback (`server.py:827`) — se manca, calcola una volta e persisti.
2. [ ] Implementare `consolidate(merge, prune, archive)` in `models.py`/`registry.py` con
       `_graveyard` recuperabile.
3. [ ] Esporre `neuron consolidate` (manuale, con report) e il toggle `--auto`.
4. [ ] Rendere la consolidation salience-aware (aggancio ad ADR-003 #5).
5. [ ] Test: merge sopra soglia, no-merge di nodi ad alta salienza, ripristino da `_graveyard`,
       idempotenza sotto save incrementale.
6. [ ] Documentare la soglia ANN (quando introdurlo) in `docs/DEVELOPER.md`.
