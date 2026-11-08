"""Test per scripts/embed_protein.py — Modulo 4 embed proteina.

Unit test: fetch e subprocess mockati (nessuna rete reale).
Integration test (@pytest.mark.integration): scarica realmente 7CFN ed esegue
pdb2gmx reale; skip se niente network o niente gmx.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import embed_protein as ep  # noqa: E402
import ppm_orientation as ppmo  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture PDB sintetico (2 chain, coordinate note)
# ---------------------------------------------------------------------------

SYNTHETIC_PDB = """\
HEADER    TEST PROTEIN
REMARK    Membrane thickness (hydrophobic): 30.0 Angstrom
ATOM      1  N   ALA A   1      10.000   0.000   0.000  1.00  0.00           N
ATOM      2  CA  ALA A   1      11.000   0.000   0.000  1.00  0.00           C
ATOM      3  C   ALA A   1      12.000   0.000   0.000  1.00  0.00           C
ATOM      4  O   ALA A   1      13.000   0.000   0.000  1.00  0.00           O
ATOM      5  N   GLU A   2      14.000   0.000  10.000  1.00  0.00           N
ATOM      6  CA  GLU A   2      15.000   0.000  10.000  1.00  0.00           C
ATOM      7  N   ARG B  50      20.000   0.000 -10.000  1.00  0.00           N
ATOM      8  CA  ARG B  50      21.000   0.000 -10.000  1.00  0.00           C
HETATM    9  O   HOH W   1      99.000   0.000  99.000  1.00  0.00           O
END
"""


def _fake_pdb2gmx(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# orient_protein
# ---------------------------------------------------------------------------

class TestOrientProtein:
    def test_opm_source(self, monkeypatch):
        monkeypatch.setattr(ep, "_http_get", lambda url, timeout=None: SYNTHETIC_PDB)
        text, source, thick = ep.orient_protein("1ABC", prefer="OPM")
        assert source == "OPM"
        assert thick == 30.0
        assert "ATOM" in text

    def test_passthrough_when_opm_fails(self, monkeypatch):
        def fake_get(url, timeout=None):
            if "opm" in url:
                return None  # OPM down
            return SYNTHETIC_PDB
        monkeypatch.setattr(ep, "_http_get", fake_get)
        monkeypatch.setattr(ppmo, "run_ppm_server", lambda raw: None)  # PPM down
        text, source, thick = ep.orient_protein("1ABC", prefer="OPM")
        assert source == "passthrough"
        assert thick == 30.0

    def test_prefer_none_passthrough(self, monkeypatch):
        monkeypatch.setattr(ep, "_http_get", lambda url, timeout=None: None)
        with pytest.raises(RuntimeError):
            ep.orient_protein("1ABC", prefer="none")


# ---------------------------------------------------------------------------
# read_chain_info / filter / strip
# ---------------------------------------------------------------------------

class TestChainHandling:
    def test_read_chain_info(self):
        info = ep.read_chain_info(SYNTHETIC_PDB)
        chains = {c["chain"]: c for c in info}
        assert set(chains) == {"A", "B"}
        assert chains["A"]["n_atoms"] == 6
        assert chains["A"]["n_residues"] == 2
        assert chains["B"]["n_atoms"] == 2
        assert chains["B"]["n_residues"] == 1
        # HETATM non conta: nessuna chain "W" (acqua) nelle info
        assert "W" not in chains

    def test_filter_chains(self):
        out = ep.filter_chains(SYNTHETIC_PDB, ["B"])
        assert "ATOM" in out
        assert "ARG B" in out
        assert "GLU A" not in out
        assert "END" in out

    def test_strip_hetatm(self):
        out = ep.strip_hetatm(SYNTHETIC_PDB)
        assert "HETATM" not in out
        assert "ATOM" in out


# ---------------------------------------------------------------------------
# Coordinate / charge / z-span
# ---------------------------------------------------------------------------

class TestMetrics:
    def test_z_span_nm(self):
        # z da -10 a +10 Å -> 20 Å = 2.0 nm
        assert ep.z_span_nm(SYNTHETIC_PDB) == 2.0

    def test_tm_z_range_nm(self):
        # Residui TM 30-230: solo res 50 (ARG B, z=-10) cade dentro -> [-1.0, -1.0]
        zmin, zmax = ep.tm_z_range_nm(SYNTHETIC_PDB)
        assert zmin == -1.0
        assert zmax == -1.0

    def test_protein_net_charge(self):
        # ALA(0) + GLU(-1) + ARG(+1) + termini(+1-1) = 0.0
        assert ep.protein_net_charge(SYNTHETIC_PDB) == 0.0


# ---------------------------------------------------------------------------
# split_topol_by_chain
# ---------------------------------------------------------------------------

class TestSplitTopol:
    TOPol_SAMPLE = """\
#include "./charmm36.ff/forcefield.itp"

[ moleculetype ]
; Name            nrexcl
Protein_chain_A     3

[ atoms ]
;   nr       type  resnr residue  atom   cgnr     charge       mass
     1        NH3     11    THR      N      1       -0.3     14.007
     2         HC     11    THR      H1      2       0.33      1.008

[ bonds ]

[ moleculetype ]
; Name            nrexcl
Protein_chain_B     3

