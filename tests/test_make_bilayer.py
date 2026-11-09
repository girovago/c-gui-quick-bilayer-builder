"""Test per scripts/make_bilayer.py — generatore bilayer locale."""

from __future__ import annotations

import json

# Import diretto dello script (script puro stdlib)
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from make_bilayer import (  # noqa: E402
    apl_for,
    autobox_xy,
    bilayer_thickness,
    clean_chain_itp,
    compute_metrics,
    generate_mdp,
    generate_pdb,
    generate_top,
    parse_lipid_text,
    volume_for,
)
from make_bilayer import (
    main as make_bilayer_main,
)

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
    assert "CHL1" in joined  # CHOL è mappato al nome rtp CHARMM36 del colesterolo


def test_generate_pdb_is_angstrom():
    """PDB di output in ANGSTROM (standard PDB): coordinate > 10."""
    pdb = generate_pdb({"POPC": 4}, {"CHOL": 2})
    atom_lines = [l for l in pdb.splitlines() if l.startswith("ATOM")]
    assert atom_lines
    z_vals = [float(l[46:54]) for l in atom_lines]
    assert max(abs(z) for z in z_vals) > 10.0  # A, non nm


def test_generate_top_has_molecules():
    top = generate_top({"POPC": 4}, {"CHOL": 2}, ion_type="KCl", ion_conc=0.15)
    assert "[ molecules ]" in top
    assert "POPC" in top
    assert "CHL1" in top  # CHOL → CHL1 (colesterolo CHARMM36)
    assert 'include "charmm36.ff/forcefield.itp"' in top


def test_generate_mdp_is_steep():
    mdp = generate_mdp()
    assert "integrator        = steep" in mdp
    assert "nsteps            = 2500" in mdp
    assert "DispCorr          = no" in mdp
    assert "rlist             = 1.2" in mdp
    assert "rvdw-switch       = 1.0" in mdp
    assert "constraints       = h-bonds" in mdp


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


# ---------------------------------------------------------------------------
# Geometria verticale: spessore D_B dalla composizione
# ---------------------------------------------------------------------------

def test_thickness_popc_pure():
    d = bilayer_thickness({"POPC": 64}, {"POPC": 64})
    assert 3.70 <= d <= 3.85


def test_thickness_popc_chol_70_30():
    comp = {"POPC": 70, "CHOL": 30}
    d = bilayer_thickness(comp, comp)
    assert 3.90 <= d <= 4.25


def test_thickness_psm_pure():
    d = bilayer_thickness({"PSM": 64}, {"PSM": 64})
    assert 4.55 <= d <= 4.80


def test_thickness_popc_pope_50_50():
    # POPE (V=1.19, APL=0.55) è più spesso del POPC per lipide: D_B sale
    d = bilayer_thickness({"POPC": 50, "POPE": 50},
                          {"POPC": 50, "POPE": 50})
    assert 3.85 <= d <= 4.15


def test_volume_sterol_alias():
    # CHOL (alias da spec) e CHL1 (nome rtp CHARMM36) = stesso volume
    assert volume_for("CHOL") == volume_for("CHL1") == 0.42


# ---------------------------------------------------------------------------
# P-P misurata dal PDB generato ≈ D_B prevista
# ---------------------------------------------------------------------------

def _rip_metrics(pdb_text: str, upper, lower, box=(8.0, 8.0, 12.0),
                 tmp_path=None):
    pdb_file = tmp_path / "sys.pdb"
    pdb_file.write_text(pdb_text)
    return compute_metrics(str(pdb_file), upper, lower, box_nm=box)


