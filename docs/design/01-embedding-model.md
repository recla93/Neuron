# ADR-001: Modello di embedding configurabile e multilingua (EN + IT)

**Stato:** Proposed
**Data:** 2026-07-07
**Deciders:** recla93 (owner)
**Fase roadmap:** 0 (fondazione)

## Contesto

Il modello di embedding è hardcoded in `src/neuron/server.py:749`:
`TextEmbedding("sentence-transformers/all-MiniLM-L6-v2")` — 384-dim, a prevalenza inglese.
L'owner lavora **sia in inglese sia in italiano**: gli embedding italiani sono di qualità più
bassa, e questo degrada tutto ciò che ci sta sopra (ranking per salienza, merge near-duplicati a
`cos > 0.85`, cross-domain spark).

Vincolo tecnico decisivo: **vettori prodotti da modelli diversi non sono confrontabili.**
`node_vectors` è una sola tabella di vettori 384-dim e la ricerca fa `vector_distance_cos`. Se
alcuni nodi sono embeddati col modello inglese e altri con uno italiano, il coseno tra loro è
rumore. Quindi "modello giusto per la lingua" significa **partizionare lo spazio** oppure usare
un **unico modello che copra entrambe le lingue**.

Forze in gioco: qualità EN vs IT; recall cross-lingua (una query IT che pesca nodi EN); RAM e
latenza; costo di migrazione (re-embed); assenza di language-detection come dipendenza dura.

## Decisione

1. Rendere il modello **configurabile via env** (`NS_EMBED_MODEL`), con `VECTOR_DIM` derivato dal
   modello (non più costante hardcoded). Default → un modello **multilingua a 384-dim**
   (es. `paraphrase-multilingual-MiniLM-L12-v2` o `multilingual-e5-small`; nome esatto da
   `TextEmbedding.list_supported_models()`).
2. Fornire uno **script di re-embed** (`scripts/reembed.py`) che rigenera `node_vectors` per tutti
   i contesti quando il modello cambia, e scrive il nome del modello nei `meta` del DB per
   rilevare disallineamenti all'avvio.
3. Mantenere l'invariante: nessuna diramazione di comportamento per lingua nel codice.

## Opzioni considerate

### Opzione A — Un solo modello multilingua (scelta)
| Dimensione | Valutazione |
|-----------|-------------|
| Complessità | Bassa (una riga + env + script re-embed) |
| Costo | RAM +~0.4–0.5 GB fissi (vocabolario multilingua ~250k token); +~2x ms/embedding (trascurabile) |
| Scalabilità | Storage vettori invariato (384×4 = 1.5 KB/nodo, come oggi) |
| Recall cross-lingua | Nativo: "dog" ≈ "cane" nello stesso spazio |

**Pro:** uno spazio coerente, nessuno schema change (resta 384-dim), niente language-detection,
copre anche il path euristico. **Contro:** qualità di picco su EN pura leggermente inferiore allo
specialista; ~0.5 GB di RAM in più; re-embed una tantum.

### Opzione A2 — Pivot all'inglese via LLM (keyword EN)
| Dimensione | Valutazione |
|-----------|-------------|
| Complessità | Media (dipende dal prompt di estrazione) |
| Costo | Zero modelli nuovi: si riusa l'LLM già in uso; RAM minima (resta L6, ~90 MB) |
| Scalabilità | Come oggi |
| Recall cross-lingua | Sì, perché tutto è pivotato a EN |

**Pro:** il più leggero (tiene `all-MiniLM-L6-v2`), zero dipendenze nuove. **Contro:** copre bene
solo il path LLM; il fallback euristico (senza LLM) resta EN-centrico sugli input IT. Ottimo come
**complemento** ad A, non sostituto.

### Opzione B — Modello specialista per lingua (spazi partizionati)
| Dimensione | Valutazione |
|-----------|-------------|
| Complessità | Alta (colonna `model`/`lang`, filtro in ricerca, language-detection) |
| Costo | Detection come dipendenza dura; più indici da mantenere |
| Scalabilità | Silos per lingua |
| Recall cross-lingua | **Perso** (query IT non vede nodi EN) |

**Pro:** qualità massima sulla singola lingua. **Contro:** rompe il cross-lingua, aggiunge
machinery e una dipendenza di detection fragile su testi corti/misti. Sconsigliata per EN+IT.

### Opzione 0 — Status quo (hardcoded EN)
**Pro:** zero lavoro. **Contro:** italiano degradato, stimoli banali, modello non testabile.

## Analisi dei trade-off

Il vero costo del modello è **fisso e una tantum** (RAM +0.5 GB, re-embed), non un costo che
cresce nel tempo: lo storage dei vettori e la latenza di ricerca dipendono dal **numero di nodi**,
non dal modello (vedi ADR-002). Quindi pagare ~0.5 GB per coprire EN+IT con un solo spazio coerente
è un ottimo affare rispetto alla complessità dell'Opzione B. A2 non esclude A: si possono combinare
(multilingua di default + keyword normalizzate dall'LLM per alzare ancora la coerenza).

## Conseguenze

- **Più facile:** provare modelli diversi sui dati reali (A vs A2) senza toccare il codice;
  qualità italiana; qualità degli stimoli cross-dominio.
- **Più difficile / da gestire:** ogni cambio di modello richiede un re-embed completo; l'avvio
  deve rifiutare o rigenerare uno store embeddato con un modello diverso da `NS_EMBED_MODEL`.
- **Da rivedere:** se in futuro servono lingue molto distanti dove il multilingua crolla, si
  riapre l'Opzione B accettando i silos.

## Action items

1. [ ] Parametrizzare `_get_embedder()` su `NS_EMBED_MODEL` (default multilingua 384-dim).
2. [ ] Derivare `VECTOR_DIM` dal modello e propagarlo a `models.py` (oggi costante).
3. [ ] Scrivere il nome del modello nei `meta` del DB; check di coerenza all'avvio.
4. [ ] `scripts/reembed.py`: rigenera `node_vectors` per ogni contesto + eventuale seed.
5. [ ] Aggiornare CI (cache fastembed) e `docs/DEVELOPER.md` (sezione "Embedding model").
6. [ ] Benchmark EN/IT: recall cross-lingua, RAM, ms/embedding — A vs A2, decidere il default coi numeri.
