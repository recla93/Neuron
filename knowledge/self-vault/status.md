# status — stato corrente del grafo

Mostra il polso della memoria: numero di nodi, link, salute del grafo, contesto attivo. Chiamata sicura come primo check per vedere se la memoria contiene qualcosa.

## Quando usarlo

- Primo tool della sessione per capire se Neuron ha già dati.
- Diagnostica veloce: il grafo sta crescendo? I link sono sani?
- [[pre_turn]] già include le info di `status` (unica chiamata).

## Come si usa

```
status()
```

Nessun parametro. Ritorna: nodi, link (attivi/totali), contesto attivo, configurazione.

## Vantaggio

Zero side effect, zero parametri — non sbagli mai. È la "spia del cruscotto".

## Link

[[summary]] (versione approfondita) | [[pre_turn]] (già include status) | [[Neuron]]
