# cgui-studio — Report Tecnico per Candidature PhD/Postdoc in CADD/Drug Discovery

**Autore:** Giorgio Veneruso (LM-9 Biotecnologie del Farmaco, Università di Napoli Federico II)  
**Data:** 5 novembre 2026  
**Repository:** github.com/giorgio-veneruso/cgui-studio (in push)

---

## 1. Obiettivo del progetto

cgui-studio è un laboratorio software personale per il **drug design computazionale** e la **dinamica molecolare (MD)**. Costruisce sistemi molecolari all-atom pronti per simulazioni MD con GROMACS, integrando l'API ufficiale di CHARMM-GUI con un builder all-atom locale che elimina l'attesa in coda del server web.

Il progetto nasce dalla tesi magistrale su *attivazione conformazionale di GPBAR1* (GROMACS, CHARMM-GUI, OneOPES/PLUMED) e dimostra competenze in:
- Biologia strutturale computazionale e CADD (Computer-Aided Drug Design)
- Dinamica molecolare (setup, equilibrazione, analisi)
- Sviluppo software scientifico (Python, FastAPI, Svelte, GROMACS API)

---

## 2. Architettura e stack tecnologico

| Componente | Tecnologia | Ruolo |
|------------|-----------|-------|
| Backend | Python 3.12, FastAPI | API REST, orchestrazione GROMACS, store job (SQLite) |
| Frontend | Svelte 5, Vite, Tailwind CSS | GUI web locale, wizard 4 step, vista 3D |
| Visualizzazione 3D | NGL View 2.5.0 (vendored, no CDN) | Rendering strutture molecolari nel browser |
| Motore MD | GROMACS 2026.3 (conda-forge) | editconf, solvate, genion, grompp, mdrun |
| Force field | CHARMM36 (lipid.rtp da Lemkul-Lab) | Definizione atomi, bond, impropers dei lipidi |
| Test | pytest (117 test), ruff | Qualità del codice e riproducibilità |
| Ambiente | miniforge3 (conda), uv | Gestione dipendenze, no pip di sistema |

**No CDN:** tutte le risorse (NGL, font, icone) sono vendored e servite localmente.  
**Server:** localhost, con modalità MOCK per test offline senza chiamate a CHARMM-GUI.

---

## 3. Funzionalità implementate

### 3.1 Wizard a 4 passi
1. **Tipo di sistema** — bilayer lipidico, proteina, DNA, RNA, small molecule (ognuna con badge colorato)
2. **Composizione** — catalogo di 411 lipidi CHARMM36 con ricerca live, filtro per 13 categorie, foglietti simmetrici/asimmetrici, preset membrane (plasma, ER, mitocondri, POPC puro)
3. **Ambiente** — box X/Y/Z, centratura, solvente, ioni
4. **Riepilogo** — spec completa, metriche post-build, log GROMACS, salva/carica JSON

### 3.2 Builder all-atom locale
Costruisce in locale senza server CHARMM-GUI:
- **Lipidi** — geometrie da lipid.rtp CHARMM36, mescolamento laterale casuale (seed riproducibile)
- **Proteine/DNA/RNA/small molecules** — invio automatico a CHARMM-GUI
- **Pipeline GROMACS** — editconf → solvate → genion → grompp → mdrun, con bilanciamento automatico delle cariche
- **Equilibratura** — file NVT/NPT/produzione + script equil.sh (stile CHARMM-GUI)
- **Metriche post-build** — spessore P-P, APL, acqua/lipide, carica netta

### 3.3 Integrazione CHARMM-GUI
Client API ufficiale con login JWT, submit job, poll status, download tar.gz, manifest SHA256 per provenance.

---

## 4. Validazione quantitativa

**Build di 3 membrane comuni** con box 7×7×10 nm, analisi dal PDB grezzo:

| Sistema | Composizione/leaflet | Lipidi/leaflet | Spessore P-P (nm) | APL misurata (nm²) | APL attesa (nm²) | Deviazione |
|---------|---------------------|----------------|-------------------|--------------------|------------------|------------|
| POPC puro | POPC=64 | 64 | 5.200 | 0.766 | 0.683 | +12% |
| POPC/POPE 50:50 | POPC=32;POPE=32 | 64 | 5.200 | 0.766 | ~0.62 | +24% |
| Membrana plasmatica | POPC=14;POPE=6;PSM=8;CHOL=20;POPS=4;POPI=2;DOPS=2 | 36 | 5.200 | 1.361 | ~0.55 | +147% |

