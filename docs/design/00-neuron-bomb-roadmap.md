# Neuron — Roadmap "bomba": da database taggato a memoria associativa

**Stato:** Proposed
**Data:** 2026-07-07
**Deciders:** recla93 (owner)
**Versione base:** 4.0.0

Questo documento è la visione d'insieme. Le decisioni puntuali vivono in ADR separati
(`01`–`04`) referenziati qui sotto. Nasce dalla review di codice e da `handoff/FutureIdeas.md`.

---

## 1. La tesi

Oggi Neuron funziona come un **database taggato con ricerca vettoriale**. L'obiettivo è
trasformarlo in una **memoria associativa** che si comporta più come un cervello: ricorda
ciò che conta, collega concetti senza che glielo si chieda, e "stimola" il modello con
associazioni pertinenti e inattese.

I tre problemi che l'owner ha identificato — **embedding**, **fallback**, **stimoli** — non
sono tre problemi separati: sono **un unico sistema a tre strati**. Trattarli isolati porta a
ottimizzazioni che non si sommano; trattarli come sistema li fa comporre.

---

## 2. Il modello mentale: tre strati sovrapposti

```
  ┌───────────────────────────────────────────────┐
  │  STRATO 3 — STIMOLI (output)                   │  flash: dormant pulse,
  │  ciò che Neuron "spinge" nella mente del modello│  cross-domain spark, creative leap
  └───────────────▲───────────────────────────────┘
                  │  è buono quanto lo strato sotto
  ┌───────────────┴───────────────────────────────┐
  │  STRATO 2 — MOTORE (search + fallback)         │  vector_distance_cos (Turso)
  │  attivazione/ricerca sul substrato             │  o cosine Python (sqlite)
  └───────────────▲───────────────────────────────┘
                  │  gira sopra
  ┌───────────────┴───────────────────────────────┐
  │  STRATO 1 — SUBSTRATO (embedding)              │  fastembed 384-dim,
  │  lo spazio semantico                            │  node_vectors per context
  └───────────────────────────────────────────────┘
```

**Invariante di progetto:** lo strato 3 non può essere migliore dello strato 1. Un
"cross-domain spark" del tipo *pizza dough ≈ structured concurrency* esiste solo se lo spazio
degli embedding coglie quella vicinanza. Perciò l'embedding è la **fondazione**, non un
dettaglio: si migliora per primo, anche se `FutureIdeas.md` non lo elencava.

---

## 3. Stato attuale (grounding sul codice, v4.0.0)

- **Embedding** — `src/neuron/server.py:749`: `TextEmbedding("sentence-transformers/all-MiniLM-L6-v2")`
  hardcoded, 384-dim (`models.py:VECTOR_DIM=384`), lazy-load. Modello a prevalenza inglese →
  qualità più bassa sull'italiano. Registrato a `models.py` via `register_embed_fn` (`server.py:916`).
- **Layer DB** — `src/neuron/db.py`: 3 tier (Turso cloud → pyturso locale → sqlite3 stdlib).
  `VECTOR_SQL_SUPPORTED = REMOTE_TURSO or LOCAL_TURSO_ENGINE`.
- **Motore/ricerca** — `_search_embeddings` (`server.py:783`): su Turso usa `vector_distance_cos`
  (SQL); su sqlite fa un **loop cosine Python O(N)**, e per i nodi senza vettore caricato
  **ri-embedda a ogni ricerca** (`server.py:827`). Nessun indice ANN: la ricerca è scan lineare
  su entrambi i path → costo che cresce col numero di nodi.
- **Stimoli** — `_build_context_window` (`server.py:~620-720`): tre flash euristici
  (dormant pulse, cross-domain spark, creative leap), calcolati **solo quando** il modello chiama
  un tool (`pre_turn`/`get_context`). MCP è **pull-only**: Neuron non può spingere nulla da solo.
- **Domini** — `store_turn.domain` free-form (l'LLM lo detta, `server.py:1671`); path `auto` usa
  euristica + `_refine_domain` (vector search sul seed) per promuovere un "general". Asimmetria
  nota: i domini emergenti dell'LLM non diventano attrattori per il path euristico.

---

## 4. Il piano a fasi

L'ordine NON è quello di `FutureIdeas.md` (1→8). È riordinato per far leva sulla fondazione.

| Fase | Cosa | Perché prima | ADR | Sforzo |
|------|------|--------------|-----|--------|
| **0** | Embedding configurabile + multilingua | fondazione: moltiplica ogni strato sopra | [ADR-001](01-embedding-model.md) | ~2 g |
| **1** | Vettori sempre persistiti + consolidation + prune | rende il fallback cheap e la crescita costante | [ADR-002](02-vectors-consolidation.md) | ~4 g |
| **2** | Motore di stimolo unificato (spreading activation) | il "bomba": stimoli cervello, non tag | [ADR-003](03-stimulus-engine.md) | ~5 g |
| **3** | Drift cross-contesto + sleep-mode pre-staging | stimoli inattesi + aggira il no-push di MCP | [ADR-004](04-drift-sleep.md) | ~4 g |

**Dipendenze:** 0 abilita tutto. 1 tiene N piccolo così il motore di stimolo (Fase 2) resta
veloce anche sul fallback. 2 introduce i pesi Hebbian/salienza che 3 (drift) riusa. Farle in
ordine massimizza il compounding — come nota giustamente `FutureIdeas.md`: *"the ideas compound."*

**Fuori dal percorso critico (migliorie ortogonali, dopo):** `extract --curated` (#6),
sentiment decay (#7), role-based tagging (#8). Buone, ma non toccano embedding→fallback→stimoli.

---

## 5. Come sappiamo che è "una bomba" (criteri di successo)

- **Embedding:** una query italiana recupera nodi inglesi pertinenti e viceversa (recall
  cross-lingua misurabile su un set di prova EN/IT). Modello scambiabile senza modifiche di schema.
- **Fallback:** tempo di `vector_search` sul tier sqlite ~costante al crescere delle sessioni
  (grazie a prune+consolidation), e zero ri-embedding a runtime durante la ricerca.
- **Stimoli:** ogni risposta di un tool porta uno stimolo compatto e pertinente; almeno una
  associazione cross-dominio "sorprendente ma sensata" per sessione, valutata a campione.
- **Sistema:** i tre strati restano scomponibili (transport ⟂ storage ⟂ embedding), invariante
  utente-solo == team (cambia solo la connessione).

## 6. Non-goal (per ora)

- Push reale verso il modello (MCP non lo consente in modo affidabile cross-client → si aggira,
  non si forza; vedi ADR-003/004).
- Indice ANN (DiskANN/libsql_vector_idx): rimandato finché i nodi per contesto non superano
  ~decine di migliaia (vedi ADR-002).
- Layer API/servizio davanti al DB condiviso (solo se si va oltre il team ≤6).
- Silos di embedding per-lingua (peggiora il recall cross-lingua; vedi ADR-001, opzione B).
