# reset — azzera il grafo

Cancella tutti i nodi e link del contesto attivo (o di tutti i contesti). **Distruttivo**: dopo `reset` la memoria riparte da zero.

## Quando usarlo

- Solo deliberatamente: cambio completo di progetto, test, pulizia radicale.
- Mai come primo passo — prima [[export]] per salvare il grafo, poi `reset`.

## Come si usa

```
reset(context=None)
```

- `context`: se specificato, resetta solo quel contesto.
- Se omesso, resetta tutti i contesti.

## Vantaggio

Ripartenza pulita senza cancellare file manualmente. È il "format" della memoria.

## Link

[[export]] (salvare prima di resettare) | [[contesti]] | [[switch_context]]
