# store_turn — LOOP STEP 2: persiste ciò che è nuovo

Dopo aver risposto, chiamare `store_turn` per salvare i concetti emersi in questo turno. È il tool che fa crescere la memoria. I dati persistiti influenzeranno i futuri [[pre_turn]] e [[get_context]].

## Quando usarlo

- Dopo ogni risposta sostanziale che ha introdotto nuovi concetti, risposto a una domanda, o stabilito una connessione.
- **Saltare** su turni puramente procedurali (niente nuovo da memorizzare).
- Da preferire a [[auto]] quando possibile: produce un grafo più pulito.

## Come si usa

```
store_turn(
    topic="3-5 parole",
    keywords=["concetto1", "concetto2", "concetto3"],
    links=[{"source":"kw1", "target":"kw2", "link_type":"deepening", "weight":"strong", "rationale":"breve"}]
)
```

- `keywords`: 3-5 **nomi** di concetti (entità, tecnologie, domini). Mai verbi o filler.
- `links`: tipizzati (`deepening|analogy|evolution|contrast|cause-effect|instance-of`), mai self-link.
- `topic`: sintesi del turno, usata come display.

## Vantaggio

La memoria cresce organicamente. I link curati permettono a [[flash]] e [[spreading_activation]] di trovare connessioni non ovvie. [[find_candidates]] prima di chiamarlo evita duplicati.

## Link

[[pre_turn]] (step 1 del loop) | [[find_candidates]] (chiamare prima per evitare duplicati) | [[auto]] (fallback) | [[confirm]] (dopo, rafforza nodi utili) | [[Neuron]]
