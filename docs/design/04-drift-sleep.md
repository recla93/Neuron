# ADR-004: Drift cross-contesto + consolidation "sleep-mode" (pre-staging asincrono)

**Stato:** Accepted (2026-07-08) — Opzione A implementata (E3.1–E3.4). Sleep-mode gira nel fallback
"consolidation all'avvio-se-inattivo" (nessuno scheduler esterno cablato: integrazione futura).
**Data:** 2026-07-07
**Deciders:** recla93 (owner)
**Fase roadmap:** 3

## Contesto

Due limiti restano dopo le Fasi 0–2:

1. **I contesti sono silos.** Oggi un'associazione cross-dominio richiede un link `analogy`
   esplicito con rationale (creato da AI o utente). Il cervello associa senza rationale:
   *neapolitan-dough* ricorda *structured-concurrency* perché entrambi implicano "attesa scoped".
   Neuron non forma associazioni implicite tra contesti diversi.
2. **MCP non fa push** (vedi ADR-003): non possiamo stimolare il modello quando è inattivo. Ma
   abbiamo uno **scheduler** (capacità Cowork / task schedulati) utilizzabile per lavoro asincrono.

`FutureIdeas.md` #2 (drift) e #4 (sleep-mode) coprono questi due punti; qui li combiniamo perché
si rinforzano: il drift crea materiale nuovo, lo sleep-mode lo consolida e lo pre-stagia.

## Decisione

1. **Drift link cross-contesto (#2).** Se il keyword A (contesto X) e B (contesto Y) co-occorrono
   in un turno — o entro N turni via flash semantico — si forma un **drift link implicito** tra
   contesti, senza rationale. Regole anti-rumore: nasce `weight=tangential` con cooldown 5 turni
   prima di poter salire; solo tra contesti **effettivamente visitati** dall'utente; prune più
   veloce dei link intra-contesto (scade dopo 3 turni inattivi invece di 5). `get_context(depth≥3)`
   può far emergere queste associazioni inattese. Riusa i pesi Hebbian di ADR-003.
2. **Consolidation "sleep-mode" (#4).** Lo scheduler traccia il tempo dall'ultima interazione;
   dopo una soglia di inattività (es. 30 min o tra sessioni), esegue in background la consolidation
   di ADR-002 (merge/prune/archive) **e** un passo di pre-staging: precalcola "cosa dovrei
   ricordare al prossimo turno" (top stimoli via spreading activation) e lo lascia pronto nei
   `meta`, così il prossimo `pre_turn` lo restituisce già caldo. Non è push, è **stimolo
   pre-caricato**.

## Opzioni considerate

### Opzione A — Drift + sleep-mode insieme (scelta)
| Dimensione | Valutazione |
|-----------|-------------|
| Complessità | Media (drift = colonna+regole; sleep = trigger scheduler) |
| Costo | Lavoro fatto quando l'utente è assente → zero latenza percepita |
| Efficacia | Associazioni cross-dominio + grafo sempre "fresco" e pre-caricato |

**Pro:** aggira il no-push in modo onesto (pre-staging, non push); il drift dà lo "spark"
sorprendente che rende Neuron memorabile. **Contro:** i drift link sono i più a rischio rumore →
regole di prune aggressive obbligatorie.

### Opzione B — Solo drift, consolidation sincrona ogni N turni
**Pro:** niente dipendenza dallo scheduler. **Contro:** la consolidation ogni N turni aggiunge
latenza durante l'uso; niente pre-staging; il "20 turni" è arbitrario (come nota `FutureIdeas.md`).

### Opzione 0 — Status quo (silos, nessun background)
**Pro:** semplice. **Contro:** nessuna associazione cross-contesto implicita; il grafo si ingrassa
durante le sessioni; nessuno stimolo pre-caricato.

## Analisi dei trade-off

Il drift è la feature a **più alto potenziale e più alto rischio**: senza prune aggressivo e senza
il gate "solo contesti visitati", il grafo si riempie di associazioni casuali. Va introdotto
**dopo** che Hebbian + salienza (ADR-003) danno gli strumenti per pesarlo e potarlo. Lo sleep-mode
è a basso rischio ma dipende dall'affidabilità dello scheduler tra sessioni; se lo scheduler non è
disponibile, si degrada al trigger "consolidation all'avvio se inattivo da > soglia".

## Conseguenze

- **Più facile:** ricordare decisioni di design durante un debug (`role=any`/`depth`), scoprire
  ponti cross-dominio, iniziare ogni sessione con un grafo pulito e stimoli pronti.
- **Più difficile / da gestire:** contenere il rumore dei drift link; gestire l'assenza dello
  scheduler; evitare che il pre-staging diventi stale se il contesto cambia molto tra sessioni.
- **Da rivedere:** soglie di prune del drift, soglia di inattività sleep-mode, freschezza del
  pre-staging.

## Action items

1. [x] Drift link: formazione su co-occorrenza cross-contesto (contesti visitati), `tangential` +
       cooldown 5, prune a 3 turni inattivi; superficie in `get_context(depth≥3)`.
       — **E3.1**: `Link.target_context` (colonna+migrazione, pattern di E2.1); `Graph.form_drift_link(
       source, target, target_context, turn)` (nasce tangential, `DRIFT_COOLDOWN=5`, riusa il contatore
       Hebbian per rinforzare, `DRIFT_EXPIRY_TURNS=3` in `prune_tangential`). Formazione dal ramo
       cross-domain spark di `_build_context_window` (solo `other_ctx` caricato = visitato). Drift
       escluso da `get_active_links` e dall'adiacenza di `spreading_activation`.
       **E3.2**: `_resolve_context` salta i drift nella traversal normale e li fa affiorare solo a
       `depth>=3` (cap del tool = 3), annotati `target@context` nel render. Test: `tests/test_drift.py`.
2. [x] Sleep-mode: trigger da scheduler / all'avvio-se-inattivo; esegue `consolidate` (ADR-002) +
       pre-staging degli stimoli nei `meta`.
       — **E3.3**: `meta.last_active_timestamp` scritto a ogni save, letto al load (`_loaded_ts`).
       `Graph.sleep_maybe(now, idle_threshold=SLEEP_IDLE_SECONDS=1800, do_consolidate)` gira dal
       `registry.get` (una volta per contesto/processo) se inattivo > soglia: consolidate opzionale
       (gate `NS_CONSOLIDATE_AUTO`) + pre-staging. Nessuno scheduler esterno → fallback avvio-se-inattivo.
3. [x] `pre_turn` restituisce lo stimolo pre-caricato se presente e fresco.
       — **E3.4**: `sleep_maybe` precalcola il top stimolo (spreading dai `last_keywords`/nodo più
       saliente) e lo salva in `meta.staged_stimulus`/`staged_ts`. `Graph.take_staged_stimulus(now,
       fresh=STAGE_FRESH_SECONDS=6h)` lo serve UNA volta (one-shot) se fresco, lo scarta se stale;
       `pre_turn` lo mostra come `🧠 staged: …`.
4. [x] Test: drift solo tra contesti visitati ✅; prune rapido ✅; pre-staging servito+invalidato ✅;
       degradazione senza scheduler ✅ (`tests/test_drift.py`, `tests/test_sleep.py`).
