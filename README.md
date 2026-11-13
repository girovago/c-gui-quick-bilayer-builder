# C-gui Quick Bilayer Builder — v2.0-alpha

Personal laboratory for **drug design / drug discovery**: molecular structure, molecular dynamics (MD) and CADD.

> **AI transparency:** this project was written and reviewed with the assistance of large language models (LLMs) — it is only fair that users know. The code has been verified with unit tests (186 tests) and a scientific audit of the lipid builder, but for production systems (long MD runs, publications) it should always be validated against your own data and simulations.

Contains:

- **C-gui Quick Bilayer Builder** (`cgui-studio` on disk) — local web GUI (FastAPI + Svelte 5 + Tailwind + vendored NGL) over the official CHARMM-GUI REST API (Quick Bilayer). Batch construction of lipid compositions with provenance manifests and GROMACS-ready output.
- **scripts/make_bilayer.py** — local **all-atom molecular builder** (no CHARMM-GUI server needed): builds bilayers, systems with proteins, nucleic acids (DNA/RNA), small molecules and small complexes, and runs GROMACS directly (editconf, solvate, grompp, mdrun). **v2.0: build progress step-by-step with live UI feedback, pdb2gmx fixed for multi-species membranes, Windows 10/11 support.**

---

## Table of contents

1. [Project structure](#project-structure)
2. [Web GUI (cgui-studio)](#web-gui-cgui-studio)
3. [Local all-atom builder (`scripts/make_bilayer.py`)](#local-all-atom-builder-scriptsmake_bilayerpy)
4. [Molecule support: lipids, proteins, nucleic acids, small molecules](#molecule-support)
5. [Native GROMACS output and version detection](#native-gromacs-output-and-version-detection)
6. [Setup and installation](#setup-and-installation)
7. [Tests and verification](#tests-and-verification)
8. [Sources and licenses](#sources-and-licenses)

---

## Project structure

```
cgui-studio/
├── scripts/make_bilayer.py          # local all-atom builder (GROMACS-ready)
├── src/cgui_studio/                 # core CLI + web GUI
│   ├── client.py                    # CHARMM-GUI API client (login, submit, status, download, wait)
│   ├── models.py                    # Spec, JobStatus, JobRecord
│   ├── paths.py                     # XDG paths, token cache, store
│   ├── store.py                     # SQLite WAL job store
│   ├── spec.py                      # single/multiple TOML spec
│   ├── webui.py                     # FastAPI app (Quick Bilayer wizard, NGL, live jobs, mock-first)
│   └── webmock.py                   # FakeClient for offline tests (mock-first)
├── web/                             # Svelte 5 + Tailwind + vendored NGL frontend
│   ├── src/
│   │   ├── App.svelte               # Singolo/Batch wizard, job table, side-by-side 3D comparison
│   │   ├── Molecule.svelte          # NGL 3D view with style controls, reset, water/ions
│   │   ├── SpecSketch.svelte        # real-time lipid composition schema
│   │   ├── BatchPanel.svelte        # sequential multi-composition queue
│   │   ├── Launcher.svelte          # all-atom builder: 4-step wizard, presets, metrics, logs, save/load JSON
│   │   └── api.js                   # API calls
│   └── public/vendor/ngl.js         # NGL View 2.5.0 MIT, vendored (no CDN)
├── tests/                           # pytest (186 tests)
├── results/                         # job outputs (one per job)
├── README.md
├── SOURCES.md
```

---

## Web GUI — C-gui Quick Bilayer Builder

Local web app at `http://127.0.0.1:8756` (configurable port):

```bash
uv run cgui serve --port 8756
# or in MOCK mode (no CHARMM-GUI server needed, offline testing):
CGUI_MOCK=1 uv run cgui serve --port 8756
```

Features:

- **Quick Bilayer wizard** (Singolo tab): lipid composition (`POPC=64`, `POPC=48;CHOL=16`), margin, wdist, ions (KCl/NaCl), membrane_only, run_ffconverter
- **All-atom builder launcher** (4 steps: Type → Composition → Environment → Summary): 411 CHARMM36 lipid catalog with live search and category filters, symmetric or asymmetric leaflets, membrane presets (1 click), physical controls (box, centering, solvent, ions), post-build metrics (P-P thickness, APL, water/lipid), GROMACS logs, save/load composition as JSON
- **Batch multi-composition** (Batch tab): local sequential queue (one row at a time), per-row states, optional auto-fetch, stop/resume, wizard pre-fill initialization
- **Build progress step-by-step** (v2.0): when a local GROMACS build starts, a panel shows each pipeline step — preparation → pdb2gmx → editconf → solvate → genion → grompp → metrics → completed — with green checkmarks on finished steps, the active step highlighted with a live label ("sto solvatando la membrana…") and elapsed time, and "Completato in NNs" at the end. **No infinite spinner.** Polling pauses when the tab is hidden.
- **Responsive layout** (v2.0): stepper labels always visible, grid and dropdowns adapt to 16:9 full screen and narrow side-by-side panes (e.g. OpenCode preview).
- **NGL 3D view** with spacefill, ball+stick, licorice, cartoon; water/ions toggle; reset view; mouse hints; side-by-side comparison of two structures
- SpecSketch: live composition diagram
- NGL vendored in `web/public/vendor/` (no CDN, no external dependencies)

---

## Local all-atom builder (`scripts/make_bilayer.py`)

Builds molecular systems **locally**, without a CHARMM-GUI server and without queue waiting. Output ready for GROMDS (`.pdb`, `.top`, `.mdp`) and ready to load in visualizers (NGL, PyMOL, VMD).

### Command

```bash
# from the cgui-studio directory:
python3 scripts/make_bilayer.py --spec "POPC=64;CHOL=16" --run-gmx
```

or with presets:

```bash
python3 scripts/make_bilayer.py --preset plasma --box-x 10 --box-y 10 --box-z 10 --run-gmx
```

or independent leaflets:

```bash
python3 scripts/make_bilayer.py --upper "POPC=14;POPE=6;PSM=8;CHOL=20;POPS=4;POPI=2;DOPS=2" --lower "POPC=14;POPE=6;PSM=8;CHOL=20;POPS=4;POPI=2;DOPS=2" --run-gmx
```

### CLI arguments

| Argument | Default | Meaning |
|----------|---------|---------|
| `--spec` | — | Composition e.g. `POPC=64;CHOL=16` (both leaflets) |
| `--upper` | — | Upper leaflet composition |
| `--lower` | — | Lower leaflet composition |
| `--membtype` | — | Server membrane preset (ignores upper/lower) |
| `--preset` | — | Local preset: `plasma`, `er`, `mito`, `popc` (ignores spec/upper/lower) |
| `--margin` | 20.0 | Margin around system (Å) |
| `--wdist` | 1.0 | Headgroup-ion distance (Å) |
| `--ion-conc` | 0.15 | Salt concentration (M) |
| `--ion-type` | KCl | Salt type (`KCl`, `NaCl`) |
| `--box-x` | 12.0 | Box X dimension (nm) |
| `--box-y` | 12.0 | Box Y dimension (nm) |
| `--box-z` | 12.0 | Box Z dimension (nm) |
| `--center` / `--no-center` | centered | Centers structure at (0,0,0) |
| `--layout` | fluid | `fluid` (random mosaic, scientific default) or `planar` (CLI debug) |
| `--solvent` | water | `water` (TIP3P) or `none` |
| `--ions` | KCl | `KCl`, `NaCl` or `none` |
| `--output` | results | Base output directory |
| `--run-gmx` | — | Runs GROMACS: `pdb2gmx` (full CHARMM36 topology) → `editconf` → solvate → genion → grompp. Minimization mdrun is opt-in (`--minimize`) because on a laptop it can exceed 5 min |
| `--minimize` / `--min-timeout` | off / 900 | Also runs `gmx mdrun` (steep) with timeout in seconds |
| `--build-type` | lipid | `lipid`, `protein`, `dna`, `rna`, `small-mol` |
| `--pdb` | — | PDB ID (e.g. `7CFN`) or PDB file: protein is oriented, pre-processed with `gmx pdb2gmx` (CHARMM36) and centered in the bilayer (module `scripts/embed_protein.py`) |
| `--pdb-chain` | all | Chain(s) to keep (e.g. `R` for GPBAR1 in 7CFN) |
| `--pdb-prefer` | OPM | Orientation: `OPM`, `PPM`, `none` (passthrough: TM domain mid-plane at z=0) |
| `--pdb-fix-missing` | off | Converts incomplete side chains to GLY (typical cryo-EM) so pdb2gmx passes |
| `--dna` | — | DNA sequence (e.g. ATGC) |
| `--rna` | — | RNA sequence (e.g. AUGC) |
| `--small-mol` | — | PDB/SDF small molecule file |

### Scientific features

- **Real lateral mixing**: species are randomly assigned to leaflet positions (reproducible seed), not in contiguous sectors — like a real membrane
- **Real APL**: area per lipid per composition (POPC 0.683, DOPC 0.725, CHL1 0.28, PSM 0.53, TOCL 1.20 nm²…) to size leaflets with physical density
- **Charge balancing**: net charge from lipids (via CHARMM36 lipid.rtp) and automatic ion addition via `gmx genion` (neutralization + target concentration)
- **Dedicated sterane geometries**: cholesterol CHL1 with 4 fused rings (OH toward water, chain toward core), not generic chains
- **PDB consistent with .top**: atom names from lipid.rtp (avoids grompp mismatch)
- **Volumetric P-P thickness**: D_B calculated from CHARMM36 lipid volumes with sterol correction (×1.2), not fixed constants — verified POPC 3.78 nm, POPC+CHL1 3.95 nm (experimental range 3.7–4.1)
- **Protein in bilayer (`--pdb`)**: OPM/PPM/passthrough orientation, `pdb2gmx` with charmm36 + cmap terms, split topology per chain, real net charge read from .itp and compensated by genion (8 extra Cl⁻ for GPBAR1 +8 e), TM domain centered at z=0, `[ molecules ]` with protein on first line (order consistent with PDB)
- **"grompp-ready" run**: without `--minimize` the GROMACS flow ends with `minim.tpr` in ~2 min on a laptop (E7440), with `metrics.json` always written
- **pdb2gmx multi-species fix (v2.0)**: one PDB chain per lipid species (POPC→B, CHL1→C…) so pdb2gmx generates one `[moleculetype]` per species instead of fusing all lipids into a single `Other_chain_A`; `[molecules]` counts are preserved from pdb2gmx (only SOL/NA/CL are recounted from the `.gro`). This fixes the fatal grompp error *"number of coordinates in coordinate file does not match topology"* on membranes with more than one lipid species. Verified end-to-end: POPC=64/64 + water + KCl (128 lipids) → `minim.tpr` OK; POPC+CHL1 mixed → OK.
- **Build progress (v2.0)**: `ProgressWriter` writes `progress.json` atomically in the job `system/` dir; the web UI polls `GET /api/jobs/{id}/progress` every 1s (paused when tab hidden) and renders step chip panels with checkmarks, active-step label and elapsed time.

### Native GROMACS output

| File | Content |
|------|---------|
| `system/step1_pdbreader.pdb` | Bilayer/system structure (PDB; atomic coordinates in **Å**, PDB standard) |
| `system/topol.top` | GROMACS topology (CHARMM36) with position restraints on polar heads |
| `system/minim.mdp` | Energy minimization parameters (steepest descent) |
| `system/boxed.gro` | Structure with box (after `gmx editconf`) |
| `system/solvated.gro` | Hydrated system (water generated **only in the water regions**, geometric exclusion of the hydrophobic core) |
| `system/ionized.gro` | System with ions added (after `gmx genion`) |
| `system/minim.tpr` | Input ready for simulation (after `gmx grompp`) |
| `system/minim.trr/.gro/.edr/.log` | Minimization output (after `gmx mdrun`) |
| `system/equilib_nvt.mdp` | NVT equilibration 50 ps with posres |
| `system/equilib_npt.mdp` | NPT equilibration 50 ps semiisotropic with posres |
| `system/production.mdp` | Free production 100 ns |
| `system/equil.sh` | Shell script to run NVT → NPT → production |
| `results/…/manifest.json` | SHA256 of each file for provenance |
| `results/…/metrics.json` | Post-build metrics (P-P thickness, APL, water/lipid, charge) |

When `--run-gmx` is specified, the script automatically executes:

1. `gmx pdb2gmx -ff charmm36 -water tip3p -ignh` — generates the full topology (`[bonds]`, `[angles]`, `[dihedrals]`, `[cmap]`) from PDB + charmm36 jul2022 (if it fails, proceeds with partial topology and warning banner)
2. `gmx editconf` — centers and sets box
3. **Region-based hydration** — water generated only in the water regions above/below the membrane (geometric exclusion of the hydrophobic core; TIP3P geometry, ~33.4 molecules/nm³, clash-checked against lipids). Every ATOM carries explicit CONECT records from the CHARMM36 rtp bonds, so molecular viewers show intra-lipid bonds only
4. `gmx grompp` (pre-ions) — prepares input for genion
5. `gmx genion` — adds ions (neutralization + concentration)
6. geometry guard: NaN/clash checks on intermediate files before continuing
7. `gmx grompp` — prepares minimization input
8. `gmx mdrun` — runs energy minimization

**Validation**: `VALIDATION.md` documents measured-vs-expected metrics (P–P
thickness, APL, ion counts, water-in-core fraction) with reproduction
commands, plus the automated geometry sweep over all 411 CHARMM36 lipid
residues (CI regression test).

### GROMACS version detection

The script automatically detects the installed GROMACS version (`gmx --version`) and:

- Shows the detected version
- Checks compatibility (minimum 2018 recommended)
- Warns if the version is very new or too old

### Persistent resources

The `lipid.rtp` file (CHARMM36) and the `charmm36.tgz` tarball (force field) are stored in the data dir (not in `/tmp`, which is cleared on reboot). They are NOT downloaded automatically — the MacKerell license requires registration.

Obtain `charmm36.tgz` from https://mackerell.umass.edu and install it with:

    cgui setup-ff --from <percorso/charmm36.tgz>

Data dir location:
- Linux/macOS: ``$XDG_DATA_HOME/cgui-studio`` or ``~/.local/share/cgui-studio``
- Windows: ``%LOCALAPPDATA%\\cgui-studio``
- Override: env var ``CGUI_DATA_DIR``

---

## Setup and installation

### Prerequisites

- **Python 3.12+** (recommended: miniforge/conda in a dedicated environment, NOT system pip)
- **GROMACS 2018+** (optional, for local simulation execution)
- **Node.js 20+** (for the frontend, optional if you only use the Python script)

### Python environment installation (conda/miniforge)

```bash
# Install miniforge if not present
curl -L -o miniforge.sh https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh
bash miniforge.sh -b -p ~/miniforge3

# Create dedicated environment
~/miniforge3/bin/conda create -n cgui-builder python=3.12 -y
~/miniforge3/bin/conda activate cgui-builder

# Install dependencies
~/miniforge3/bin/conda install -c conda-forge gromacs mdtraj MDAnalysis -y
```

### Project installation

```bash
cd cgui-studio
uv run python scripts/make_bilayer.py --help
```

### Windows (PowerShell / cmd)

1. Prerequisiti:
   - `winget install Git.Git astral-sh.uv OpenJS.NodeJS.LTS`
2. Clona il repo:
   - `git clone <url-repo> && cd cgui-studio`
3. Ambiente e dipendenze:
   - `uv sync --all-extras`
4. Test (quelli che richiedono gmx/lipid.rtp si saltano da soli se mancanti):
   - PowerShell: `$env:CGUI_MOCK="1"; uv run pytest -q`
   - cmd: `set CGUI_MOCK=1 && uv run pytest -q`
5. Force field CHARMM36 (richiesto per le analisi scientifiche):
   - `uv run cgui setup-ff --from <percorso\charmm36.tgz>`
6. Avvia la GUI:
   - `.\run.bat` (apre http://127.0.0.1:8756; demo offline: `set CGUI_MOCK=1` prima di lanciare)
   - GROMACS nativo non esiste su Windows (conda-forge non lo distribuisce): per `--run-gmx` usare WSL2.

For the web GUI:

```bash
cd web && npm install
npm run build
cd ..
CGUI_MOCK=1 uv run cgui serve --port 8756
```

---

## Tests and verification

```bash
cd cgui-studio

# Python tests
uv run pytest -q

# Lint
uv run ruff check .

# Frontend build
cd web && npm run build
cd ..

# Local builder test (charged membrane, tests genion + metrics)
python3 scripts/make_bilayer.py --upper "POPC=8;POPS=8" --lower "POPC=8;POPS=8" --box-x 9 --box-y 9 --box-z 9 --run-gmx

# Plasma preset test
python3 scripts/make_bilayer.py --preset plasma --box-x 10 --box-y 10 --box-z 10 --run-gmx
```

---

## Sources and licenses

See [`SOURCES.md`](SOURCES.md).

### Main licenses

- **cgui-studio**: MIT
- **C-gui Quick Bilayer Builder**: version 2.0-alpha
- **NGL View**: MIT (vendored in `web/public/vendor/`)
- **CHARMM-GUI**: academic use, citation required
- **CHARMM36**: free academic use with registration (MacKerell Lab)

---

## Important notes

- The public CHARMM-GUI documentation covers only General + Quick Bilayer. PDB Reader is not publicly documented but is required for embedding proteins.
- The CHARMM-GUI token expires: re-run `cgui login` if you get error 403.
- For production calculations (long MD runs), use a cluster or dedicated server: this tool is optimized for small systems and quick tests on a local workstation.

---

## Versione italiana (sintesi) 🇮🇹

**C-gui Quick Bilayer Builder v2.0-alpha** — laboratorio personale di drug design: builder all-atom di bilayer **in locale** (GROMACS-ready) + GUI web sopra l'API ufficiale di CHARMM-GUI.

- **Builder locale** (`scripts/make_bilayer.py`): bilayer, sistemi con proteine (orientamento OPM/PPM), DNA, RNA, small molecules → `.pdb` (in Å), `topol.top` CHARMM36 completo via `gmx pdb2gmx`, `minim.tpr` pronto, mdp NVT/NPT/produzione.
- **GUI web**: wizard Quick Bilayer, catalogo 411 lipidi CHARMM36, preset membrane, batch sequenziale, vista 3D NGL (vendored, no CDN), confronto affiancato, metriche post-build (spessore P-P, APL, carica).
- **v2.0**: feedback di avanzamento step-per-step durante la build locale (niente spinner infinito), fix del doppio conteggio in `grompp` su membrane multi-specie, porting Windows 10/11 (PowerShell, senza WSL).
- **Scienza**: mescolamento laterale reale, APL sperimentali, bilanciamento cariche automatico con `genion`, geometria steranica dedicata per il colesterolo.
- **Progetto scritto con assistenza LLM** — verificato con 186 test unitari e audit scientifico; valida sempre con i tuoi dati prima dell'uso in produzione.

> Attualmente cerco un dottorato finanziato o un lavoro in CADD/drug discovery (Europa o Italia) — contattami pure! 😊
