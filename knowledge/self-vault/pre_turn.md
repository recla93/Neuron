# pre_turn — LOOP STEP 1: carica contesto prima di rispondere

Chiamare **per primo** su ogni turno sostanziale, **prima** di generare la risposta. Unico tool obbligatorio del loop. Carica in un colpo solo: stato del grafo + contesto rilevante (nodi/link collegati al topic corrente).

## Quando usarlo

- **Sempre** su turni che contengono una domanda, un task, un'esplorazione.
- **Saltare** solo su turni puramente procedurali (ringraziamenti, saluti, conferme yes/no) o quando il grafo è vuoto (primo turno assoluto).
- È il primo tool della sessione: senza `pre_turn` il modello risponde a freddo, senza memoria.

## Come si usa

```
pre_turn(topic="riassunto 3-5 parole", keywords=["kw1", "kw2", "kw3"], max_tokens=200)
```

- `topic`: sintesi del messaggio utente
- `keywords`: 3-5 concetti estratti, sostantivi (mai verbi o parole di riempimento)
- `max_tokens`: contesto restituito (default 200, compact)

## Vantaggio

Un'unica chiamata sostituisce [[status]] + [[get_context]] (2 chiamate). Il contesto caricato viene iniettato silenziosamente nel ragionamento: il modello sa cosa Neuron già conosce e cosa no.

## Quando ammette di non sapere

`pre_turn` **non serve il meno peggio**. Se la risposta poggia sui vettori — nessuna keyword centrata nel grafo — e la somiglianza migliore resta sotto soglia, dichiara:

```
⚠ nessun contesto rilevante (best=0.14, soglia=0.35)
```

e non propone [[confirm]], perché chiedere di confermare un errore è peggio che non chiedere niente. La soglia si regola con `NEURON_MIN_RELEVANCE`.

Il motivo è asimmetrico: una risposta vuota insegna «grafo ancora povero, riprovo». Una risposta irrilevante ma servita con sicurezza insegna «questo tool è rumore» — e quella non si disimpara. Un falso positivo non costa una risposta, costa tutte le chiamate successive.

## Quando la stanza è sbagliata

Il richiamo cerca nel **contesto attivo**. Se il topic è più vicino a un altro contesto, `pre_turn` lo dice invece di rispondere lo stesso:

```
⚠ topic distante dal contesto attivo 'ai' (0.11) — 'studio/bash' è più vicino (0.71): switch_context('studio/bash')?
```

Margine regolabile con `NEURON_ROUTE_MARGIN`. Quasi sempre un risultato irrilevante non è un problema di richiamo: è la stanza sbagliata. Vedi [[contesti]] e [[switch_context]].

## Gli avvisi stanno su riga propria

Sopra il contesto e fuori dal budget di token, mai in coda alla riga di stato: un avviso che non interrompe la lettura non è un avviso. Vale anche per `(vector fallback)` e per `db=sqlite!degraded(...)`, che dicono **attraverso cosa** ha risposto questa chiamata.

## Link

[[store_turn]] (step 2 del loop) | [[get_context]] (versione esplicita) | [[salienza]] (i nodi restituiti hanno salienza) | [[switch_context]] e [[contesti]] (l'instradamento) | [[confirm]] e [[dismiss]] (il ciclo di feedback) | [[Neuron]]
