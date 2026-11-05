"""Test per scripts/ppm_orientation.py — Modulo 1 orientamento proteina-membrana."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from ppm_orientation import (  # noqa: E402
    _parse_thickness,
    classify_residues,
    parse_pdb_residues,
)

# ---------------------------------------------------------------------------
# PDB sintetico: 2 residui nel foglietto superiore (z>0), 2 nell'inferiore,
# 1 titolabile nel core (ASP a z=1.0) e 1 titolabile in acqua (GLU a z=30.0)
# ---------------------------------------------------------------------------

SYNTHETIC_PDB = """\
REMARK    Membrane thickness (hydrophobic): 30.0 Angstrom
ATOM      1  N   MET A   1       0.000   0.000  30.000  1.00  0.00           N
ATOM      2  CA  MET A   1       1.000   0.000  30.000  1.00  0.00           C
ATOM      3  N   LEU A   2       2.000   0.000   5.000  1.00  0.00           N
ATOM      4  CA  LEU A   2       3.000   0.000   5.000  1.00  0.00           C
ATOM      5  N   ASP A   3       4.000   0.000   1.000  1.00  0.00           N
ATOM      6  CA  ASP A   3       5.000   0.000   1.000  1.00  0.00           C
ATOM      7  N   LEU A   4       6.000   0.000  -5.000  1.00  0.00           N
ATOM      8  CA  LEU A   4       7.000   0.000  -5.000  1.00  0.00           C
ATOM      9  N   ILE A   5       8.000   0.000 -12.000  1.00  0.00           N
ATOM     10  CA  ILE A   5       9.000   0.000 -12.000  1.00  0.00           C
ATOM     11  N   GLU A   6      10.000   0.000 -30.000  1.00  0.00           N
ATOM     12  CA  GLU A   6      11.000   0.000 -30.000  1.00  0.00           C
END
"""


class TestParseThickness:
    def test_thickness_from_remark(self):
        assert _parse_thickness(SYNTHETIC_PDB) == 30.0

    def test_thickness_missing_returns_none(self):
        assert _parse_thickness("HEADER    TEST\nEND\n") is None

    def test_thickness_case_insensitive(self):
        pdb = "REMARK    MEMBRANE THICKNESS: 25.0\n"
        assert _parse_thickness(pdb) == 25.0


class TestParsePdbResidues:
    def test_residues_parsed(self):
        residues = parse_pdb_residues(SYNTHETIC_PDB)
        assert len(residues) == 6
        assert residues[0]["resname"] == "MET"
        assert residues[0]["chain"] == "A"
        assert residues[0]["resno"] == 1

    def test_z_mean(self):
        residues = parse_pdb_residues(SYNTHETIC_PDB)
        # MET A 1: entrambi gli atomi a z=30 → z_mean = 30
        assert residues[0]["z_mean"] == 30.0
        # ASP A 3: entrambi gli atomi a z=1 → z_mean = 1
        assert residues[2]["z_mean"] == 1.0

    def test_hetatm_ignored(self):
        pdb = SYNTHETIC_PDB + (
            "HETATM    1  HOH W   1      99.000   0.000  99.000  1.00  0.00           O\n"
        )
        residues = parse_pdb_residues(pdb)
        assert len(residues) == 6  # non conta il residuo HOH


class TestClassifyResidues:
    """Classificazione con spessore 30 Å: core |z|<15, interfaccia 15≤|z|<20, acqua |z|≥20."""

    def make_classified(self):
        residues = parse_pdb_residues(SYNTHETIC_PDB)
        return classify_residues(residues, hydrophobic_thickness=30.0)

    def test_regions(self):
        classified = self.make_classified()
        regions = {r["resno"]: r["region"] for r in classified}
        # MET A 1: z=30 → acqua
        assert regions[1] == "aqueous"
        # LEU A 2: z=5 → core idrofobico
        assert regions[2] == "hydrophobic_core"
        # ASP A 3: z=1 → core idrofobico
        assert regions[3] == "hydrophobic_core"
        # LEU A 4: z=-5 → core idrofobico
        assert regions[4] == "hydrophobic_core"
        # ILE A 5: z=-12 → core idrofobico
        assert regions[5] == "hydrophobic_core"
        # GLU A 6: z=-30 → acqua
        assert regions[6] == "aqueous"

    def test_flagged_pka_shift(self):
        """ASP A 3 (titolabile, core) → flag true; GLU A 6 (titolabile, acqua) → false."""
        classified = self.make_classified()
        flags = {r["resno"]: r["flagged_pka_shift"] for r in classified}
        assert flags[3] is True  # ASP nel core
        assert flags[6] is False  # GLU in acqua

    def test_hydrophobicity_present(self):
        classified = self.make_classified()
        hyd = {r["resno"]: r["hydrophobicity"] for r in classified}
        assert hyd[1] == 1.9  # MET
        assert hyd[3] == -3.5  # ASP
        assert hyd[5] == 4.5  # ILE

    def test_titratable_flag(self):
        classified = self.make_classified()
        tit = {r["resno"]: r["titratable"] for r in classified}
        assert tit[3] is True  # ASP
        assert tit[6] is True  # GLU
        assert tit[2] is False  # LEU

    def test_interface_region(self):
        """Residuo a z=17 (spessore/2=15, interfaccia 15-20) → interface."""
        residues = [{"chain": "A", "resno": 99, "resname": "LEU", "z_mean": 17.0}]
        classified = classify_residues(residues, hydrophobic_thickness=30.0)
        assert classified[0]["region"] == "interface"
        assert classified[0]["flagged_pka_shift"] is False  # LEU non titolabile

    def test_custom_thickness(self):
        """Spessore 10 Å: core |z|<5, interfaccia 5-10, acqua ≥10."""
        residues = parse_pdb_residues(SYNTHETIC_PDB)
        classified = classify_residues(residues, hydrophobic_thickness=10.0)
        regions = {r["resno"]: r["region"] for r in classified}
        # LEU A 2: z=5 → interfaccia (5 ≤ 5 < 10)
        assert regions[2] == "interface"
        # ASP A 3: z=1 → core
        assert regions[3] == "hydrophobic_core"
        # GLU A 6: z=-30 → acqua
        assert regions[6] == "aqueous"
