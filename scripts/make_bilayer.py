#!/usr/bin/env python3
"""make_bilayer.py - genera una struttura bilayer all-atom in locale.

Legge una specifica lipidica (es. POPC=64;CHOL=16) e produce in
results/AAAA-MM-GG_nome/:
    system/step1_pdbreader.pdb   struttura bilayer (PDB)
    topol.top                    topologia GROMACS (scheletro charmm36)
    minim.mdp                    parametri di minimizzazione energia
    README.txt                   nota di provenienza
    manifest.json                sha256 dei file generati

Lo script è puro Python (stdlib) per la generazione PDB/TOP/MDP.
Se gmx è disponibile nell'ambiente, esegue anche:
    gmx editconf   (centra e box)
    gmx solvate    (aggiunge acqua)

Uso tipico:
    python scripts/make_bilayer.py --spec "POPC=64;CHOL=16"
    python scripts/make_bilayer.py --upper "POPC=64" --lower "POPC=48;CHOL=16"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Costanti geometriche (nanometri)
# ---------------------------------------------------------------------------

HEAD_Z = 2.6          # posizione teste polari (fuori dal bilayer)
TAIL_END_Z = 0.2      # estremità code (verso centro bilayer)
LIPID_RADIUS = 0.55   # raggio occupato da un lipide nel piano xy (nm)

# Residui supportati con conteggio atomi approssimativo (all-atom ridotto)
LIPID_ATOMS = {
    "POPC": 21,  # testa colina+fosfato+glicerolo + 2 code (16:0 + 18:1)
    "CHOL": 27,  # colesterolo (anelli + coda + idrossile)
    "DOPC": 21,
    "DPPC": 21,
    "POPE": 20,
}


# ---------------------------------------------------------------------------
# Parsing spec
# ---------------------------------------------------------------------------

def parse_lipid_text(text: str) -> dict[str, int]:
    """Converte 'POPC=64;CHOL=16' in {'POPC': 64, 'CHOL': 16}.

    Accetta separatori ';' o newline. Valori interi positivi.
    """
    if not text or not text.strip():
        return {}
    result: dict[str, int] = {}
    normalized = text.replace(";", "\n")
    for raw in normalized.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        if "=" not in raw:
            raise ValueError(f"riga lipidica non valida (serve NOME=count): '{raw}'")
        name, count_s = raw.split("=", 1)
        name = name.strip().upper()
        count_s = count_s.strip()
        if not name.isalpha():
            raise ValueError(f"nome lipidico non valido: '{name}'")
        try:
            count = int(count_s)
        except ValueError as exc:
            raise ValueError(f"count non intero per '{name}': '{count_s}'") from exc
        if count < 0:
            raise ValueError(f"count negativo per '{name}': {count}")
        if count == 0:
            continue
        result[name] = result.get(name, 0) + count
    return result


def parse_spec(spec: str | None) -> dict[str, int]:
    return parse_lipid_text(spec or "")


# ---------------------------------------------------------------------------
# Generatore posizioni lipidi
# ---------------------------------------------------------------------------

def _circle_positions(n: int, radius: float) -> list[tuple[float, float]]:
    """Distribuisce n punti su un cerchio di raggio `radius` nel piano xy."""
    if n <= 0:
        return []
    return [
        (radius * math.cos(2 * math.pi * i / n),
         radius * math.sin(2 * math.pi * i / n))
        for i in range(n)
    ]


def _random_positions(n: int, radius: float, seed: int = 42) -> list[tuple[float, float]]:
    """Distribuisce n punti in modo casuale (mosaico fluido) nel piano xy."""
    if n <= 0:
        return []
    rng = random.Random(seed)
    positions: list[tuple[float, float]] = []
    attempts = 0
    max_attempts = n * 100
    while len(positions) < n and attempts < max_attempts:
        attempts += 1
        # campiona raggio e angolo uniformemente nell'area del cerchio
        r = radius * math.sqrt(rng.random())
        theta = rng.random() * 2 * math.pi
        positions.append((r * math.cos(theta), r * math.sin(theta)))
    return positions


def _lipid_atom_lines(
    resname: str,
    resno: int,
    x: float,
    y: float,
    z_head: float,
    chain_id: str = "A",
) -> list[str]:
    """Genera righe ATOM per un singolo lipide (modello all-atom realistico).

    Geometria:
      - Teste polari a z_head (fuori), con lunghezze di legame realistiche
        (C-C 0.154, C-O 0.143, C-N 0.147, C-P 0.158, P-O 0.148 nm).
      - Code: catena di atomi C con angolo tetraedrico (~112° C-C-C)
        che scende verso TAIL_END_Z (centro bilayer).
    """
    lines: list[str] = []
    n_atoms = LIPID_ATOMS.get(resname, 15)

    # ------------------------------------------------------------------
    # Costanti geometriche (nm)
    # ------------------------------------------------------------------
    BOND_CC = 0.154
    BOND_CO = 0.143
    BOND_CN = 0.147
    BOND_PO = 0.148
    TETRA_RAD = math.radians(112.0)  # angolo C-C-C negli alcani
    TAIL_TILT = 0.3  # inclinazione delle code verso il centro (rad)

    # ------------------------------------------------------------------
    # Testa polare (formato semplificato ma geometricamente coerente)
    # ------------------------------------------------------------------
    # N (colina) in cima, P sotto di essa, O attorno a P,
    # C_glicerolo sotto P che collega alla prima coda

    if resname == "POPC":
        # N3 (colina)
        h_n = (x, y, z_head)
        # P sotto N
        h_p = (x, y, z_head - BOND_CN)
        # O1, O2 attorno a P (fossili del fosfato)
        h_o1 = (x + BOND_PO, y, z_head - BOND_CN)
        h_o2 = (x - BOND_PO, y, z_head - BOND_CN)
        # C3 del glicerolo sotto P, collegato alla prima coda
        h_c3 = (x, y + BOND_CO, z_head - BOND_CN - BOND_PO)

        head_atoms = [
            ("N3",  h_n,  "N",  0.00),
            ("P",   h_p,  "P",  0.00),
            ("O1",  h_o1, "O", -0.50),
            ("O2",  h_o2, "O", -0.50),
            ("C3",  h_c3, "C",  0.00),
        ]
    elif resname == "CHOL":
        # Colesterolo: testa OH + anelli (semplificato a 4 atomi chiave)
        h_o3 = (x, y, z_head)          # ossigeno idrossile
        h_c3 = (x, y, z_head - BOND_CO)  # carbonio C3
        h_c1 = (x + BOND_CC, y, z_head - BOND_CO - BOND_CC)
        h_c2 = (x - BOND_CC, y, z_head - BOND_CO - BOND_CC)

        head_atoms = [
            ("O3", h_o3, "O", -0.50),
            ("C3", h_c3, "C",  0.00),
            ("C1", h_c1, "C",  0.00),
            ("C2", h_c2, "C",  0.00),
        ]
    else:
        # Generico: testa con N, P, O, C
        h_n = (x, y, z_head)
        h_p = (x, y, z_head - BOND_CN)
        h_o1 = (x + BOND_PO, y, z_head - BOND_CN)
        h_c3 = (x, y + BOND_CO, z_head - BOND_CN - BOND_PO)

        head_atoms = [
            ("N",  h_n,  "N",  0.00),
            ("P",  h_p,  "P",  0.00),
            ("O1", h_o1, "O", -0.50),
            ("C3", h_c3, "C",  0.00),
        ]

    for _i, (name, (ox, oy, oz), elem, _charge) in enumerate(head_atoms):
        lines.append(
            f"ATOM  {len(lines)+1:5d} {name:>4s} {resname:>4s}"
            f"{chain_id:1s}{resno:4d}    {ox:8.3f}{oy:8.3f}{oz:8.3f}  "
            f"1.00  0.00          {elem:>2s}"
        )

    # ------------------------------------------------------------------
    # Code: catene di atomi C con angolo tetraedrico verso il centro
    # ------------------------------------------------------------------
    # Per POPC: 2 code (satura + insatura). Per gli altri: 1 coda.
    if resname == "POPC":
        n_tails = 2
        atoms_per_tail = (n_atoms - len(head_atoms)) // 2
    else:
        n_tails = 1
        atoms_per_tail = n_atoms - len(head_atoms)

    for tail_idx in range(n_tails):
        # Ogni coda parte dal C3 del glicerolo
        start_x, start_y, start_z = head_atoms[-1][1]  # C3 posizione
        # Direzione iniziale: verso il basso (z diminuisce) con leggera dispersione
        dir_x = math.sin(TAIL_TILT) * math.cos(tail_idx * math.pi / n_tails)
        dir_y = math.sin(TAIL_TILT) * math.sin(tail_idx * math.pi / n_tails)
        dir_z = -math.cos(TAIL_TILT)

        cur_x, cur_y, cur_z = start_x, start_y, start_z
        prev_dir_x, prev_dir_y, prev_dir_z = dir_x, dir_y, dir_z

        for j in range(atoms_per_tail):
            # Applica angolo tetraedrico: la direzione cambia leggermente a ogni passo
            if j > 0:
                # ruota la direzione di un angolo casuale intorno all'asse z
                rot = TETRA_RAD * 0.3 * (0.5 - (j % 3) / 3.0)
                nx = (
                    prev_dir_x * math.cos(rot)
                    - prev_dir_y * math.sin(rot)
                )
                ny = (
                    prev_dir_x * math.sin(rot)
                    + prev_dir_y * math.cos(rot)
                )
                nz = prev_dir_z * 0.9  # leggera attenuazione verso il basso
                norm = math.sqrt(nx * nx + ny * ny + nz * nz)
                prev_dir_x, prev_dir_y, prev_dir_z = nx / norm, ny / norm, nz / norm

            cur_x += prev_dir_x * BOND_CC
            cur_y += prev_dir_y * BOND_CC
            cur_z += prev_dir_z * BOND_CC

            # forza z verso il centro se si allontana troppo
            target_z = TAIL_END_Z if z_head > 0 else -TAIL_END_Z
            if (z_head > 0 and cur_z < target_z) or (z_head < 0 and cur_z > target_z):
                cur_z = target_z

            lines.append(
                f"ATOM  {len(lines)+1:5d} C{tail_idx+1}{j+1:>3}  {resname:>4s}"
                f"{chain_id:1s}{resno:4d}    {cur_x:8.3f}{cur_y:8.3f}{cur_z:8.3f}  "
                f"1.00  0.00          C"
            )

    return lines


def generate_pdb(
    upper: dict[str, int],
    lower: dict[str, int],
    box_x: float = 12.0,
    box_y: float = 12.0,
    box_z: float = 12.0,
    center: bool = True,
    layout: str = "planar",
) -> str:
    """Genera una stringa PDB di un bilayer con due fogli.

    upper/lower: dizionari {NOME_LIPIDE: count}
    box_x/box_y/box_z: dimensioni della scatola (nm)
    center: centra la struttura nel box
    layout: "planar" (griglia regolare) o "fluid" (mosaico casuale)
    """
    lines: list[str] = []
    lines.append("TITLE    BILAYER GENERATO DA make_bilayer.py (cgui-studio)")
    lines.append(
        "REMARK   Foglio superiore (upper) e inferiore (lower)"
    )
    lines.append(f"REMARK   Scatola: {box_x:.2f} x {box_y:.2f} x {box_z:.2f} nm")
    lines.append(f"REMARK   Layout: {layout}, centro={'si' if center else 'no'}")

    resno = 1
    total_lipids = sum(upper.values()) + sum(lower.values())
    if total_lipids == 0:
        raise ValueError("nessun lipide specificato (upper e lower vuoti)")

    area_per_lipid = math.pi * (LIPID_RADIUS ** 2)
    total_area = total_lipids * area_per_lipid * 1.3
    sheet_radius = math.sqrt(total_area / math.pi)

    # scegli generatore posizioni
    if layout == "fluid":
        upper_positions = _random_positions(sum(upper.values()), sheet_radius * 0.8)
        lower_positions = _random_positions(sum(lower.values()), sheet_radius * 0.8, seed=7)
    else:
        upper_positions = _circle_positions(sum(upper.values()), sheet_radius * 0.8)
        lower_positions = _circle_positions(sum(lower.values()), sheet_radius * 0.8)

    # offset di centratura (sposta il centro di massa a 0,0 se richiesto)
    ox_off = oy_off = 0.0
    if center:
        all_pos = upper_positions + lower_positions
        if all_pos:
            ox_off = -sum(p[0] for p in all_pos) / len(all_pos)
            oy_off = -sum(p[1] for p in all_pos) / len(all_pos)

    # --- Foglio superiore ---
    idx = 0
    for resname in sorted(upper):
        for _ in range(upper[resname]):
            x, y = upper_positions[idx]
            idx += 1
            for line in _lipid_atom_lines(
                resname, resno, x + ox_off, y + oy_off, HEAD_Z, "U",
            ):
                lines.append(line)
            resno += 1

    # --- Foglio inferiore ---
    idx = 0
    for resname in sorted(lower):
        for _ in range(lower[resname]):
            x, y = lower_positions[idx]
            idx += 1
            for line in _lipid_atom_lines(
                resname, resno, x + ox_off, y + oy_off, -HEAD_Z, "L",
            ):
                lines.append(line)
            resno += 1

    lines.append("END")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Generatore TOP e MDP
# ---------------------------------------------------------------------------

def generate_top(
    upper: dict[str, int],
    lower: dict[str, int],
    ion_type: str = "KCl",
    ion_conc: float = 0.15,
    solvent: str = "water",
    ions: str = "KCl",
) -> str:
    """Genera una topologia GROMACS scheletro (force field charmm36).

    solvent: "water" -> aggiunge SOL, "none" -> nessuna acqua
    ions: "KCl"/"NaCl" -> aggiunge ioni, "none" -> nessun ione
    """
    lines: list[str] = []
    lines.append("; Topologia generata da make_bilayer.py (cgui-studio)")
    lines.append("; Force field: charmm36 (verificare disponibilità in $GMXDATA/top)")
    lines.append("")
    lines.append('#include "charmm36.ff/forcefield.itp"')
    lines.append("")

    all_lipids = set(upper) | set(lower)
    for name in sorted(all_lipids):
        lines.append("[ moleculetype ]")
        lines.append("; Name        nrexcl")
        lines.append(f"{name}          3")
        lines.append("")
        lines.append("[ atoms ]")
        lines.append(
            "; nr   atype  resnr  residuum  atom   cgnr     charge   mass"
        )
        for i in range(1, LIPID_ATOMS.get(name, 15) + 1):
            lines.append(
                f"{i:5d}   C       1      {name}     C{i}      1      "
                f"0.00   12.000"
            )
        lines.append("")

    lines.append("[ system ]")
    lines.append("; Name")
    lines.append("Bilayer")
    lines.append("")
    lines.append("[ molecules ]")
    lines.append("; Compound        #mols")
    for name in sorted(all_lipids):
        total_count = upper.get(name, 0) + lower.get(name, 0)
        lines.append(f"{name:<16s} {total_count:6d}")
    lines.append("")

    # Acqua (solo se solvent attivo)
    if solvent == "water":
        n_lipids = sum(upper.values()) + sum(lower.values())
        n_water = max(1, int(n_lipids * 30))
        lines.append(f"SOL             {n_water:6d}")

    # Ioni (solo se ioni attivo e solvent attivo - senza acqua gli ioni non stanno)
    if ions != "none" and solvent == "water":
        n_lipids = sum(upper.values()) + sum(lower.values())
        n_ions = max(1, int(n_lipids * ion_conc * 0.1))
        if "/" in ions:
            ion_cat, ion_an = ions.split("/")
        else:
            ion_cat, ion_an = ions, "CL"
        lines.append(f"{ion_cat:<16s} {n_ions:6d}")
        lines.append(f"{ion_an:<16s} {n_ions:6d}")
    lines.append("")

    return "\n".join(lines) + "\n"


def generate_mdp(box_x: float = 12.0, box_y: float = 12.0, box_z: float = 12.0) -> str:
    """Genera un file .mdp per minimizzazione energia (steepest descent)."""
    return f""";
