# confirm — feedback sul contesto ricevuto

Segnale di feedback: dice a Neuron che il contesto caricato da [[pre_turn]] o [[get_context]] è stato effettivamente utile. Aumenta la salienza delle keyword indicate, così riemergono più facilmente in futuro.

## Quando usarlo

- Dopo aver usato il contesto restituito da `pre_turn` o `get_context` per generare la risposta.
- Opzionale ma prezioso: migliora la qualità futura del retrieval.
- Saltarlo è sicuro — non rompe nulla, degrada solo gradualmente la pertinenza.

## Come si usa

```
confirm(keywords=["kw_utile1", "kw_utile2"], boost=2)
```

- `keywords`: i nodi che sono stati effettivamente rilevanti
- `boost`: quanto aumentare la salienza (default 2, max 5)

## Vantaggio

Chiude il ciclo di rinforzo: `pre_turn` carica → usi → `confirm` rafforza. Col tempo i nodi più utili emergono sempre prima, quelli ignorati decadono. È il "gradiente" della memoria.

## Link

[[pre_turn]] (contesto che confermi) | [[store_turn]] (loop partner) | [[salienza]] (la metrica che modifichi)
