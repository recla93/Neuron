# switch_context — cambia il contesto attivo

Cambia il contesto corrente (es. da `default` a `java/spring`). Se il contesto non esiste ancora, viene creato al primo accesso.

## Quando usarlo

- All'inizio di una sessione su un dominio diverso.
- Quando il topic corrente è cambiato abbastanza da meritare un contesto separato.
- [[pre_turn]] e [[store_turn]] operano sul contesto attivo — cambialo prima se servi un altro dominio.

## Come si usa

```
switch_context(context="java/spring")
```

- La notazione è a slash: `java/spring`, `python/django`.
- La normalizzazione deduplica: `java-spring` e `java_spring` vengono riconosciuti come esistenti.

## Vantaggio

La memoria non è un unico calderone. Ogni dominio ha il suo spazio, ma i drift link ([[get_context]] depth>=3) li collegano.

## Link

[[contesti]] (struttura ed ereditarietà) | [[list_contexts]] (cosa esiste) | [[pre_turn]] (usa il contesto attivo)
