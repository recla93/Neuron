# dismiss — feedback negativo sul contesto ricevuto

L'opposto di [[confirm]]: dice a Neuron che le associazioni recuperate erano sbagliate, fuori tema o rumorose. Abbassa salienza e trust dei nodi indicati, così riemergono meno nelle ricerche future.

## Quando usarlo

- Quando il contesto restituito da [[pre_turn]] o [[get_context]] non c'entrava con la domanda.
- Quando un nodo continua a riaffiorare portando associazioni che non servono.
- **Non** per punire un nodo che semplicemente non era il più rilevante: serve per quelli che ingannano, non per quelli che arrivano secondi.

## Come si usa

```
dismiss(keywords=["kw1", "kw2"], penalty=3, trust_penalty=0.5)
```

- `keywords`: i nodi da declassare
- `penalty`: quanta [[salienza]] togliere (0-5, default 3)
- `trust_penalty`: quanta fiducia togliere (default 0.5)

## Vantaggio

Il segnale negativo è più raro e più informativo di quello positivo: un `confirm` mancato può voler dire mille cose, un `dismiss` dice esattamente cosa non far riemergere. Insieme a [[confirm]] chiude il ciclo di feedback che decide cosa il grafo mostra per primo.

## Link

[[confirm]] (segnale opposto) | [[salienza]] (cosa viene abbassato) | [[pre_turn]] (dove nasce il contesto sbagliato) | [[prune]] (rimozione strutturale, non per feedback) | [[Neuron]]
