# Neuron — Installer Fixes & GitHub Release Plan

Proposta tecnica (v3.3.0). Due obiettivi: **(A)** correggere i bug dell'installer
`install.ps1`, e **(B)** trasformare il progetto in un pacchetto Python distribuibile
con una **GitHub Release** che pubblica wheel + sdist + source zip.

Stato: *proposta da rivedere prima di toccare il codice.*

---

## Parte A — Bug dell'installer (`install.ps1` / `install.bat`)

### A1. `Invoke-PipRetry` non rileva i fallimenti di pip (CRITICO)
**Dove:** `install.ps1` righe 50–59.
**Problema:** pip viene eseguito dentro `cmd /c "... 2>&1"` e poi si controlla
`$LASTEXITCODE`, ma quell'exit code è quello del wrapper esterno, non sempre quello di
pip. Inoltre la riga 55 tratta `$LASTEXITCODE -eq $null` come **successo**: uno step che
non imposta alcun exit code viene riportato "OK". Un `pip install` fallito può passare
in silenzio e l'installazione prosegue rotta.
**Fix:** invocare pip direttamente (non via `cmd /c`), controllare *solo*
`$LASTEXITCODE -eq 0` come successo, e fare hard-fail su qualsiasi altro valore.

```powershell
function Invoke-PipRetry {
    param([string[]]$PipArgs, [string]$Name)
    for ($a = 1; $a -le 3; $a++) {
        if ($a -gt 1) { Write-Host "   Attempt $a/3..." -ForegroundColor DarkYellow; Start-Sleep 3 }
        & $pip @PipArgs
        if ($LASTEXITCODE -eq 0) { Write-Host "   $Name OK" -ForegroundColor Green; return }
    }
    Write-Host "ERROR: $Name failed after 3 attempts" -ForegroundColor Red
    exit 1
}
# chiamata:
Invoke-PipRetry -PipArgs @("install","--timeout","60","--retries","3","mcp>=1.28.0") -Name "MCP SDK"
```

### A2. Controllo versione Python fragile (CRITICO)
**Dove:** righe 67–68.
**Problema:** `[double]$ver -lt 3.10` interpreta `"3.10"` come `3.1`, quindi
**Python 3.10 risulta `3.1 < 3.10` = vero** → rifiuta una versione valida; e su locale
con la virgola decimale il cast `[double]` si rompe del tutto.
**Fix:** confrontare major/minor come interi, indipendenti dal locale.

```powershell
$verParts = (python -c "import sys; print(sys.version_info.major, sys.version_info.minor)").Split()
$maj = [int]$verParts[0]; $min = [int]$verParts[1]
if ($maj -lt 3 -or ($maj -eq 3 -and $min -lt 10)) {
    Write-Host "ERROR: Python $maj.$min < 3.10" -ForegroundColor Red; exit 1
}
```

### A3. Il DB seed non esiste (CRITICO)
**Dove:** `knowledge/` contiene solo `base_knowledge.db.prerepair-20260630`.
**Problema:** `src/neuron/registry.py:46`, `scripts/neuron_console.py`,
`scripts/populate_vectors.py`, `scripts/seed_repair_links.py` e `scripts/run_mcp.bat`
si aspettano tutti `knowledge/base_knowledge.db`. Una nuova installazione parte **senza
conoscenza seed**.
**Fix (da decidere):** o (1) ripristinare/rigenerare `base_knowledge.db` (il file
`.prerepair-` suggerisce un repair andato storto), o (2) confermare che il seed venga
generato a runtime. Va chiarito prima di rilasciare: il file seed è dato che deve
viaggiare nel pacchetto (vedi B2).

### A4. Componente MSVC errato/deprecato
**Dove:** riga 134, `Microsoft.VisualStudio.Component.VC.Runtime.UCRTSDK`.
**Problema:** id componente non valido per i VS 2022 Build Tools correnti → l'install
silenzioso può non fare nulla pur "riuscendo".
**Fix:** usare i componenti corretti, p.es.
`Microsoft.VisualStudio.Component.Windows11SDK.22621` (o `Windows10SDK.*`) insieme a
`Microsoft.VisualStudio.Component.VC.Tools.x86.x64`, oppure il workload
`Microsoft.VisualStudio.Workload.VCTools`.

### A5. Refresh del PATH incompleto dopo l'install di toolchain
**Dove:** righe 88 e 136.
**Problema:** dopo aver installato Rust/MSVC, il PATH viene ricomposto solo da
`Machine` + `User` env. Le voci scritte dal toolchain nella sessione corrente possono
non essere ancora visibili → `rustc`/`cl` "non trovati" anche quando l'install è
andato bene.
**Fix:** aggiungere esplicitamente `~/.cargo/bin` (Rust) al PATH di sessione e
ri-sondare con `where.exe` prima di dichiarare fallimento.

