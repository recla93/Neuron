# ADR-003: Motore di stimolo unificato (spreading activation) — il cuore

**Stato:** Proposed
**Data:** 2026-07-07
**Deciders:** recla93 (owner)
**Fase roadmap:** 2 — il "bomba"

## Contesto

Gli "stimoli" sono ciò che distingue una memoria associativa da un DB taggato: le associazioni
pertinenti e inattese che Neuron porta alla mente del modello. Oggi vivono in
`_build_context_window` (`server.py:~620-720`) come **tre euristiche separate**:
dormant pulse, cross-domain spark, creative leap (path a 2 hop verso un dominio diverso).

Due limiti strutturali:

1. **MCP è pull-only.** I flash emergono **solo quando** il modello chiama un tool
   (`pre_turn`/`get_context`). Neuron non può "bussare" da solo. L'efficacia dipende dal fatto che
   il modello chiami davvero i tool a ogni turno — cosa che il funnel skill (signpost + `skill`)
   incoraggia ma non garantisce.
2. **Euristiche scollegate.** Tre regole indipendenti, non un motore. I pesi dei link sono statici
   (fissati alla creazione, mai rinforzati), la salienza dei nodi non influenza il ranking di
   recupero, e non c'è propagazione dell'attivazione lungo il grafo. È "cervello" solo a metà.

`FutureIdeas.md` #1 (Hebbian), #2 (drift), #5 (salience ranking) sono i pezzi giusti, ma vanno
**fusi in un unico motore**, non implementati come feature isolate.

## Decisione

Costruire un **motore di stimolo unico** basato su **spreading activation** e integrarlo nel
percorso di risposta dei tool.