; Minimizzazione energia - generata da make_bilayer.py (cgui-studio)
;

integrator        = steep
emtol             = 1000.0
emstep            = 0.01
nsteps            = 500
nstenergy         = 100

nstlog            = 100
nstxout           = 0
nstvout           = 0
nstxtcout         = 0

coulombtype       = PME
rcoulomb          = 1.2
rvdw              = 1.2
pbc               = xyz

ref-t             = 300

; Dimensioni scatola (nm) - usate da gmx editconf
; box_x = {box_x:.2f}, box_y = {box_y:.2f}, box_z = {box_z:.2f}
"""


# ---------------------------------------------------------------------------
# Esecuzione GROMACS (opzionale)
# ---------------------------------------------------------------------------

def find_gmx() -> str | None:
    """Trova l'eseguibile gmx nel PATH o nell'ambiente conda attivo."""
    for candidate in ("gmx", "gmx_d", "gmx_mpi"):
        path = subprocess.run(
            ["which", candidate], capture_output=True, text=True
        )
        if path.returncode == 0 and path.stdout.strip():
            return path.stdout.strip()
    conda_prefix = os.environ.get("CONDA_PREFIX")
    if conda_prefix:
        gmx = Path(conda_prefix) / "bin" / "gmx"
        if gmx.exists():
            return str(gmx)
    return None


def run_gmx(
    gmx: str,
    args: list[str],
    cwd: Path,
    input_text: str | None = None,
) -> subprocess.CompletedProcess:
    """Esegue un comando gmx con gestione output."""
    cmd = [gmx] + args
    return subprocess.run(
        cmd, cwd=cwd, input=input_text,
        capture_output=True, text=True, timeout=300,
    )


# ---------------------------------------------------------------------------
# Output e manifest
# ---------------------------------------------------------------------------

def slug_label(label: str) -> str:
    """Slug sicuro per nomi di cartella."""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
    return safe or "bilayer"


def write_manifest(outdir: Path, files: dict[str, Path]) -> None:
    """Scrive manifest.json con sha256 di ogni file."""
    manifest: dict[str, dict] = {}
    for name, path in files.items():
        if path.exists():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            manifest[name] = {
                "path": str(path.relative_to(outdir)),
                "sha256": digest,
                "size": path.stat().st_size,
            }
    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Genera una struttura bilayer all-atom in locale."
    )
    parser.add_argument(
        "--spec", type=str, default=None,
        help='Spec lipidica "POPC=64;CHOL=16" (entrambi i fogli)',
    )
    parser.add_argument(
        "--upper", type=str, default=None,
        help='Composizione foglio superiore (es. "POPC=64")',
    )
    parser.add_argument(
        "--lower", type=str, default=None,
        help='Composizione foglio inferiore (es. "POPC=48;CHOL=16")',
    )
    parser.add_argument(
        "--membtype", type=str, default=None,
        help="Preset membrane del server (ignora upper/lower)",
    )
    parser.add_argument(
        "--margin", type=float, default=20.0,
        help="Margin attorno al sistema (Å, default 20)",
    )
    parser.add_argument(
        "--wdist", type=float, default=1.0,
        help="Distanza teste-ioni (Å, default 1)",
    )
    parser.add_argument(
        "--ion-conc", type=float, default=0.15,
        help="Concentrazione sale (M, default 0.15)",
    )
    parser.add_argument(
        "--ion-type", type=str, default="KCl",
        help="Tipo sale (KCl o NaCl, default KCl)",
    )
    parser.add_argument(
        "--output", type=str, default="results",
        help="Directory base risultati (default results/)",
    )
    parser.add_argument(
        "--run-gmx", action="store_true",
        help="Esegue gmx editconf/solvate se disponibile",
    )
    parser.add_argument(
        "--box-x", type=float, default=12.0,
        help="Dimensione box X in nm (default 12)",
    )
    parser.add_argument(
        "--box-y", type=float, default=12.0,
        help="Dimensione box Y in nm (default 12)",
    )
    parser.add_argument(
        "--box-z", type=float, default=12.0,
        help="Dimensione box Z in nm (default 12)",
    )
    parser.add_argument(
        "--center", action="store_true", default=True,
        help="Centra la struttura a (0,0,0) (default true)",
    )
    parser.add_argument(
        "--no-center", action="store_false", dest="center",
        help="Non centrare la struttura",
    )
    parser.add_argument(
        "--layout", type=str, default="planar",
        choices=["planar", "fluid"],
        help="Disposizione lipidi: planar (griglia regolare) o fluid (mosaico casuale)",
    )
    parser.add_argument(
        "--solvent", type=str, default="water",
        choices=["water", "none"],
        help="Solvente: water (acqua TIP3P) o none",
    )
    parser.add_argument(
        "--ions", type=str, default="KCl",
        choices=["KCl", "NaCl", "none"],
        help="Ioni: KCl, NaCl o none",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    upper = parse_spec(args.upper) if args.upper else {}
    lower = parse_spec(args.lower) if args.lower else {}
    spec = parse_spec(args.spec) if args.spec else {}

    if not upper and not lower:
        if spec:
            upper = dict(spec)
            lower = dict(spec)
        else:
            parser.print_help()
            print("\nERRORE: specifica almeno --spec, --upper o --lower.", file=sys.stderr)
            return 2

    unsupported = (set(upper) | set(lower)) - set(LIPID_ATOMS)
    if unsupported:
        print(
            "AVVISO: lipidi non nel database atomico (15 atomi generici): "
            f"{sorted(unsupported)}", file=sys.stderr,
        )

    today = date.today().isoformat()
    label_parts: list[str] = []
    if args.membtype:
        label_parts.append(args.membtype)
    else:
        for side in (upper, lower):
            for n, c in sorted(side.items()):
                label_parts.append(f"{n}{c}")
    label = "_".join(label_parts) if label_parts else "bilayer"
    outdir = Path(args.output) / f"{today}_make_bilayer_{slug_label(label)}"
    system_dir = outdir / "system"
    system_dir.mkdir(parents=True, exist_ok=True)

    # --- PDB ---
    pdb_path = system_dir / "step1_pdbreader.pdb"
    pdb_content = generate_pdb(
        upper, lower,
        box_x=args.box_x, box_y=args.box_y, box_z=args.box_z,
        center=args.center, layout=args.layout,
    )
    pdb_path.write_text(pdb_content)

    # --- TOP ---
    top_path = system_dir / "topol.top"
    top_content = generate_top(
        upper, lower,
        ion_type=args.ion_type, ion_conc=args.ion_conc,
        solvent=args.solvent, ions=args.ions,
    )
    top_path.write_text(top_content)

    # --- MDP ---
    mdp_path = system_dir / "minim.mdp"
    mdp_path.write_text(
        generate_mdp(box_x=args.box_x, box_y=args.box_y, box_z=args.box_z)
    )

    # --- README ---
    readme_path = outdir / "README.txt"
    readme_path.write_text(
        f"""Bilayer generato da make_bilayer.py (cgui-studio)
