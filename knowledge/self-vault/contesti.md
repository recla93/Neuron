# Contesti — organizzazione gerarchica della memoria

I contesti dividono la memoria in domini separati ma collegati. Ogni contesto è un grafo indipendente. Un contesto figlio eredita i nodi del genitore (es. `java/spring` eredita da `java` e da `default`).

## Perché esiste

Un singolo grafo mescolerebbe concetti backend, frontend, gaming e AI — il rumore ucciderebbe il retrieval. I contesti tengono separati i domini ma permettono ai link cross-contesto (drift) di collegarli quando serve.

## Struttura

- `default` — radice, sempre presente
- `java/spring` — backend Spring
- `python/django` — backend Python
- `architecture` — pattern, deployment, infrastruttura

## Strumenti

- [[switch_context]] cambia il contesto attivo (lo crea se nuovo)
- [[list_contexts]] elenca tutti i contesti esistenti
- [[get_context]] cerca nel contesto attivo (e risale la catena di ereditarietà)
- [[store_turn]] salva nel contesto attivo
- [[reset]] azzera un contesto specifico o tutti

## Ereditarietà

Quando `get_context` cerca keyword nel contesto attivo, se non trova abbastanza risultati risale automaticamente ai genitori. I risultati ereditati sono annotati `(from: <parent>)`.
