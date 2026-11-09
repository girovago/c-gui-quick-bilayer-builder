# C-gui Quick Bilayer Builder

Laboratorio personale di **drug design / drug discovery**: struttura molecolare, dinamica molecolare (MD) e CADD.

Contiene:

- **C-gui Quick Bilayer Builder** (`cgui-studio` su disco) — GUI web locale (FastAPI + Svelte 5 + Tailwind + NGL vendored) sopra l'API REST ufficiale di CHARMM-GUI (Quick Bilayer). Costruzione batch di composizioni lipidiche con manifest di provenance e output GROMACS pronto.
- **scripts/make_bilayer.py** — builder molecolare **all-atom in locale** (no server CHARMM-GUI necessario): costruisce bilayer, sistemi con proteine, acidi nucleici (DNA/RNA), small molecules e piccoli complessi, ed esegue direttamente GROMACS (editconf, solvate, grompp, mdrun).

---

## Indice

1. [Struttura del progetto](#struttura-del-progetto)
2. [GUI web (cgui-studio)](#gui-web-cgui-studio)
3. **Builder all-atom locale (`scripts/make_bilayer.py`) — NUOVO**
4. **Supporto molecole: lipidi, proteine, acidi nucleici, small molecules — NUOVO**
5. **Output GROMACS nativo e rilevamento versione — NUOVO**
6. [Setup e installazione](#setup-e-installazione)
7. [Verifica e test](#verifica-e-test)
8. [Fonti e licenze](#fonti-e-licenze)

---

## Struttura del progetto

```
cgui-studio/
├── scripts/make_bilayer.py          # builder all-atom locale (GROMACS-ready)
├── src/cgui_studio/                 # core CLI + web GUI
│   ├── client.py                    # client API CHARMM-GUI (login, submit, status, download, wait)
│   ├── models.py                    # Spec, JobStatus, JobRecord
│   ├── paths.py                     # percorsi XDG, token cache, store
│   ├── store.py                     # SQLite WAL per job
│   ├── spec.py                      # TOML singolo/multiplo
│   ├── webui.py                     # app FastAPI (wizard Quick Bilayer, NGL, job live, mock-first)
│   └── webmock.py                   # FakeClient per test offline (mock-first)
├── web/                             # frontend Svelte 5 + Tailwind + NGL vendored
│   ├── src/
│   │   ├── App.svelte               # wizard Singolo/Batch, tabella job, confronto 3D affiancato
│   │   ├── Molecule.svelte          # vista 3D NGL con controlli stile, reset, acqua/ioni
│   │   ├── SpecSketch.svelte        # schema composizione lipidi in tempo reale
│   │   ├── BatchPanel.svelte        # coda multi-composizione sequenziale
│   │   ├── Launcher.svelte          # builder all-atom: 4-step wizard, preset, metriche, log, salva/carica JSON
│   │   └── api.js                   # chiamate API
│   └── public/vendor/ngl.js         # NGL View 2.5.0 MIT, vendored (no CDN)
├── tests/                           # pytest (117 test)
├── results/                         # output dei job (uno per job)
├── literature/                      # PDF e note bibliografiche
├── README.md
├── SOURCES.md
└── AGENTS.md                        # istruzioni per OpenCode
```

---

## GUI web — C-gui Quick Bilayer Builder

App web locale su `http://127.0.0.1:8756` (porta configurabile):

```bash
uv run cgui serve --port 8756
# oppure in modalità MOCK (nessun server CHARMM-GUI richiesto, test offline):
CGUI_MOCK=1 uv run cgui serve --port 8756
```

Funzionalità:

- **Wizard Quick Bilayer** (tab Singolo): composizione lipidi (`POPC=64`, `POPC=48;CHOL=16`), margine, wdist, ioni (KCl/NaCl), membrane_only, run_ffconverter
- **Launcher builder all-atom** (4 step: Tipo → Composizione → Ambiente → Riepilogo): catalogo 411 lipidi CHARMM36 con ricerca live e filtri per categoria, foglietti simmetrici o asimmetrici, preset membrane (1 click), controlli fisici (box, centratura, solvente, ioni), metriche post-build (spessore P-P, APL, acqua/lipide), log GROMACS, salva/carica composizione in JSON
- **Batch multi-composizione** (tab Batch): coda locale sequenziale (una riga alla volta), stati per riga, auto-fetch opzionale, stop/riprendi, inizializzazione pre-compilata dal wizard
- **Vista 3D NGL** con spacefill, ball+stick, licorice, cartoon; toggle acqua/ioni; reimposta vista; hint mouse; confronto affiancato di due strutture
- SpecSketch: schema composizione live
- NGL vendored in `web/public/vendor/` (no CDN, nessuna dipendenza esterna)

---

## Builder all-atom locale (`scripts/make_bilayer.py`)

Costruisce sistemi molecolari **in locale**, senza server CHARMM-GUI e senza attesa in coda. Output pronto per GROMACS (`.pdb`, `.top`, `.mdp`) e pronto per caricamento in visualizzatori (NGL, PyMOL, VMD).

### Comando

```bash
# dalla directory cgui-studio:
python3 scripts/make_bilayer.py --spec "POPC=64;CHOL=16" --run-gmx
```

oppure con preset:

```bash
python3 scripts/make_bilayer.py --preset plasma --box-x 10 --box-y 10 --box-z 10 --run-gmx
```

oppure foglietti indipendenti:

```bash
python3 scripts/make_bilayer.py --upper "POPC=14;POPE=6;PSM=8;CHOL=20;POPS=4;POPI=2;DOPS=2" --lower "POPC=14;POPE=6;PSM=8;CHOL=20;POPS=4;POPI=2;DOPS=2" --run-gmx
```

### Argomenti CLI

| Argomento | Valore default | Significato |
|-----------|---------------|-------------|
| `--spec` | — | Composizione es. `POPC=64;CHOL=16` (entrambi i fogli) |
| `--upper` | — | Composizione foglio superiore |
| `--lower` | — | Composizione foglio inferiore |
| `--membtype` | — | Preset membrane del server (ignora upper/lower) |
| `--preset` | — | Preset locale: `plasma`, `er`, `mito`, `popc` (ignora spec/upper/lower) |
| `--margin` | 20.0 | Margin attorno al sistema (Å) |
| `--wdist` | 1.0 | Distanza teste-ioni (Å) |
| `--ion-conc` | 0.15 | Concentrazione sale (M) |
| `--ion-type` | KCl | Tipo sale (`KCl`, `NaCl`) |
| `--box-x` | 12.0 | Dimensione box X (nm) |
| `--box-y` | 12.0 | Dimensione box Y (nm) |
| `--box-z` | 12.0 | Dimensione box Z (nm) |
| `--center` / `--no-center` | centrato | Centra la struttura a (0,0,0) |
| `--layout` | fluid | `fluid` (mosaico casuale, scienza di default) o `planar` (debug CLI) |
| `--solvent` | water | `water` (acqua TIP3P) o `none` |
| `--ions` | KCl | `KCl`, `NaCl` o `none` |
| `--output` | results | Directory base output |
| `--run-gmx` | — | Esegue GROMACS: `pdb2gmx` (topologia completa CHARMM36) → `editconf` → solvate → genion → grompp. La minimizzazione mdrun è opt-in (`--minimize`) perché su laptop può superare i 5 min |
| `--minimize` / `--min-timeout` | off / 900 | Esegue anche `gmx mdrun` (steep) con timeout in secondi |
| `--build-type` | lipid | `lipid`, `protein`, `dna`, `rna`, `small-mol` |
| `--pdb` | — | ID PDB (es. `7CFN`) o file PDB: la proteina viene orientata, pre-processata con `gmx pdb2gmx` (CHARMM36) e inserita al centro del bilayer (modulo `scripts/embed_protein.py`) |
| `--pdb-chain` | tutte | Chain da mantenere (es. `R` per GPBAR1 in 7CFN) |
| `--pdb-prefer` | OPM | Orientamento: `OPM`, `PPM`, `none` (passthrough: il mid del dominio TM va su z=0) |
| `--pdb-fix-missing` | off | Converte a GLY i residui con side chain incompleta (tipico cryo-EM) per far passare pdb2gmx |
| `--dna` | — | Sequenza DNA (es. ATGC) |
| `--rna` | — | Sequenza RNA (es. AUGC) |
| `--small-mol` | — | File PDB/SDF di una small molecule |

### Caratteristiche scientifiche

- **Mescolamento laterale reale**: le specie sono assegnate casualmente alle posizioni nel foglietto (seed riproducibile), non a settori contigui — come in una membrana vera
- **APL reali**: area per lipide per composizione (POPC 0.683, DOPC 0.725, CHL1 0.28, PSM 0.53, TOCL 1.20 nm²…) per dimensionare i foglietti con densità fisica
- **Bilanciamento cariche**: calcolo carica netta dai lipidi (tramite lipid.rtp CHARMM36) e aggiunta automatica di ioni via `gmx genion` (neutralizzazione + concentrazione target)
- **Geometrie sterane dedicate**: colesterolo CHL1 con 4 anelli fused (OH verso acqua, catena verso core), non catene generiche
- **PDB coerente col .top**: nomi atomi da lipid.rtp (evita mismatch grompp)
- **Spessore P-P volumetrico**: D_B calcolato dai volumi CHARMM36 dei lipidi con correzione sterolo (×1.2), non da costanti fisse — verificato POPC 3.78 nm, POPC+CHL1 3.95 nm (range sperimentale 3.7–4.1)
- **Proteina nel bilayer (`--pdb`)**: orientamento OPM/PPM/passthrough, `pdb2gmx` con termini chamati charmm36 + `cmap`, split topology per chain, carica netta reale letta dal .itp e compensata da genion (8 CL⁻ extra per GPBAR1 +8 e), centraggio del dominio TM su z=0, `[ molecules ]` con proteina in prima riga (ordine coerente col PDB)
- **Run "grompp-ready" pronto**: senza `--minimize` il flusso GROMACS termina con `minim.tpr` in ~2 min su laptop (E7440), con `metrics.json` sempre scritta

### Output GROMACS nativo

| File | Contenuto |
|------|-----------|
| `system/step1_pdbreader.pdb` | Struttura bilayer/sistema (PDB; coordinate atomiche in **Å**, standard PDB) |
| `system/topol.top` | Topologia GROMACS (CHARMM36) con position_restraints sulle teste polari |
| `system/minim.mdp` | Parametri minimizzazione energia (steepest descent) |
| `system/boxed.gro` | Struttura con box (dopo `gmx editconf`) |
| `system/solvated.gro` | Sistema solvatato (dopo `gmx solvate`) |
| `system/ionized.gro` | Sistema con ioni aggiunti (dopo `gmx genion`) |
| `system/minim.tpr` | Input pronto per la simulazione (dopo `gmx grompp`) |
| `system/minim.trr/.gro/.edr/.log` | Output minimizzazione (dopo `gmx mdrun`) |
| `system/equilib_nvt.mdp` | Equilibratura NVT 50 ps con posres |
| `system/equilib_npt.mdp` | Equilibratura NPT 50 ps semiisotropa con posres |
| `system/production.mdp` | Produzione 100 ns libera |
| `system/equil.sh` | Script shell per eseguire NVT → NPT → produzione |
| `results/…/manifest.json` | SHA256 di ogni file per provenance |
| `results/…/metrics.json` | Metriche post-build (spessore P-P, APL, acqua/lipide, carica) |

Se `--run-gmx` è specificato, lo script esegue automaticamente:

1. `gmx pdb2gmx -ff charmm36 -water tip3p -ignh` — genera la topologia completa
   (`[bonds]`, `[angles]`, `[dihedrals]`, `[cmap]`) dal PDB + charmm36 jul2022
   (se fallisce, si procede con un top parziale e banner di avviso)
2. `gmx editconf` — centra e imposta la box
3. `gmx solvate` — aggiunge acqua TIP3P
4. `gmx grompp` (pre-ioni) — prepara input per genion
5. `gmx genion` — aggiunge ioni (neutralizzazione + concentrazione)
6. `gmx grompp` — prepara l'input per la minimizzazione
7. `gmx mdrun` — esegue la minimizzazione energetica

### Rilevamento versione GROMACS

Lo script rileva automaticamente la versione di GROMACS installata (`gmx --version`) e:
- Mostra la versione rilevata
- Verifica compatibilità (minimo 2018 raccomandato)
- Avvisa se la versione è molto recente o troppo vecchia

### Risorse persistenti

Il file `lipid.rtp` (CHARMM36) e il tarball `charmm36.tgz` (force field) sono memorizzati in `~/.local/share/cgui-studio/` (non in `/tmp`, che viene svuotato al riavvio). Scaricati automaticamente al primo uso se mancanti.

---

## Setup e installazione

### Prerequisiti

- **Python 3.12+** (consigliato: miniforge/conda in ambiente dedicato, NON pip di sistema)
- **GROMACS 2018+** (opzionale, per esecuzione locale delle simulazioni)
- **Node.js 20+** (per il frontend, opzionale se usi solo lo script Python)

### Installazione ambiente Python (conda/miniforge)

```bash
# Installa miniforge se non presente
curl -L -o miniforge.sh https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh
bash miniforge.sh -b -p ~/miniforge3

# Crea ambiente dedicato
~/miniforge3/bin/conda create -n cgui-builder python=3.12 -y
~/miniforge3/bin/conda activate cgui-builder

# Installa dipendenze
~/miniforge3/bin/conda install -c conda-forge gromacs mdtraj MDAnalysis -y
```

### Installazione progetto

```bash
cd /home/giorgio/Documenti/cgui-studio
uv run python scripts/make_bilayer.py --help
```

Per la GUI web:

```bash
cd web && PATH=/home/giorgio/.local/node24/bin:$PATH npm install
PATH=/home/giorgio/.local/node24/bin:$PATH npm run build
cd ..
CGUI_MOCK=1 uv run cgui serve --port 8756
```

---

## Verifica e test

```bash
cd /home/giorgio/Documenti/cgui-studio

# Test Python
uv run pytest -q

# Lint
uv run ruff check .

# Build frontend
cd web && PATH=/home/giorgio/.local/node24/bin:$PATH npm run build
cd ..

# Test builder locale (membrana carica, testa genion + metriche)
python3 scripts/make_bilayer.py --upper "POPC=8;POPS=8" --lower "POPC=8;POPS=8" --box-x 9 --box-y 9 --box-z 9 --run-gmx

# Test preset plasma
python3 scripts/make_bilayer.py --preset plasma --box-x 10 --box-y 10 --box-z 10 --run-gmx
```

---

## Fonti e licenze

Vedi [`SOURCES.md`](SOURCES.md).

### Licenze principali

- **cgui-studio**: MIT
- **NGL View**: MIT (vendored in `web/public/vendor/`)
- **CHARMM-GUI**: uso accademico, citazione obbligatoria
- **CHARMM36**: accademico gratuito con registrazione (MacKerell Lab)

---

## Note importanti

- La doc pubblica CHARMM-GUI copre solo General + Quick Bilayer. PDB Reader non è documentato pubblicamente ma è richiesto per embeddare proteine.
- Il token CHARMM-GUI scade: rifare `cgui login` se ricevi errore 403.
- Per calcoli di produzione (MD lunghi), usa un cluster o server dedicato: questo strumento è ottimizzato per sistemi piccoli e test rapidi su workstation locale.
