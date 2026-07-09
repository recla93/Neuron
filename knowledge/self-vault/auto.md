# auto — fallback POST: estrai + salva in un colpo solo

Pipeline completa in una chiamata: extract → topic-shift → auto-link → [[store_turn]]. Fallback a costo zero (euristico, nessun LLM) per quando non si ha tempo di curare un [[store_turn]] manuale.

## Quando usarlo

- Turni "usa e getta": messaggi brevi, conversazione veloce, informazioni a basso rischio.
- Quando il modello non è in grado di fare estrazione curata (modelli piccoli).
- **Preferire** `store_turn` manuale quando si introducono concetti importanti o link complessi.

## Come si usa

```
auto(text="messaggio utente + risposta")
```

- Unico parametro: tutto il testo del turno.
- L'estrazione è euristica (keyword, topic, dominio, intento, sentiment).
- Usa `use_llm` solo se serve estrazione LLM (costosa, lenta).

## Vantaggio

Zero sforzo: una chiamata e la memoria è aggiornata. Il grafo cresce anche quando non si cura. Il costo è grafo leggermente più rumoroso (link generici, keyword meno precise) — [[consolidate]] periodico lo ripulisce.

## Link

[[store_turn]] (alternativa curata) | [[extract]] (solo estrazione) | [[consolidate]] (pulizia post-auto) | [[find_candidates]] (screening)
