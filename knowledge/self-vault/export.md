# export — esporta il grafo come JSON

Produce una copia portabile del grafo corrente (nodi, link, metadati) in formato JSON. Non modifica il grafo.

## Quando usarlo

- **Prima di [[reset]]**: backup dei dati.
- Per migrare dati tra installazioni Neuron.
- Per analisi esterne (es. Graphify, visualizzazione custom).
- Per condividere un subset di memoria con un altro utente.

## Come si usa

```
export()
```

Ritorna l'intero grafo attivo come JSON strutturabile.

## Vantaggio

Portabilità: il JSON può essere letto da qualunque tool. È il "save-as" della memoria.

## Link

[[reset]] (distruttivo — export prima) | [[status]] (sapere quanto stai esportando) | [[Neuron]]
