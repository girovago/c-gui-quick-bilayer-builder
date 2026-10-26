# cgui-studio

Layer **CLI + API** sopra l'API REST ufficiale di **CHARMM-GUI** (Quick Bilayer):
costruzione di sistemi di membrana in **batch**, con **provenance riproducibile**
(`manifest.json` + sha256 + archivio immutabile).

La GUI web (FastAPI + Svelte 5, vista 3D NGL in ogni step) **c'è**: `cgui serve`,
mock-first, sopra questo core: qui solo modelli, client API, store SQLite e CLI, tutti importabili.

## Stato

| Componente | Stato |
|---|---|
| `client.py` — login, submit, status, download, wait, retry | fatto, 62 test |
| `models.py` — `Spec` (query/form verificati), `JobStatus`, `JobRecord` | fatto |
| `archive.py` — normalizzazione gzip con coda ASCII, estrazione sicura | fatto |
| `store.py` — SQLite (WAL) dei job, UNIQUE su `jobid` | fatto |
| `spec.py` — TOML singolo/multiplo con errori leggibili | fatto |
| `cli.py` — `login build batch status fetch ls doctor` | fatto |
| `webui.py` — GUI FastAPI (wizard Quick Bilayer, NGL, job live, mock-first) | fatto |

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

## GUI web (MVP)

App locale su `http://127.0.0.1:8756`: wizard Quick Bilayer (composizione
lipidica con validazione live, parametri, payload di debug), **vista 3D NGL**
del PDB estratto (across/i d'acqua e ioni nascosti, toggle) e lista job live
con dettaglio (stato, coda, step, tar) + `Scarica e estrai` + `Clona nel form`
(prefill client-side; il clone server-side via `clone_job` resta disattivo
nell'MVP). Tema scuro, nessuna dipendenza UI oltre a Tailwind.

Avvio (produzione, build compilate una volta):

```bash
# build frontend (una tantum, Node >= 20):
cd web && npm i && npm run build && cd ..

# backend + static:
uv run cgui serve --port 8756            # usa il token reale in cache
CGUI_MOCK=1 uv run cgui serve --port 8756  # mock: nessuna rete/token necessari
```

Sviluppo frontend (hot reload):

```bash
uv run cgui serve --port 8756 &
cd web && npm run dev   # http://localhost:5173, proxy /api → :8756
```

In **mock** (`CGUI_MOCK=1`) il client finto risponde in ~8s (pending → running
@3s → done @8s) e genera un bilayer PDB di prova (~10k atomi, 2 leaflets a
z=±2 nm): utile per sviluppare e per i test senza account né coda reale.

- API: `GET /api/ping`, `POST/GET /api/jobs`, `GET /api/jobs/{id}`,
  `POST /api/jobs/{id}/fetch` (`{"wait": true}` per attendere), `GET
  /api/files/{id}` e `/api/files/{id}/{path..}` (text/plain per testo,
  octet-stream per binari), `POST /api/form-preview`.
- Errori: 404 job non noto/non estratto · 409 token assente
  (messaggio: esegui `uv run cgui login`) o job non pronto · 422 spec invalida ·
  502 errori API a monte.

## Licenza

MIT. Vedi [SOURCES.md](SOURCES.md) per le attribuzioni e per l'elenco dei
progetti esaminati (nessun codice copyleft è stato copiato).
