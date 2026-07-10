# extract — estrazione semantica da testo (euristico, 0 token)

Analizza un testo e ne estrae: keyword, topic, dominio, intento, sentiment, entità.
Sempre euristico (regole + scoring). LLM extraction rimossa da server.py — è responsabilità
del modello chiamante fornire parametri via [[store_turn]].

## Quando usarlo

- Come passo separato se vuoi ispezionare l'estrazione prima di salvare.
- [[auto]] già include `extract` + save in un colpo solo — preferire quello per il workflow normale.

## Come si usa

```
extract(text="testo da analizzare")
```

- 0-token, deterministico, nessuna dipendenza esterna.

## Vantaggio

Trasparenza: vedi cosa Neuron "capisce" dal testo prima che finisca nel grafo. Utile per debugging e per capire perché certi nodi vengono creati.

## Link

[[auto]] (extract + save insieme) | [[store_turn]] (salvataggio curato)