[ atoms ]
;   nr       type  resnr residue  atom   cgnr     charge       mass
     1        NH3     50    ARG      N      1       -0.3     14.007
     2         HC     50    ARG      H1      2       0.33      1.008

[ bonds ]

[ system ]
; Name
Protein

[ molecules ]
; Compound        #mols
Protein_chain_A     1
Protein_chain_B     1
"""

    def test_split_creates_one_itp_per_chain(self, tmp_path):
        topol = tmp_path / "topol.top"
        topol.write_text(self.TOPol_SAMPLE)
        results = ep.split_topol_by_chain(topol, ["A", "B"], tmp_path)
        assert set(results) == {"A", "B"}
        content_a = results["A"].read_text()
        assert "Protein_chain_A" in content_a
        assert "Protein_chain_B" not in content_a
        assert results["A"].name == "topol_ProteinChainA.itp"

    def test_split_handles_missing_chain(self, tmp_path):
        topol = tmp_path / "topol.top"
        topol.write_text(self.TOPol_SAMPLE)
        results = ep.split_topol_by_chain(topol, ["A", "C"], tmp_path)
        assert set(results) == {"A"}  # C non esiste, saltato


# ---------------------------------------------------------------------------
# run() con subprocess mockato
# ---------------------------------------------------------------------------

class TestRunMocked:
    def test_manifest_fields_and_chain_filter(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ep, "orient_protein",
                            lambda pdb, prefer="OPM": (SYNTHETIC_PDB, "OPM", 30.0))
        fake_cp = _fake_pdb2gmx(returncode=0, stdout="ok", stderr="log tail here")
        monkeypatch.setattr(ep, "prepare_pdb2gmx",
                            lambda pdb_text, out_dir, gmx=None, ff_dir=None: fake_cp)
        monkeypatch.setattr(ep, "get_gmx_binary", lambda: "/fake/gmx")

        manifest = ep.run(pdb="1ABC", out_dir=tmp_path, chain_filter=["A"], prefer="OPM")

        assert manifest["pdb_id"] == "1ABC"
        assert manifest["source"] == "OPM"
        assert manifest["chains"] == ["A"]
        assert manifest["per_chain_atom_counts"] == {"A": 6}
        assert manifest["protein_net_charge"] == -1.0
        assert manifest["z_span_nm"] == 1.0
        assert manifest["pdb2gmx_status"] == "ok"
        assert "log tail here" in manifest["pdb2gmx_log_tail"]
        assert "timestamp" in manifest
        # File attesi presenti
        assert (tmp_path / "opm_oriented.pdb").exists()
        assert (tmp_path / "manifest.json").exists()
        # timestamp parsabile
        datetime.fromisoformat(manifest["timestamp"].replace("Z", "+00:00"))

    def test_pdb2gmx_fail_recorded(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ep, "orient_protein",
                            lambda pdb, prefer="OPM": (SYNTHETIC_PDB, "passthrough", 30.0))
        monkeypatch.setattr(ep, "prepare_pdb2gmx",
                            lambda pdb_text, out_dir, gmx=None, ff_dir=None:
                            _fake_pdb2gmx(returncode=1, stderr="FATAL: something"))
        monkeypatch.setattr(ep, "get_gmx_binary", lambda: "/fake/gmx")

        manifest = ep.run(pdb="1ABC", out_dir=tmp_path)
        assert manifest["pdb2gmx_status"] == "fail"
        assert "FATAL" in manifest["pdb2gmx_log_tail"]


# ---------------------------------------------------------------------------
# Integration test (rete reale + gmx reale) — skip se non disponibile
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestIntegrationReal:
    def test_real_7cfn_embed(self, tmp_path):
        """Scarica 7CFN da RCSB ed esegue pdb2gmx reale."""
        ep.get_gmx_binary()  # solleva se assente
        # Scarica PDB reale
        text = ep.fetch_rcsb_pdb("7CFN")
        assert text is not None and "ATOM" in text
        # Esegue run su chain A sola
        manifest = ep.run(pdb="7CFN", out_dir=tmp_path, chain_filter=["A"], prefer="PPM")
        assert manifest["pdb_id"] == "7CFN"
        assert manifest["source"] in ("PPM_server", "passthrough")
        assert "A" in manifest["chains"]
        assert manifest["per_chain_atom_counts"].get("A", 0) > 0
        assert (tmp_path / "opm_oriented.pdb").exists()
        if manifest["pdb2gmx_status"] == "ok":
            assert (tmp_path / "protein.gro").exists()
            assert (tmp_path / "topol.top").exists()
            assert (tmp_path / "topol_ProteinChainA.itp").exists()
        # Stampa numeri per visibilità
        print("\nREAL 7CFN:", json.dumps({
            "source": manifest["source"],
            "chains": manifest["chains"],
            "atoms_A": manifest["per_chain_atom_counts"].get("A"),
            "net_charge": manifest["protein_net_charge"],
            "z_span_nm": manifest["z_span_nm"],
            "tm_z_nm": [manifest["tm_z_min_nm"], manifest["tm_z_max_nm"]],
            "pdb2gmx": manifest["pdb2gmx_status"],
        }, indent=2))