### A6. Codice morto + commento fuorviante
**Dove:** riga 173 (`$pipBase` mai usato) e i commenti su un "EU mirror" che non
esiste nel codice.
**Fix:** rimuovere `$pipBase` e allineare i commenti al comportamento reale.

> Nota: A1, A2, A3 sono bloccanti per una release affidabile; A4–A6 sono robustezza/pulizia.

---

## Parte B — Packaging per la GitHub Release (wheel)

Scelta confermata: **GitHub Release con wheel + sdist** allegati (no PyPI). Gli utenti
installeranno con `pip install` puntando all'asset della release.

### B1. `pyproject.toml` non sa trovare il pacchetto (BLOCCANTE)
**Problema:** con il src-layout (`src/neuron/`) e nessuna sezione
`[tool.setuptools]`, `python -m build` / `pip install .` **non trovano il pacchetto
`neuron`** → wheel vuoto o build fallita.
**Fix:** dichiarare la package discovery src-layout.

```toml
[tool.setuptools]
package-dir = {"" = "src"}

[tool.setuptools.packages.find]
where = ["src"]
```

### B2. Il DB seed non finisce nel wheel (BLOCCANTE per la funzionalità)
**Problema:** `*.db` è in `.gitignore`, non c'è `MANIFEST.in`, né `package-data`. Il
seed non verrebbe impacchettato. In più oggi `registry.py` cerca il seed via percorso
relativo al repo (`../knowledge/base_knowledge.db`), che **non esiste in un pacchetto
installato**.
**Fix (consigliato):** spostare il seed *dentro* il package, p.es.
`src/neuron/data/base_knowledge.db`, dichiararlo come package-data e leggerlo via
`importlib.resources` invece che con un path relativo al repo.

```toml
[tool.setuptools.package-data]
neuron = ["data/*.db"]
```
```python
# registry.py — lettura robusta del seed
from importlib.resources import files
self._seed_path = str(files("neuron").joinpath("data/base_knowledge.db"))
```
Aggiungere comunque un `MANIFEST.in` con `recursive-include src/neuron/data *.db` per
l'sdist, e in `.gitignore` un'eccezione `!src/neuron/data/base_knowledge.db` così il
seed entra nel versioning.

### B3. Console entry-point rotto (BLOCCANTE)
**Problema:** `[project.scripts] neuron-mcp = "neuron.server:main"`, ma
`server.py:1939` è `async def main()`. Eseguito come console script fallirebbe (coroutine
mai attesa).
**Fix:** aggiungere un wrapper sincrono e puntare lo script a quello.

```python
# server.py
def cli() -> None:
    asyncio.run(main())
```
```toml
[project.scripts]
neuron-mcp = "neuron.server:cli"
```

### B4. Single source of truth per la versione
**Problema:** `3.3.0` è duplicato in `pyproject.toml` e `__init__.py` (rischio drift al
prossimo bump).
**Fix:** versione dinamica da `__init__.py`.

```toml
[project]
dynamic = ["version"]
[tool.setuptools.dynamic]
version = {attr = "neuron.__version__"}
```

### B5. Workflow di release (nuovo file) — con wheel pyturso Windows pre-compilati
Aggiungere `.github/workflows/release.yml` che, al push di un tag `v*`:
1. **Build delle dipendenze Windows** (`pyturso`) su `windows-latest`, una wheel per
   ogni versione di Python supportata. Il runner GitHub ha già Rust + MSVC + Windows SDK,
   quindi la compilazione che fallirebbe sul PC dell'utente qui riesce gratis.
2. **Build del pacchetto Neuron** (`python -m build`) → wheel + sdist.
3. **GitHub Release** con allegati: la wheel di Neuron, l'sdist, e le wheel
   `pyturso-*-win_amd64.whl` vendorizzate.

```yaml
name: Release
on:
  push:
    tags: ["v*"]
permissions:
  contents: write
jobs:
  # --- 1. Compila pyturso per Windows (no win_amd64 su PyPI → lo costruiamo noi) ---
  build-pyturso-win:
    runs-on: windows-latest          # ha già Rust + MSVC + Windows SDK
    strategy:
      matrix:
        python: ["3.10", "3.11", "3.12", "3.13"]
    steps:
      - uses: actions/setup-python@v5
        with: { python-version: "${{ matrix.python }}" }
      - run: pip wheel "pyturso==0.6.1" --no-deps -w wheelhouse
      - uses: actions/upload-artifact@v4
        with: { name: pyturso-win-${{ matrix.python }}, path: wheelhouse/*.whl }

  # --- 2. Build pacchetto Neuron + 3. Release ---
  build-release:
    needs: build-pyturso-win
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install build
      - run: python -m build           # dist/*.whl + dist/*.tar.gz
      - run: python -m pip install dist/*.whl && python -c "import neuron; print(neuron.__version__)"
      - uses: actions/download-artifact@v4
        with: { pattern: pyturso-win-*, path: dist, merge-multiple: true }
      - uses: softprops/action-gh-release@v2
        with:
          files: dist/*               # neuron wheel + sdist + pyturso win wheels
          generate_release_notes: true
```