def test_pp_measured_popc_pure(tmp_path: Path):
    upper, lower = {"POPC": 64}, {"POPC": 64}
    d_expected = bilayer_thickness(upper, lower)
    pdb = generate_pdb(upper, lower, box_x=8.0, box_y=8.0)
    m = _rip_metrics(pdb, upper, lower, tmp_path=tmp_path)
    assert abs(m["thickness_P_nm"] - d_expected) <= 0.2
    # il PDB è in Å (standard): compute_metrics divide per 10 -> nm
    assert 3.0 < m["thickness_P_nm"] < 4.5
    assert m["n_phosphate_upper"] == 64
    assert m["n_phosphate_lower"] == 64


def test_pp_measured_popc_chol_mixed(tmp_path: Path):
    upper, lower = {"POPC": 56, "CHOL": 24}, {"POPC": 56, "CHOL": 24}
    d_expected = bilayer_thickness(upper, lower)
    pdb = generate_pdb(upper, lower, box_x=8.0, box_y=8.0)
    m = _rip_metrics(pdb, upper, lower, tmp_path=tmp_path)
    assert abs(m["thickness_P_nm"] - d_expected) <= 0.2
    # solo POPC ha P: CHOL non contribuisce ai fosfati
    assert m["n_phosphate_upper"] == 56
    assert m["n_phosphate_lower"] == 56


# ---------------------------------------------------------------------------
# Autobox
# ---------------------------------------------------------------------------

def test_autobox_popc64():
    area = 64 * apl_for("POPC")
    box = autobox_xy(area)
    assert 6.9 <= box + 1e-9 <= 7.5


def test_autobox_cli(tmp_path: Path):
    outdir = tmp_path / "results"
    rc = make_bilayer_main([
        "--spec", "POPC=64", "--output", str(outdir),
    ])
    assert rc == 0
    generated = [d for d in outdir.iterdir() if d.is_dir()]
    metrics = json.loads(
        (generated[0] / "metrics.json").read_text()
    )
    bx, by, bz = metrics["box_nm"]
    assert 6.9 <= bx <= 7.5
    assert 6.9 <= by <= 7.5
    assert bz == 12.0


# ---------------------------------------------------------------------------
# compute_metrics: APL, acqua per lipide e warning
# ---------------------------------------------------------------------------

def _pdb_line(i, name, resname, x, y, z):
    return (f"ATOM  {i:5d} {name:>4s} {resname:>4s}"
            f"A{1:4d}    {x:8.3f}{y:8.3f}{z:8.3f}  "
            f"1.00  0.00           {name[0]:>2s}")


def _gro_text(n_sol, box=(6.4, 6.4, 7.0)):
    lines = ["test", "???"]
    rows = []
    atom_i = 0
    for sol in range(n_sol):
        for aname in ("OW", "HW1", "HW2"):
            atom_i += 1
            rows.append(
                f"{sol + 1:5d}{'SOL':<5s}{aname:<5s}{atom_i:5d}"
                f"{0.0:8.3f}{0.0:8.3f}{1.000:8.3f}"
            )
    lines[1] = f"{atom_i:5d}"
    lines += rows
    lines.append("")
    lines.append("")
    lines.append(f"{box[0]:10.5f}{box[1]:10.5f}{box[2]:10.5f}")
    return "\n".join(lines) + "\n"


def test_metrics_apl_warning_and_water(tmp_path: Path):
    # 4 POPC (2/foglio), box concordato col .gro 6.4x6.4
    upper, lower = {"POPC": 2}, {"POPC": 2}
    pdb_lines = []
    i = 0
    # PDB in A (standard): z in A, compute_metrics divide per 10 -> nm
    for z in (19.0, 19.0):
        i += 1
        pdb_lines.append(_pdb_line(i, "P", "POPC", 0.0, 0.0, z))
    for z in (-19.0, -19.0):
        i += 1
        pdb_lines.append(_pdb_line(i, "P", "POPC", 0.0, 0.0, z))
    pdb_lines.append("END")
    pdb_file = tmp_path / "sys.pdb"
    pdb_file.write_text("\n".join(pdb_lines) + "\n")
    gro_file = tmp_path / "ionized.gro"
    gro_file.write_text(_gro_text(n_sol=60))

    m = compute_metrics(
        str(pdb_file), upper, lower,
        gro_path=str(gro_file), box_nm=(6.4, 6.4, 7.0),
    )
    assert m["thickness_P_nm"] == 3.8
    assert m["n_phosphate_upper"] == 2
    assert m["n_phosphate_lower"] == 2
    # APL dal box / n lipidi PER FOGLIO (non dal conteggio atomi P)
    assert m["apl_measured_nm2"] == 20.48
    assert m["water_per_lipid"] == 15.0
    assert m["apl_expected_nm2"] == 0.683
    # 20.48 vs 0.683 -> deviazione > 15%: warning (non bloccante)
    assert any("APL" in w for w in m["warning"])
    assert not any("acqua" in w for w in m["warning"])