Data: {datetime.now().isoformat()}
Spec: upper={dict(upper)}, lower={dict(lower)}
Box: {args.box_x} x {args.box_y} x {args.box_z} nm, centro={args.center}, layout={args.layout}
Solvente: {args.solvent}, Ioni: {args.ions} ({args.ion_type} {args.ion_conc} M)
Force field: charmm36 (scheletro - completare parametri prima di gmx grompp)
File:
  system/step1_pdbreader.pdb  struttura bilayer
  system/topol.top            topologia GROMACS
  system/minim.mdp            minimizzazione energia (steep)
  manifest.json               sha256 dei file
"""
    )

    # --- Esecuzione GROMACS opzionale ---
    gmx_run = False
    if args.run_gmx:
        gmx_path = find_gmx()
        if gmx_path:
            gmx_run = True
            pdb_abs = str(pdb_path.resolve())
            top_abs = str(top_path.resolve())
            boxed_gro = str((system_dir / "boxed.gro").resolve())
            r = run_gmx(
                gmx_path,
                ["editconf", "-f", pdb_abs, "-o", boxed_gro,
                 "-box", f"{args.box_x}", f"{args.box_y}", f"{args.box_z}"],
                cwd=system_dir,
            )
            if r.returncode != 0:
                print(f"AVVISO: gmx editconf fallito: {r.stderr[-500:]}", file=sys.stderr)
            else:
                print("gmx editconf: OK")
                gro_in = system_dir / "boxed.gro"
                if gro_in.exists():
                    solvated_gro = str((system_dir / "solvated.gro").resolve())
                    r = run_gmx(
                        gmx_path,
                        ["solvate", "-cp", str(gro_in.resolve()), "-cs", "spc216.gro",
                         "-o", solvated_gro, "-p", top_abs],
                        cwd=system_dir,
                    )
                    if r.returncode != 0:
                        print(f"AVVISO: gmx solvate fallito: {r.stderr[-500:]}", file=sys.stderr)
                    else:
                        print("gmx solvate: OK")
        else:
            print("AVVISO: --run-gmx richiesto ma gmx non trovato nel PATH.", file=sys.stderr)

    # --- Manifest ---
    files = {
        "pdb": pdb_path,
        "top": top_path,
        "mdp": mdp_path,
        "readme": readme_path,
    }
    write_manifest(outdir, files)

    print(f"Bilayer generato in: {outdir}")
    print(f"  Lipidi totali: {sum(upper.values()) + sum(lower.values())}")
    print("  File: step1_pdbreader.pdb, topol.top, minim.mdp")
    if gmx_run:
        print("  GROMACS: editconf/solvate eseguiti")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
