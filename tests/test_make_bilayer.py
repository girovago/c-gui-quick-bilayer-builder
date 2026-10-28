"""Test per scripts/make_bilayer.py — generatore bilayer locale."""

from __future__ import annotations

import json

# Import diretto dello script (script puro stdlib)
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from make_bilayer import generate_mdp, generate_pdb, generate_top, parse_lipid_text  # noqa: E402

# ---------------------------------------------------------------------------
# Parsing spec
# ---------------------------------------------------------------------------

def test_parse_lipid_text_simple():
    assert parse_lipid_text("POPC=64") == {"POPC": 64}


def test_parse_lipid_text_multiple():
    assert parse_lipid_text("POPC=48;CHOL=16") == {"POPC": 48, "CHOL": 16}


def test_parse_lipid_text_newline_separator():
    assert parse_lipid_text("POPC=32\nCHOL=8") == {"POPC": 32, "CHOL": 8}


def test_parse_lipid_text_duplicates_sum():
    assert parse_lipid_text("POPC=10;POPC=20") == {"POPC": 30}


def test_parse_lipid_text_empty():
    assert parse_lipid_text("") == {}
    assert parse_lipid_text("   ") == {}


def test_parse_lipid_text_invalid():
    with pytest.raises(ValueError, match="non valida"):
        parse_lipid_text("POPC")
    with pytest.raises(ValueError, match="non intero"):
        parse_lipid_text("POPC=abc")
    with pytest.raises(ValueError, match="negativo"):
        parse_lipid_text("POPC=-1")


# ---------------------------------------------------------------------------
# Generatori PDB / TOP / MDP
# ---------------------------------------------------------------------------

def test_generate_pb_has_header_and_end():
    pdb = generate_pdb({"POPC": 4}, {"CHOL": 2})
    assert pdb.startswith("TITLE")
    assert "END" in pdb.splitlines()[-1]
    atom_lines = [line for line in pdb.splitlines() if line.startswith("ATOM")]
    assert len(atom_lines) > 0


def test_generate_pdb_counts_match():
    upper = {"POPC": 10, "CHOL": 5}
    lower = {"POPC": 8}
    pdb = generate_pdb(upper, lower)
    # verifica che i nomi residue compaiano nelle righe ATOM
    atom_lines = [line for line in pdb.splitlines() if line.startswith("ATOM")]
    assert len(atom_lines) > 0
    joined = " ".join(atom_lines)
    assert "POPC" in joined
    assert "CHOL" in joined


def test_generate_top_has_molecules():
    top = generate_top({"POPC": 4}, {"CHOL": 2}, ion_type="KCl", ion_conc=0.15)
    assert "[ molecules ]" in top
    assert "POPC" in top
    assert "CHOL" in top
    assert 'include "charmm36.ff/forcefield.itp"' in top


def test_generate_mdp_is_steep():
    mdp = generate_mdp()
    assert "integrator        = steep" in mdp
    assert "nsteps            = 500" in mdp


# ---------------------------------------------------------------------------
# E2E: esecuzione CLI su directory temporanea
# ---------------------------------------------------------------------------

@pytest.fixture
def outdir(tmp_path: Path) -> Path:
    return tmp_path / "results"


def test_cli_generates_files(outdir: Path):
    import subprocess

    script = str(Path(__file__).resolve().parents[1] / "scripts" / "make_bilayer.py")
    result = subprocess.run(
        [sys.executable, script, "--spec", "POPC=16;CHOL=4", "--output", str(outdir)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"CLI fallito: {result.stderr}"

    # Trova la directory generata (una sola sotto results/)
    generated = [d for d in outdir.iterdir() if d.is_dir()]
    assert len(generated) == 1
    run_dir = generated[0]
    system_dir = run_dir / "system"

    assert (system_dir / "step1_pdbreader.pdb").exists()
    assert (system_dir / "topol.top").exists()
    assert (system_dir / "minim.mdp").exists()
    assert (run_dir / "README.txt").exists()
    assert (run_dir / "manifest.json").exists()

    # manifest valido
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert "pdb" in manifest
    assert "top" in manifest
    assert "mdp" in manifest

    # PDB contiene righe ATOM e END
    pdb_text = (system_dir / "step1_pdbreader.pdb").read_text()
    assert pdb_text.startswith("TITLE")
    assert pdb_text.splitlines()[-1] == "END"
    assert sum(1 for line in pdb_text.splitlines() if line.startswith("ATOM")) > 0

    # nome directory contiene la data ISO
    assert run_dir.name.startswith("20")
