# Validation — C-gui Quick Bilayer Builder

Misure reali sui sistemi generati, con i comandi per riprodurli. Nessuna
rivendicazione senza numero.

## 1. Geometria: sweep su tutti i lipidi CHARMM36

Test automatico (`tests/test_make_bilayer.py::test_all_rtp_lipids_geometry_no_clash`):
genera la struttura di **ogni residuo in `lipid.rtp`** (411 lipidi + 3 steroli)
e verifica, per ognuno:

- nessuna coordinata NaN
- nessuna coppia di atomi pesanti non legati a distanza < 0.9 Å nello stesso
  residuo (i legami rtp sono esclusi dal check)

Risultato: tutti i lipidi comuni (POPC, CHOL/CHL1, POPE, DOPE, DPPC, DSPC,
POPS, POPG, POPI, PSM, TOCL, DOTAP, DMPC, DLPC, DPPS…) passano; 3 esotici
di micelle/bola-lipidi (CTLIPA, MCLIPA, VCLIPC) vengono rigettati dal guard
con un messaggio esplicito invece di produrre topologie rotte silenziosamente.

Riproduzione:

```bash
uv run pytest tests/test_make_bilayer.py::test_all_rtp_lipids_geometry_no_clash -v
```

## 2. Build end-to-end con solvente a regione

Comando:

```bash
uv run python scripts/make_bilayer.py \
  --upper "POPC=28;CHL1=12" --lower "POPC=28;CHL1=12" \
  --output results/validation --box-x 6.5 --box-y 6.5 --box-z 9 \
  --solvent water --ion-type NaCl --solvate --run-gmx
```

<!-- VALS: riempito dalle run del 13/11/2026 -->

Risultato atteso/misurato (aggiornato a ogni release):

| Metrica | Valore |
|---|---|
| atomi totali | 24966 |
| spessore P-P misurato | 4.006 nm (atteso: 4.021) |
| SOL generate | 15618 (region-based hydration: solo sopra/sotto la membrana) |
| SOL nel core idrofobico | **0** (verificato sul .gro ionizzato) |
| ioni (NaCl 0.15 M + neutralizzazione) | 34 Na⁺ / 34 Cl⁻, 0 ioni nel core |
| CONECT records nel PDB | 9216 (bond CHARMM36 rtp espliciti: i viewer non inferiscono legami inter-lipide) |
| serial PDB | unici (contatore globale) |
| grompp minim | OK (`minim.tpr`) |

Nota sul metodo: l'idratazione usa `gmx solvate` con il packer a regioni
come CHARMM-GUI — l'acqua non viene mai generata nel core, non c'è nulla
da correggere a posteriori. Il controllo post-genion (`_strip_core_solvent`)
resta come verificatore: sul build validato ha rimosso 0 molecole.

## 3. Pacchetto di equilibratura

Il build produce un protocollo CHARMM-GUI-style pronto per GROMACS:

```
equil_nvt.mdp   NVT 50 ps, posres sulle teste polari
equil_npt.mdp   NPT 50 ps semiisotropo, posres
production.mdp  produzione 100 ns libera
equil.sh        lancia la sequenza completa
```

```bash
cd results/validation/2026-…_make_bilayer_…/
gmx mdrun -s minim.tpr          # minimizzazione steepest descent
bash equil.sh                   # NVT → NPT → produzione
```

## 4. Limiti dichiarati

- la geometria è generata programmaticamente (griglia + orientazioni
  random + resolver anti-clash): non sostituisce un packing con
  minimizzazione preliminare. La minimizzazione steepest descent del
  pacchetto è il primo passo obbligatorio.
- lipidi esotici con headgroup complessi possono essere rigettati dal
  guard (errore esplicito con resname e atomi coinvolti).
- nanodiscs e vescicole non sono supportati (roadmap).