1. **Pesi Hebbian (#1).** I link co-attivati si rinforzano: quando A e B compaiono nello stesso
   turno, `co_activation_count += 1` (con cooldown ≥2 turni per evitare il caso fortuito); soglie
   di upgrade peso `tangential→medium` a 3, `medium→strong` a 8. Nuova colonna sui link (no nuovo
   storage). Il layer atomico esiste già (T11: promozione peso monotòna via SQL).
2. **Spreading activation.** Dai keyword attivi si propaga attivazione lungo i link, pesata da
   forza-link (Hebbian) × salienza-nodo × decadimento per hop. Emerge il nodo a più alta
   attivazione **anche se non è un match vettoriale diretto** — è ciò che rende lo stimolo
   sorprendente ma sensato.
3. **Ranking composito (#5).** Il recupero non ordina più solo per peso→inattività, ma per
   punteggio composito: `score = cos_sim*0.5 + (salience/MAX)*0.3 + (1/inactive_turns)*0.2`
   (pesi tarabili). "Recupera ciò che conta, non solo ciò che matcha."
4. **Emissione piggyback (aggira il no-push).** Poiché il modello **comunque** chiama
   `store_turn`/`auto`/`pre_turn`, ogni risposta di tool porta sempre un **blocco-stimolo compatto**
   (il flash a più alta attivazione), non solo `get_context`. Stimolazione continua senza push.

## Opzioni considerate

### Opzione A — Motore unificato + piggyback (scelta)
| Dimensione | Valutazione |
|-----------|-------------|
| Complessità | Media-alta (spreading activation + integrazione nei tool) |
| Costo | Propagazione limitata a k-hop (≤2–3) e top-M nodi → economica |
| Efficacia | Stimoli continui, brain-like; sfrutta ciò che il modello già chiama |

**Pro:** trasforma tre euristiche in un motore coerente; non richiede push; i pezzi (#1/#2/#5)
si rinforzano. **Contro:** più superficie da tarare (pesi, soglie, decadimento).

### Opzione B — Push via notifiche MCP (`resources/updated`)
**Pro:** sarebbe vero push. **Contro:** pochi client inoltrano le notifiche al modello come turno
→ valore reale limitato e non portabile. Utile al massimo come canale opzionale, non come base.

### Opzione C — Tenere le euristiche separate, solo migliorarle
**Pro:** minimo sforzo. **Contro:** resta "cervello a metà": niente rinforzo, niente propagazione,
salienza ignorata nel ranking. Non è il salto di qualità richiesto.

## Analisi dei trade-off

Il rischio principale è il **rumore**: uno spreading activation troppo largo produce stimoli
irrilevanti. Mitigazioni: limitare a k≤2–3 hop e top-M nodi, soglia minima di attivazione, cooldown
Hebbian, e — cruciale — **embedding buoni** (ADR-001) perché la componente `cos_sim` del ranking
non menta. La qualità dello stimolo eredita direttamente dallo Strato 1: senza ADR-001, questo
motore amplifica anche gli errori. Per questo è Fase 2, dopo 0 e 1.

## Conseguenze

- **Più facile:** stimoli pertinenti e continui a ogni interazione; associazioni cross-dominio non
  banali; recupero che privilegia i concetti importanti.
- **Più difficile / da gestire:** budget di token dello stimolo (deve restare compatto per non
  gonfiare ogni risposta); tuning dei pesi; test di non-regressione sulla "sorpresa utile".
- **Da rivedere:** i pesi del ranking composito e i parametri di propagazione, sui dati reali; la
  soglia oltre cui uno stimolo va soppresso perché troppo debole.

## Action items

1. [x] Aggiungere `co_activation_count` + cooldown ai link; upgrade peso Hebbian (riusa il path
       atomico T11). — `Graph.reinforce_coactivation()` (E2.1): bump ≤1 ogni `HEBBIAN_COOLDOWN=2`
       turni, upgrade `tangential→medium` a 3, `medium→strong` a 8, promozione monotòna via il
       CASE atomico di T11; colonna `co_activation_count` persistita (MAX sotto scrittori concorrenti).
       Chiamato in `store_turn` e `auto`. Test: `tests/test_hebbian.py`.
2. [x] Implementare `spreading_activation(seed_keywords, k, weights)` in `server.py`/`models.py`.
       — `Graph.spreading_activation(seeds, k=2, decay=0.5, min_activation=0.01)` (E2.3): walk puro,
       contributo per hop = `attivazione × (WEIGHT_ORDER/3) × (1 + salience/max_sal) × decay`; la
       forza-link cresce con l'Hebbian (E2.1). Ritorna i nodi non-seed per attivazione. Cablato ai
       flash in E2.4. Test: `tests/test_spreading.py`.
3. [x] Sostituire l'ordinamento di `get_context` col punteggio composito (#5), pesi via costante.
       — `_resolve_context` ranca i nodi con `RANK_WEIGHTS["sim"]*cos + ["salience"]*sal_norm +
       ["recency"]*1/(inactive+1)` (0.5/0.3/0.2, tunable). Sblocca il gate salienza di E1.2: la
       auto-consolidation ora passa `protect_salience=CONSOLIDATE_PROTECT_SALIENCE` (=8). Test:
       `tests/test_composite_ranking.py`.
4. [x] Unificare i tre flash sotto il motore; selezione dello stimolo top-1/top-2 per compattezza.
       — **Scelta owner: motore come SELETTORE** (non generatore). Le 3 euristiche (dormant/cross/leap)
       generano candidati; `spreading_activation` (E2.3) punteggia quelli in-graph (dormant/leap),
       il cross-domain resta scored per sim (motore single-graph); emessi solo i **top-2** per
       attivazione in `_build_context_window`. Test: `test_flashes_capped_at_top_two`.
       **Opzione B (futuro "forse"):** far diventare `spreading_activation` il generatore PRIMARIO
       (il nodo a più alta attivazione È lo stimolo, dormant/leap emergenti) — reshape più audace,
       da rivalutare sui dati reali. Nota lasciata nel codice (`_build_context_window`) e qui.
5. [ ] Emettere il blocco-stimolo compatto in **ogni** tool response (piggyback), con budget token.
6. [ ] Test: co-attivazione rinforza il link giusto; propagazione a 2 hop emerge; salienza alta
       risale nel ranking; stimolo sotto soglia soppresso; budget token rispettato.
