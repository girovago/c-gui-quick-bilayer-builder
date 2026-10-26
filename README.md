# cgui-studio

Layer **CLI + API** sopra l'API REST ufficiale di **CHARMM-GUI** (Quick Bilayer):
costruzione di sistemi di membrana in **batch**, con **provenance riproducibile**
(`manifest.json` + sha256 + archivio immutabile).

La GUI web (FastAPI + Svelte, vista 3D NGL in ogni step) arriverà **sopra** questo
core: qui solo modelli, client API, store SQLite e CLI, tutti importabili.

## Stato

| Componente | Stato |
|---|---|
| `client.py` — login, submit, status, download, wait, retry | fatto, 62 test |
| `models.py` — `Spec` (query/form verificati), `JobStatus`, `JobRecord` | fatto |
| `archive.py` — normalizzazione gzip con coda ASCII, estrazione sicura | fatto |
| `store.py` — SQLite (WAL) dei job, UNIQUE su `jobid` | fatto |
| `spec.py` — TOML singolo/multiplo con errori leggibili | fatto |
| `cli.py` — `login build batch status fetch ls doctor` | fatto |
| GUI (FastAPI + Svelte 5 + NGL) | da fare |

## Setup

```bash
uv sync --all-extras

uv run cgui doctor            # ambiente, token, raggiungibilità API
uv run cgui login             # password dal prompt (o CGUI_PASS)
uv run cgui build spec.toml --wait
uv run cgui batch batch.toml --wait --max-parallel 2
uv run cgui status <JOBID>    # oppure: cgui status --all
uv run cgui fetch <JOBID>
uv run cgui ls
```

Test e lint:

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
```

## File spec (TOML)

Un blocco `[build]` o più blocchi `[[build]]`:

```toml
[[build]]
label = "POPC64"
upper = "POPC=64"
lower = "POPC=64"
margin = 20.0
ion_conc = 0.15
ion_type = "NaCl"

[[build]]
label = "plasma-mammalian"
membtype = "PMm"          # una delle 18 biomembrane predefinite
```

Con `clone_job_from = "<jobid>"` si riparte dalla struttura di un job precedente
(proteina già inserita dal PDB Reader) cambiando la composizione lipidica.

## Due cose da sapere (verificate, non lette)

1. **`ion_conc`/`ion_type` viaggiano in minuscolo.** I nomi maiuscoli della
   documentazione (`Ion_conc`, `Ion_type`) sono **ignorati** dal server: il
   sistema esce sempre NaCl 0.15 M. Dettagli e prove in [SOURCES.md](SOURCES.md).
2. **`run_ffconverter=true` è obbligatorio** per avere gli input GROMACS.
   Senza, il server restituisce solo `membuilder` e niente `topol.top`/`*.mdp`.

## Dove finiscono i file

- token: `~/.cache/cgui-studio/token.json` (0600)
- database job: `~/.local/share/cgui-studio/jobs.db`
- risultati: `./results/YYYY-MM-DD_<label>_<jobid>/{system/, job_<jobid>.tar.gz, manifest.json}`

Override per test o home alternative: `CGUI_STUDIO_HOME`.
Password non interattiva: `CGUI_PASS`.

## Licenza

MIT. Vedi [SOURCES.md](SOURCES.md) per le attribuzioni e per l'elenco dei
progetti esaminati (nessun codice copyleft è stato copiato).
