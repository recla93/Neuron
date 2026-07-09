# prune — potatura link tangenziali inattivi

Rimuove i link con peso `tangential` rimasti inattivi per più di 5 turni. I link `medium` e `strong` non vengono mai toccati.

## Quando usarlo

- Periodicamente, dopo [[consolidate]] o sessioni lunghe.
- I link tangenziali nascono da associazioni deboli (es. drift cross-contesto) e vanno puliti per non inquinare la traversata del grafo.

## Come si usa

```
prune()
```

Nessun parametro. Opera sui link del contesto attivo.

## Vantaggio

Riduce il rumore nella [[spreading_activation]]. I link deboli invecchiati non deviano più l'attivazione verso vicoli ciechi. Mantiene il grafo snello.

## Link

[[consolidate]] (pulizia complementare) | [[salienza]] (decadimento) | [[forgotten]] (nodi dimenticati)