> **Pin esatto:** `pyproject.toml` deve fissare `pyturso==0.6.1` (non `>=`), così pip non
> tenta una versione più nuova priva di wheel vendorizzata cadendo in compilazione.
> Bump di pyturso o di una minor di Python ⇒ ri-eseguire il job e ri-allegare le wheel.

### B6. Installer riscritto: dependency-first, MSVC minimale, `pip install` del wheel
Modello scelto: **Opzione B (hybrid)**. L'installer NON installa più tutto VS, e prova
prima la via senza compilatore.

**Ordine (dependency-first):**
1. **Python ≥ 3.10** e nella matrice supportata (3.10–3.13). Se fuori range → messaggio
   chiaro (fuori dai wheel vendorizzati).
2. **venv**.
3. **`pip install --find-links <vendor> neuron-*.whl`** — `<vendor>` è la cartella con le
   wheel `pyturso-*-win_amd64.whl` scaricate dalla Release. pip trova la wheel binaria di
   pyturso → **nessuna compilazione**; `mcp`/`fastembed` arrivano da PyPI (pure-python).
4. **Fallback toolchain** — *solo* se il passo 3 fallisce (Python fuori matrice, niente
   wheel compatibile): installare **solo i Build Tools MSVC minimi**, poi `pip install`
   in modalità compilazione. Componenti corretti (fix A4):
   ```
   --add Microsoft.VisualStudio.Component.VC.Tools.x86.x64
   --add Microsoft.VisualStudio.Component.Windows11SDK.22621
   --quiet --wait --norestart
   ```
   NON installare workload completi né l'IDE. (Rust serve solo in questo ramo fallback.)
5. **Registrazione client MCP** + shortcut (come oggi).

Effetto: le sezioni 2–3 attuali (Rust, Windows SDK, MSVC, fallback GNU, ~95 righe)
diventano un ramo *eccezionale*, non il percorso normale. Spariscono di fatto i bug A4/A5
dal flusso comune. Il `run_mcp.bat` + hack `PYTHONPATH=src` vengono rimpiazzati da un vero
pacchetto installato nel venv (niente drift sorgente/install).

---

## Decisione: Opzione B (hybrid), dependency-first

Sintesi del percorso scelto, in ordine di priorità ("prima le dipendenze"):
- **Le dipendenze vengono prima.** L'utente Windows non deve avere un compilatore: pip
  installa `pyturso` da una **wheel `win_amd64` pre-compilata in CI** e allegata alla
  Release. `mcp`/`fastembed` sono pure-python (sempre da PyPI).
- **Niente VS completo.** Il ramo di compilazione (fallback) installa **solo i Build
  Tools MSVC minimi** (`VC.Tools.x86.x64` + `Windows11SDK.22621`), mai l'IDE o i workload.
- **`pip install` del wheel** sostituisce la copia manuale + `PYTHONPATH=src`.

## Ordine di esecuzione consigliato

1. **B1 + B3 + B4** — rendere il progetto buildabile (`python -m build` → wheel
   importabile). Verifica: install del wheel in un venv pulito + `import neuron`.
2. **B2 + A3** — risolvere il seed DB (dentro il package + `importlib.resources`),
   decidere come rigenerare `base_knowledge.db`.
3. **B5** — `release.yml` con il job `build-pyturso-win` (le wheel di dipendenza
   pre-compilate) + build pacchetto + Release. *Questo realizza il "dependency-first":
   gli artefatti delle dipendenze esistono prima che l'installer giri.*
4. **B6** — riscrittura `install.ps1`: dependency-first, `pip install --find-links`,
   MSVC minimale solo come fallback. (Incorpora A1 + A2; rende A4/A5 marginali.)
5. **A1 + A2 + A6** — fix/pulizia residui dell'installer non già coperti da B6.

## Verifica finale (prima di taggare)
- Job `build-pyturso-win` produce `pyturso-0.6.1-cp3XX-cp3XX-win_amd64.whl` per ogni
  Python 3.10–3.13.
- `python -m build` produce `dist/neuron-3.3.0-py3-none-any.whl` **contenente**
  `neuron/data/base_knowledge.db`.
- Su Windows pulito (senza Rust/MSVC): `pip install --find-links <vendor> neuron-*.whl`
  installa **senza compilare** → `python -m neuron` e `neuron-mcp` partono.
- Ramo fallback testato: forzando l'assenza di wheel, l'installer mette **solo** MSVC
  minimale e completa.
- CI verde su `ci.yml`; `release.yml` testato su un tag di prova (es. `v3.3.0-rc1`).
- `TASKLIST.md` aggiornato (regola di progetto: chat ↔ TASKLIST.md allineati).
