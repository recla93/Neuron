# list_contexts — elenca tutti i contesti esistenti

Mostra tutti i contesti attualmente caricati in memoria, con metadati: numero di nodi, link, turni, contesto attivo, se è seed o utente.

## Quando usarlo

- Per navigare la struttura della memoria.
- Prima di [[switch_context]] per vedere cosa è disponibile.
- Diagnostica: quanta memoria c'è in ogni dominio?

## Come si usa

```
list_contexts(parent=None)
```

- `parent`: filtra per prefisso (es. `java` mostra `java/spring`, `java/backend`).

## Vantaggio

Visione d'insieme della struttura a contesti. Senza `list_contexts` non sai quali domini sono già popolati.

## Link

[[contesti]] | [[switch_context]] | [[status]] (contesto attivo)
