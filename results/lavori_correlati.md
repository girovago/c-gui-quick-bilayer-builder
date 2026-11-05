# Lavori correlati

## Insane.py (INSert membrANE)

**Approccio:** costruttore di membrane a grana grossa (Martini force field) basato su template. L'utente definisce la posizione di ciascun bead lipidico in un sistema di coordinate pseudo-2D; Insane genera il sistema .gro e .top per GROMACS. È parte dell'ecosistema Martini (Tsjerk/Insane su GitHub).

**Force field supportati:** esclusivamente Martini (coarse-grained, 2 e 3). Non supporta force field all-atom come CHARMM36.

**Output:** file .gro (struttura) e .top (topologia) per GROMACS, pronti per simulazione CG. Non produce file di equilibratura né metriche post-build.

**Differenza con cgui-studio:** Insane è orientato a membrane coarse-grained e richiede conoscenze del sistema Martini; cgui-studio è all-atom (CHARMM36), include bilanciamento automatico delle cariche, genion, file di equilibratura NVT/NPT e metriche post-build (spessore, APL, acqua per lipide).

## PACKMOL-Memgen

**Approccio:** workflow generalizzato basato su PACKMOL per l'impacchettamento stoccastico di molecole in una scatola, seguito da solvatazione e preparazione della topologia. Supporta sistemi membrana-proteina-ligando.

**Force field supportati:** AMBER (Lipid17) e CHARMM c36 (tramite VMD psfgen). Output compatibile con AMBER's Leap e VMD's psfgen.

**Output:** file .pdb della membrana preparata (.gro non nativo), richiede passaggi successivi per la topologia GROMACS completa. Non include bilanciamento automatico delle cariche né file di equilibratura.

**Differenza con cgui-studio:** PACKMOL-Memgen è più orientato a sistemi membrana-proteina complessi e richiede l'ecosistema AMBER/VMD; cgui-studio è un tool standalone Python+GROMACS con output GROMACS nativo (.pdb, .top, .mdp, .tpr), bilanciamento cariche integrato e interfaccia grafica web. PACKMOL-Memgen è più lento (impacchettamento stoccastico) e meno integrato con il flusso GROMACS.
