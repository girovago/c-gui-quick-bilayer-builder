# Validazione quantitativa — cgui-studio builder all-atom

**Data:** 5 novembre 2026  
**Sistema:** builder all-atom locale (scripts/make_bilayer.py), GROMACS 2026.3, CHARMM36 force field  
**Metodo:** build di 3 membrane con box 7×7×10 nm, PDB grezzo analizzato per spessore P-P e area per lipide (APL)

---

## 1. Risultati ottenuti

| Sistema | Composizione (per foglietto) | Lipidi/leaflet | Spessore P-P (nm) | APL misurata (nm²) | APL attesa (nm²) | Deviazione APL |
|---------|------------------------------|----------------|-------------------|--------------------|------------------|----------------|
| POPC puro | POPC=64 | 64 | 5.200 | 0.766 | 0.683 | +12% |
| POPC/POPE 50:50 | POPC=32;POPE=32 | 64 | 5.200 | 0.766 | ~0.62 | +24% |
| Membrana plasmatica | POPC=14;POPE=6;PSM=8;CHOL=20;POPS=4;POPI=2;DOPS=2 | 36 | 5.200 | 1.361 | ~0.55 | +147% |

---

## 2. Valori di riferimento dalla letteratura

| Sistema | Spessore P-P (nm) | APL (nm²) | Fonte |
|---------|-------------------|-----------|-------|
| POPC puro | 4.42 (con 220±30 mM NaCl) | 0.64 ± 0.01 | Dickey et al., Biophys J 2008 [S0006349508784091] |
| POPC puro | ~3.9–3.95 | 0.64–0.69 | ResearchGate, simulazioni vs sperimentale |
| POPC puro | hydrophobic 2.67 | 0.698 ± 0.007 | Hills Jr et al., PMC5067697 (2016) |
| POPS puro | — | 0.58 ± 0.01 | Zak et al., ACS 2023 [39/51/18995/323538] |
| Membrana biologica | — | ↓ ~21% con colesterolo | Saeedimasine et al., Sci Rep 2019 [s41598-019-44318-9] |

**Note sulla letteratura:**
- Il spessore P-P misurato da raggruppamento a raggi X include i gruppi fosfato e tende a sovrastimare lo spessore idrofobico puro (~2.7 nm per POPC per Hills 2016).
- L'APL di POPC puro si attesta attorno a 0.64–0.70 nm² a 303 K.
- L'aggiunta di colesterolo (20% nel preset plasma) riduce l'APL di circa il 20% e aumenta lo spessore del ~7% (Saeedimasine 2019).

---

## 3. Confronto e analisi

| Sistema | APL ottenuta vs attesa | Deviazione | Interpretazione |
|---------|----------------------|------------|-----------------|
| POPC puro | 0.766 vs 0.683 | +12% | Accettabile per build iniziale senza minimizzazione |
| POPC/POPE | 0.766 vs ~0.62 | +24% | POPE ha APL minore di POPC; builder usa raggio fisso per foglietto |
| Plasma | 1.361 vs ~0.55 | +147% | Colesterolo e sfingomielina hanno APL minore; builder non ridimensiona il foglietto per composizione mista |

**Note sull'interpretazione:**
- Il builder calcola il raggio del foglietto dall'APL attesa per composizione (tabella APL_NM2 in make_bilayer.py), ma le dimensioni fisse della box (7×7 nm) forzano un'APL diversa.
- LO SPessore P-P è costante a 5.2 nm per tutti i sistemi perché HEAD_Z è fissato a 2.6 nm (geometria semplificata del builder, non minimizzazione energetica reale).
- Dopo minimizzazione energetica (gmx mdrun), le distanze si aggiusterebbero ma richiede RAM non disponibile su workstation locale.

---

## 4. Note metodologiche

- Le coordinate del PDB generato dal builder sono in nm (fixato il 5/11/2026, commit 2261710).
- Le metriche sono calcolate dal file step1_pdbreader.pdb (PDB grezzo, pre-solvatazione).
- Per sistemi più grandi o produzioni MD reali, si raccomanda l'uso di un cluster o server dedicato.
