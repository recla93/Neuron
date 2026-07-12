# Releasing Neuron — flow di riferimento

Stato del repo al 2026-07-11 (audit): master è la linea viva (v5.2.0 rilasciata);
`FiveFix`, `feat/neuron-bomb` e `5.x` sono TUTTI già merged in master — etichette
morte; `4.x` è la linea v4 (solo locale); 3 stash vecchi. La sezione "Bonifica"
qui sotto sistema tutto una volta sola.

## Il flow (da qui in avanti — trunk-based, semplice)

1. **master è sempre rilasciabile.** Ogni lavoro nasce su un branch corto
   `feat/<tema>` o `fix/<tema>` e rientra in master quando la suite è verde.
   Niente branch di lunga vita: FiveFix/neuron-bomb sono serviti, ma il costo
   (drift, stash persi, merge ansiogeni) l'hai visto.
2. **Release = 1 commit + 1 tag.** Nello stesso commit: bump di
   `src/neuron/__init__.py::__version__` + sezione nuova in `CHANGELOG.md`.
   Poi:
   ```
   git tag vX.Y.Z
   git push origin master --tags
   ```
   Il tag fa scattare `release.yml`: wheel PyTurso (3.10-3.14), build del
   pacchetto, GitHub Release automatica. Non serve altro.
3. **SemVer:** PATCH = solo fix; MINOR = feature retro-compatibili (il caso
   tipico); MAJOR = migrazioni dati/comportamento (es. cambio modello
   embedding di default).
4. **La linea v4** (se ancora ti serve): vive su `4.x`, riceve solo cherry-pick
   di fix (`git cherry-pick <sha>`), tagga `v4.y.z`. Se non la usa più nessuno,
   eliminala e chiudi il capitolo.

## Bonifica one-shot (esegui in locale, ~2 minuti)

```powershell
cd C:\Users\recla\Desktop\NEURON\Update\neuron-project

# 0. Ispeziona gli stash prima di buttarli (roba del 9-10 luglio, quasi certo obsoleta)
git stash show -p stash@{0} | more   # ripeti per {1} e {2}; se non serve nulla:
git stash clear

# 1. Committa il lavoro corrente (T54-T59: gate, episodi, telemetria,
#    extraction/funnel, doctor processi, menu, plugin Cowork, bump 5.3.0)
git add -A
git commit -m "release(v5.3.0): quality at the door - curation gate, episodes, telemetry, extraction/funnel split"

# 2. Tag + push (fa partire release.yml)
git tag v5.3.0
git push origin master --tags

# 3. Elimina i branch morti (tutti gia' merged in master - verificato)
git branch -d FiveFix feat/neuron-bomb 5.x
git push origin --delete FiveFix feat/neuron-bomb 5.x

# 4. La linea v4: pubblicala se la vuoi mantenere, altrimenti eliminala
git push origin 4.x          # oppure: git branch -D 4.x

# 5. Igiene finale
git remote prune origin
git log --oneline --graph --all -10   # deve mostrare UNA linea pulita
```

Dopo il push del tag: controlla Actions → `release.yml` verde → la Release
compare su GitHub con wheel e sdist allegati. (Verifica anche che i tag
v5.0.x-v5.2.0 abbiano la loro Release: se il workflow all'epoca era rotto,
puoi rilanciarlo retroattivamente con `gh release create v5.2.0 --generate-notes`.)

## Impostazioni GitHub consigliate (una volta)

- Branch protection su master: PR non necessarie finché sei solo, ma attiva
  "Require status checks" (ci.yml) appena il team cresce.
- Default branch = master (già così), elimina i branch remoti morti dalla UI
  se qualcosa resiste al comando sopra.