def test_metrics_water_warning(tmp_path: Path):
    upper, lower = {"POPC": 2}, {"POPC": 2}
    pdb_lines = [
        # PDB in A (standard): z in A, compute_metrics divide per 10 -> nm
        _pdb_line(1, "P", "POPC", 0.0, 0.0, 19.0),
        _pdb_line(2, "P", "POPC", 0.0, 0.0, -19.0),
    ]
    pdb_lines.append("END")
    pdb_file = tmp_path / "sys.pdb"
    pdb_file.write_text("\n".join(pdb_lines) + "\n")
    gro_file = tmp_path / "ionized.gro"
    gro_file.write_text(_gro_text(n_sol=400))
    m = compute_metrics(
        str(pdb_file), upper, lower,
        gro_path=str(gro_file), box_nm=(6.4, 6.4, 7.0),
    )
    assert m["water_per_lipid"] == 100.0
    assert any("acqua" in w for w in m["warning"])


def test_metrics_extra_warnings_passed_through(tmp_path: Path):
    upper, lower = {"POPC": 2}, {"POPC": 2}
    pdb_lines = [
        # PDB in A (standard): z in A, compute_metrics divide per 10 -> nm
        _pdb_line(1, "P", "POPC", 0.0, 0.0, 19.0),
        _pdb_line(2, "P", "POPC", 0.0, 0.0, -19.0),
        "END",
    ]
    pdb_file = tmp_path / "sys.pdb"
    pdb_file.write_text("\n".join(pdb_lines) + "\n")
    m = compute_metrics(
        str(pdb_file), upper, lower, box_nm=(6.4, 6.4, 7.0),
        extra_warnings=["box troppo grande"],
    )
    assert "box troppo grande" in m["warning"]


def test_cli_box_explicit_area_warning(tmp_path: Path):
    # box 12x12 esplicito con POPC=10 (area 6.83 nm2 < 40% del box): warning
    outdir = tmp_path / "results"
    rc = make_bilayer_main([
        "--spec", "POPC=10", "--box-x", "12", "--box-y", "12",
        "--output", str(outdir),
    ])
    assert rc == 0
    generated = [d for d in outdir.iterdir() if d.is_dir()]
    metrics = json.loads((generated[0] / "metrics.json").read_text())
    warnings_text = json.dumps(metrics["warning"])
    assert "troppa acqua" in warnings_text


# ---------------------------------------------------------------------------
# E2E: pdb2gmx + grompp (richiede gmx nel PATH)
# ---------------------------------------------------------------------------

def _gmx_available() -> bool:
    import shutil
    return shutil.which("gmx") is not None


