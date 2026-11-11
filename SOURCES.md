# SOURCES.md — Fonti e riferimenti per cgui-studio

Questo documento elenca le fonti, le licenze e i riferimenti utilizzati nel progetto cgui-studio, inclusi il builder all-atom locale e l'integrazione con CHARMM-GUI.

---

## 1. API CHARMM-GUI

| Fonte | URL | Licenza | Uso |
|-------|-----|---------|-----|
| CHARMM-GUI REST API ufficiale | https://www.charmm-gui.org/?doc=api | Uso accademico, citazione obbligatoria | Quick Bilayer, PDB Reader (non documentato pubblicamente), Membrane Builder |
| CHARMM-GUI Lipid Library | https://charmm-gui.org/?doc=archive&lib=lipid | Licenza non esplicita in pagina, citazione obbligatoria | Template lipidici PDB all-atom |
| CHARMM-GUI Forum | https://charmm-gui.org/forum | Supporto community | Segnalazioni infrastruttura, bug, endpoint non documentati |

**Nota**: La doc pubblica copre solo General + Quick Bilayer. PDB Reader non è documentato pubblicamente ma è richiesto per embeddare proteine.

---

## 2. Force field e template lipidici

| Fonte | URL | Licenza | Uso |
|-------|-----|---------|-----|
| MacKerell Lab CHARMM36 | https://mackerell.umaryland.edu/charmm_ff.shtml | Uso accademico gratuito con registrazione | Force field CHARMM36, top_all36_lipid.rtf |
| Lemkul-Lab toppar_c36_releases | https://github.com/Lemkul-Lab/toppar_c36_releases | Uso accademico, citazione obbligatoria | File `lipid.rtp` CHARMM36 per GROMACS (scaricato da qui, non da link non verificato) |
| LipidBook | https://lipidbook.org/package/policy.html | Licenza per pacchetto (PDDL/CC-BY-SA/fair use) | Database lipidi, licenze per pacchetto |

---

## 3. Strumenti di costruzione membrane (riferimenti)

| Progetto | URL | Licenza | Note |
|----------|-----|---------|------|
| OpenMembraneBuilder | https://github.com/lolicato/OpenMembraneBuilder | AGPL-3.0 | GUI Streamlit, Martini CG only |
| PACKMOL-Memgen | Parte di AmberTools | Licenza AmberTools | All-atom bilayer builder |
| INSANE | https://github.com/afarangia/INSANE | Licenza varia | Membrane CG builder |
| PACKMOL-GUI | Plugin VCF | Licenza varia | Packing molecolare 3D in VCF |

---

## 4. Area per lipide (APL) — riferimenti scientifici

| Riferimento | Contenuto |
|-------------|-----------|
| Kucerka et al. (2011) | Area per lipide di fosfolipidi comuni (POPC, DOPC, DPPC…) a ~303 K |
| Ingólfsson et al. (2014) | Composizione membrane plasmatica mammaliana, organelli; APL per PE, PS, PI, cardiolipina |

Usati per dimensionare i foglietti con densità fisica (`APL_NM2` in `scripts/make_bilayer.py`).

---

## 5. File includi nel progetto

### NGL View (vendored in `web/public/vendor/ngl.js`)

| File | Fonte | Licenza |
|------|-------|---------|
| ngl.js (UMD 2.5.0) | https://github.com/arose/ngl | MIT |
| NGL-LICENSE | https://github.com/arose/ngl/blob/master/LICENSE | MIT |

### Trajectory kit (trajkit)

Progetto sibling (`trajkit`) — CLI per analisi traiettorie MD (info, rmsd, rmsf, rg, sasa, hbonds, ligdist, convert, report).

Backend: mdtraj + MDAnalysis + numpy puro.

Licenza: MIT.

---

## 6. Fonti per il builder all-atom (`scripts/make_bilayer.py`)

Questo progetto **non include** codice copyleft da progetti terzi. I file `lipid.rtp` e `forcefield.itp` di CHARMM36 sono scaricati dal repository ufficiale di Lemkul-Lab (GitHub) o dal sito MacKerell, e inclusi nel progetto solo per uso locale con le relative licenze.

### Fonti consultate:

- **CHARMM-GUI lipid.rtp**: https://github.com/Lemkul-Lab/toppar_c36_releases — repository ufficiale con file `.rtp` per GROMACS (branch `main`, directory `gromacs/charmm36-feb2026_cgenff-5.0.ff/`)
- **MacKerell CGenFF**: https://mackerell.umaryland.edu/charmm_ff.shtml — force field CHARMM36 completo
- **RCSB PDB**: https://www.rcsb.org/ — database strutture proteiche e lipidi (PDB ID per lipidi singoli non sempre disponibili)
- **RCSB PDB Search API**: https://search.rcsb.org/rcsbsearch/v2/query — API per ricerca strutture
- **CHARMM36 jul2022** (`charmm36-jul2022.ff`): distribuzione GROMACS del force field CHARMM36 (file `lipid.rtp`, `lipid.hdb`, `ffbonded.itp`, `forcefield.itp`, `tip3p.itp`, `ions.itp`). Usato da `gmx pdb2gmx` per generare la topologia completa (`[bonds]`, `[angles]`, `[dihedrals]`, `[cmap]`) nel builder all-atom locale.

### Convenzione unità (importante)
Il PDB di output (`step1_pdbreader.pdb`) scrive le coordinate atomiche in **Ångström (Å)**, standard PDB; il calcolo interno del builder resta in nanometri (nm). I consumatori del PDB (`_heavy_clash_count`, `compute_metrics`, embed proteina) dividono per 10 prima dell'uso. I file GROMACS (`.gro`, `.top`, `.mdp`) restano in nm, come richiesto da GROMACS.

---

## 7. Licenze del progetto

| Componente | Licenza | Note |
|------------|---------|------|
| cgui-studio (codice Python, Svelte, documentazione) | MIT | Codice originale di Giorgio Veneruso |
| NGL View (vendored) | MIT | Vendored in `web/public/vendor/` |
| CHARMM-GUI API | Uso accademico | Citazione obbligatoria, citare Park & Im (JMB 2026) |
| CHARMM36 force field | Accademico gratuito | Registrazione richiesta su mackerell.umaryland.edu |
| LipidBook | Per-pacchetto | Vedere lipidbook.org/package/policy.html |
| CHARMM-GUI Lipid Library | Non esplicita | Citazione obbligatoria |

---

## 8. Riferimenti scientifici

- Park, S.J. & Im, W. (2026). CHARMM-GUI Quick Bilayer. *Journal of Molecular Biology*.
- MacKerell, A.D. et al. (2004). All-atom empirical force field for nucleic acids. *J. Comput. Chem.*
- Klauda, J.B. et al. (2010). Update of CHARMM all-atom additive force field for lipids. *J. Phys. Chem. B*.
- Kucerka, N. et al. (2011). Structure of fully hydrated fluid-phase lipid bilayers with monounsaturated chains. *Biochim. Biophys. Acta*.
- Ingólfsson, H.I. et al. (2014). Lipid organization of the plasma membrane. *J. Am. Chem. Soc.*

---

## 9. Contatti e segnalazioni

- Forum CHARMM-GUI: https://charmm-gui.org/forum (account phpBB, cookie phpbb3_8fjan)
- Dr. Wonpil Im (CHARMM-GUI): email non esposta in pagina, contattare via forum o istituzione accademica
- Giorgio Veneruso: giorgi.veneruso@studenti.unina.it (account istituzionale)