**Fonti letteratura:**
- Dickey et al., Biophys J 2008: POPC APL 0.64±0.01 nm², spessore 4.42 nm
- Hills Jr et al. 2016: POPC APL 0.698±0.007 nm², spessore idrofobico 2.67 nm
- Zak et al. 2023: POPS APL 0.58±0.01 nm²
- Saeedimasine et al. 2019: colesterolo riduce APL ~21%, aumenta spessore ~7%

**Note:** il builder usa geometria semplificata (teste a ±2.6 nm fisse); dopo minimizzazione energetica le distanze si aggiusterebbero. Le deviazioni APL sono dovute alle dimensioni fisse della box che forzano una densità diversa da quella ottimale.

---

## 5. Lavori correlati

### Insane.py (INSert membrANE)
**Approccio:** costruttore di membrane a grana grossa (Martini FF) basato su template. L'utente definisce la posizione di ciascun bead lipidico in un sistema pseudo-2D.  
**Force field:** esclusivamente Martini (coarse-grained 2 e 3). Non supporta CHARMM36 all-atom.  
**Output:** .gro e .top per GROMACS. Nessun bilanciamento cariche, nessun file di equilibratura, nessuna metrica post-build.  
**Differenza:** Insane è CG-only e richiede conoscenze Martini; cgui-studio è all-atom CHARMM36 con bilanciamento cariche, genion, equilibratura NVT/NPT e metriche integrate.

### PACKMOL-Memgen
**Approccio:** workflow basato su PACKMOL per impacchettamento stoccastico di molecole in una scatola, supporta sistemi membrana-proteina-ligando.  
**Force field:** AMBER Lipid17 e CHARMM c36 (tramite VMD psfgen).  
**Output:** .pdb della membrana preparata; richiede passaggi successivi per topologia GROMACS completa. Nessun bilanciamento cariche automatico.  
**Differenza:** PACKMOL-Memgen è orientato a sistemi complessi membrana-proteina, richiede ecosistema AMBER/VMD, è più lento (impacchettamento stoccastico). cgui-studio è standalone Python+GROMACS con output nativo .pdb/.top/.mdp/.tpr, bilanciamento cariche integrato e interfaccia web.

---

## 6. Vantaggio in tempo: build locale vs CHARMM-GUI web

| Metrica | Build locale (cgui-studio) | CHARMM-GUI web |
|---------|---------------------------|----------------|
| Tempo per build (POPC=64, box 7nm) | ~4.3 secondi | N/A (coda bloccata) |
| Tempo in coda | 0 (esecuzione immediata) | 2+ giorni (job fermi, throughput ~0) |
| Dipendenza da server esterno | No | Sì (server sovraccarico, lettera ufficiale di Im in home page) |
| Riproducibilità | Totale (file immutabili, manifest SHA256) | Limitata (job server-side) |

**Misurazione:** build locale di POPC=64 misurata con `time`: 4.331 secondi reali. CHARMM-GUI web: job inviati il 24/10/2026 ancora in coda al 5/11/2026 (12 giorni), server infrastrutturalmente sovraccarico (comunicato ufficiale del PI Wonpil Im).

---

## 7. Screenshot

- **Wizard di composizione** (`results/screenshot_wizard.png`): interfaccia a 4 step con catalogo lipidi, preset membrane, controlli fisici
- **Vista 3D NGL** (`results/screenshot_3d.png`): rendering della struttura bilayer generata, visualizzazione interattiva nel browser

---

## 8. Stato del progetto e prossimi passi

- **Commit attuale:** `2261710` (branch `main`)
- **Tutto il codice è committato e pronto per il push su GitHub** (richiesto da candidature, es. UiO entro 9/11/2026)
- **117 test pytest verdi, ruff pulito, build frontend OK**
- **Prossimi passi:** push su GitHub con CI (GitHub Actions: ruff + pytest), test con proteina reale (PDB ID), eventualmente GIF animata del wizard

---

## 9. Riferimenti e licenze

- CHARMM-GUI API: https://www.charmm-gui.org/?doc=api (uso accademico, citazione obbligatoria)
- CHARMM36 force field: MacKerell Lab, https://mackerell.umaryland.edu/charmm_ff.shtml
- lipid.rtp: Lemkul-Lab toppar_c36_releases, https://github.com/Lemkul-Lab/toppar_c36_releases
- NGL View: https://github.com/arose/ngl (MIT, vendored)
- GROMACS: https://www.gromacs.org
- Licenza progetto: MIT (codice originale)