@pytest.mark.skipif(not _gmx_available(), reason="gmx non disponibile")
def test_e2e_pdb2gmx_grompp_popc_chol(tmp_path: Path):
    """pdb2gmx genera topologia completa ([bonds] non vuoto) e grompp
    produce minim.tpr su POPC+CHL1."""
    import subprocess
    script = str(Path(__file__).resolve().parents[1] / "scripts" / "make_bilayer.py")
    result = subprocess.run(
        [sys.executable, script, "--spec", "POPC=8;CHOL=2", "--run-gmx",
         "--output", str(tmp_path)],
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, f"CLI fallito: {result.stderr}"
    generated = [d for d in tmp_path.iterdir() if d.is_dir()]
    assert len(generated) == 1
    system_dir = generated[0] / "system"
    top_path = system_dir / "topol.top"
    tpr_path = system_dir / "minim.tpr"
    assert top_path.exists()
    assert tpr_path.exists(), "minim.tpr non generato (grompp fallito)"
    top_text = top_path.read_text()
    assert "[ bonds ]" in top_text
    # la sezione [ bonds ] deve contenere almeno un bond
    in_bonds = False
    n_bonds = 0
    for line in top_text.splitlines():
        if line.strip() == "[ bonds ]":
            in_bonds = True
            continue
        if in_bonds:
            if line.strip().startswith("["):
                break
            s = line.strip()
            if s and not s.startswith(";"):
                n_bonds += 1
    assert n_bonds > 0, "[ bonds ] e' vuoto: topologia incompleta"


# ---------------------------------------------------------------------------
# clean_chain_itp: tail pdb2gmx rimosso, moleculetype intatto
# ---------------------------------------------------------------------------

def test_clean_chain_itp_cuts_after_posres_endif():
    """Il tail (acqua assoluta + POSRES_WATER + fsys/molecules) va via."""
    itp = (
        "[ moleculetype ]\nProt 3\n\n[ atoms ]\n1 N 1\n"
        "\n#ifdef POSRES\n#include \"posre.itp\"\n#endif\n"
        "\n#include \"/home/x/.local/share/cgui-studio/charmm36.ff/tip3p.itp\"\n"
        "\n#ifdef POSRES_WATER\n[ position_restraints ]\n1 1 1000 1000 1000\n#endif\n"
        "\n[ system ]\nS\n\n[ molecules ]\nProt 1\n"
    )
    out = clean_chain_itp(itp)
    assert "[ moleculetype ]" in out
    assert "#ifdef POSRES" in out
    assert "#include \"posre.itp\"" in out
    assert "tip3p.itp" not in out
    assert "POSRES_WATER" not in out
    assert "[ molecules ]" not in out


def test_clean_chain_itp_cuts_at_absolute_tip3p_when_no_posres():
    itp = (
        "[ moleculetype ]\nProt 3\n\n[ atoms ]\n1 N 1\n"
        "\n#include \"/home/x/charmm36.ff/tip3p.itp\"\n[ molecules ]\nProt 1\n"
    )
    out = clean_chain_itp(itp)
    assert "[ moleculetype ]" in out
    assert "tip3p.itp" not in out
    assert "[ molecules ]" not in out


def test_clean_chain_itp_intact_without_tail():
    itp = "[ moleculetype ]\nProt 3\n\n[ atoms ]\n1 N 1\n"
    assert clean_chain_itp(itp) == itp


def test_clean_chain_itp_real_pdb2gmx_file():
    """Sul file reale del benchmark: le sezioni moleculetype restano intatte."""
    candidates = [
        Path("results/2026-11-08_benchmark_gpbar1/topol_ProteinChainR.itp"),
        Path("/tmp/opencode/embed_fix_test/topol_ProteinChainR.itp"),
    ]
    real = next((p for p in candidates if p.exists()), None)
    if real is None:
        pytest.skip("file benchmark non presente")
    out = clean_chain_itp(real.read_text())
    # tutte le sezioni fino a cmap restano
    for sect in ("[ atoms ]", "[ bonds ]", "[ angles ]", "[ dihedrals ]", "[ cmap ]"):
        assert sect in out
    # niente acqua relativa/assoluta, niente POSRES_WATER
    assert "tip3p.itp" not in out
    assert "POSRES_WATER" not in out
    # la definizione posres relativa resta (risolta dalla copia in system/)
    assert "#include \"posre.itp\"" in out
