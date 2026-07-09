# pre_turn — LOOP STEP 1: carica contesto prima di rispondere

Chiamare **per primo** su ogni turno sostanziale, **prima** di generare la risposta. Unico tool obbligatorio del loop. Carica in un colpo solo: stato del grafo + contesto rilevante (nodi/link collegati al topic corrente).

## Quando usarlo

- **Sempre** su turni che contengono una domanda, un task, un'esplorazione.
- **Saltare** solo su turni puramente procedurali (ringraziamenti, saluti, conferme yes/no) o quando il grafo è vuoto (primo turno assoluto).
- È il primo tool della sessione: senza `pre_turn` il modello risponde a freddo, senza memoria.

## Come si usa

```
pre_turn(topic="riassunto 3-5 parole", keywords=["kw1", "kw2"], max_tokens=200)
```

- `topic`: sintesi del messaggio utente
- `keywords`: 2-4 concetti estratti
- `max_tokens`: contesto restituito (default 200, compact)

## Vantaggio

Un'unica chiamata sostituisce `status` + `get_context` (2 chiamate). Il contesto caricato viene iniettato silenziosamente nel ragionamento: il modello sa cosa Neuron già conosce e cosa no.

## Link

[[store_turn]] (step 2 del loop) | [[get_context]] (versione esplicita) | [[salienza]] (i nodi restituiti hanno salienza) | [[Neuron]]
