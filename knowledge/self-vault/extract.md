# extract — estrazione semantica da testo

Analizza un testo e ne estrae: keyword, topic, dominio, intento, sentiment, entità. Usa euristica (regole + scoring) di default, o LLM opzionale se `use_llm=true`.

## Quando usarlo

- Come passo separato se vuoi ispezionare l'estrazione prima di salvare.
- [[auto]] già include `extract` + save in un colpo solo — preferire quello per il workflow normale.
- `use_llm=true` per estrazione più intelligente su testi complessi (costa latenza).

## Come si usa

```
extract(text="testo da analizzare", use_llm=False)
```

- L'estrazione euristica è 0-token e deterministica.
- LLM richiede un endpoint Ollama configurato e blocca l'event loop se `use_llm=true`.

## Vantaggio

Trasparenza: vedi cosa Neuron "capisce" dal testo prima che finisca nel grafo. Utile per debugging e per capire perché certi nodi vengono creati.

## Link

[[auto]] (extract + save insieme) | [[store_turn]] (salvataggio curato)
