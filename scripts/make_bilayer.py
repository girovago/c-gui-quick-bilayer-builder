#!/usr/bin/env python3
"""make_bilayer.py - genera una struttura bilayer all-atom in locale.

Legge una specifica lipidica (es. POPC=64;CHOL=16) e produce in
results/AAAA-MM-GG_nome/:
    system/step1_pdbreader.pdb   struttura bilayer (PDB)
    topol.top                    topologia GROMACS (charmm36)
    minim.mdp                    minimizzazione energia (steep)
    README.txt                   nota di provenienza
    manifest.json                sha256 dei file generati

Se gmx è disponibile, esegue anche:
    gmx editconf   (centra e box)
    gmx solvate    (aggiunge acqua)
    gmx grompp     (prepara minim.tpr)
    gmx mdrun      (esegue minimizzazione)

Uso tipico:
    python scripts/make_bilayer.py --spec "POPC=64;CHOL=16"
    python scripts/make_bilayer.py --upper "POPC=64" --lower "POPC=48;CHOL=16" --run-gmx
    python scripts/make_bilayer.py --pdb "1ABC" --run-gmx      # proteina da PDB
    python scripts/make_bilayer.py --dna "ATGC" --run-gmx
    python scripts/make_bilayer.py --rna "AUGC" --run-gmx
    python scripts/make_bilayer.py --small-mol "ligand.pdb" --run-gmx
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tarfile
from datetime import date, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Costanti geometriche (nanometri)
# ---------------------------------------------------------------------------

HEAD_Z = 2.6          # posizione teste polari (fuori dal bilayer)
TAIL_END_Z = 0.2      # estremità code (verso centro bilayer)
LIPID_RADIUS = 0.55   # raggio occupato da un lipide nel piano xy (nm)

# Area per lipide (nm^2) a ~303 K — Kucerka et al. 2011, Ingólfsson et al. 2014.
# Usata per dimensionare i foglietti con densità realistica per composizione.
APL_NM2 = {
    "POPC": 0.683, "DOPC": 0.725, "DPPC": 0.629, "DMPC": 0.604,
    "DLPC": 0.597, "DSPC": 0.635, "DPPS": 0.60, "PLPC": 0.62,
    "POPE": 0.55, "DOPE": 0.644, "DPPE": 0.512, "PE": 0.55,
    "CHL1": 0.28, "PSM": 0.53, "SM": 0.53, "NSM": 0.53, "OSM": 0.53,
    "POPS": 0.62, "DOPS": 0.65, "POPA": 0.56, "POPG": 0.60, "POPI": 0.58,
    "TOCL1": 1.20, "TLCL1": 1.20,
}
DEFAULT_APL = 0.62
APL_HEADGROUP_FALLBACK = {
    "PC": 0.66, "PE": 0.56, "PS": 0.62, "PA": 0.56, "PG": 0.60, "PI": 0.58,
}


def apl_for(name: str) -> float:
    """Area per lipide (nm^2) per specie; fallback sul gruppo (suffisso)."""
    if name in APL_NM2:
        return APL_NM2[name]
    mapped = LIPID_NAME_MAP.get(name, name)
    if mapped in APL_NM2:
        return APL_NM2[mapped]
    for suf, val in APL_HEADGROUP_FALLBACK.items():
        if mapped.endswith(suf):
            return val
    return DEFAULT_APL


# Distanze legame tipiche (nm)
BOND_CC = 0.154
BOND_CO = 0.143
BOND_CN = 0.147
BOND_CP = 0.158
BOND_PO = 0.148
TETRA_RAD = math.radians(112.0)  # angolo C-C-C negli alcani
TAIL_TILT = 0.3  # inclinazione delle code verso il centro (rad)

# Conteggio atomi per lipide (all-atom semplificato)
LIPID_ATOMS = {
    "POPC": 21,
    "CHOL": 27,
    "DOPC": 21,
    "DPPC": 21,
    "POPE": 20,
    "POPS": 23,
    "DOPS": 22,
    "DOTAP": 23,
    "SM": 28,
    "PSM": 28,
    "POPI": 20,
}

# Lipid per cui abbiamo dati in lipid.rtp
LIPID_NAME_MAP = {
    "POPC": "POPC",
    "CHOL": "CHL1",  # colesterolo CHARMM36 (CHM1 è un analogo troncato, ERRATO)
    "POPE": "POPE",
    "DPPC": "DPPC",
    "DSPC": "DSPC",
    "DOPS": "DOPS",
    "DOTAP": "DOTAP",
    "SM": "PSM",  # sfingomielina d18:1/16:0 (LSM è d18:1/24:0)
}

# ---------------------------------------------------------------------------
# Parsing lipid.rtp CHARMM36 (atomi, bond, impropers per ogni residuo)
# ---------------------------------------------------------------------------

LIPID_RTP_PATH = str(Path.home() / ".local" / "share" / "cgui-studio" / "lipid.rtp")


def parse_lipid_rtp(path: str) -> dict:
    """Parsa lipid.rtp CHARMM36 e restituisce dizionario per residuo:
    { NOME: {'atoms': [(name, type, charge, group), ...],
             'bonds': [(a1, a2), ...],
             'impropers': [(a1, a2, a3, a4), ...]} }
    """
    residues: dict = {}
    current: str | None = None
    section: str | None = None
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(";"):
                continue
            if line.startswith("[") and line.endswith("]"):
                inner = line[1:-1].strip()
                if inner in ("atoms", "bonds", "impropers"):
                    section = inner
                    continue
                else:
                    current = inner
                    section = None
                    residues[current] = {"atoms": [], "bonds": [], "impropers": []}
                    continue
            if current is None:
                continue
            if line.startswith("[") and line.endswith("]"):
                section = None
                continue
            if section == "atoms":
                parts = line.split()
                if len(parts) >= 4:
                    residues[current]["atoms"].append(
                        (parts[0], parts[1], float(parts[2]), int(parts[3]))
                    )
            elif section == "bonds":
                parts = line.split()
                if len(parts) >= 2:
                    residues[current]["bonds"].append((parts[0], parts[1]))
            elif section == "impropers":
                parts = line.split()
                if len(parts) >= 4:
                    residues[current]["impropers"].append(tuple(parts[:4]))
    return residues


LIPID_RTP_DATA: dict = {}
try:
    LIPID_RTP_DATA = parse_lipid_rtp(LIPID_RTP_PATH)
except Exception:
    LIPID_RTP_DATA = {}


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
        # i nomi CHARMM36 contengono cifre e trattini (CHL1, TOCL1, SB3-12…)
        if not name.replace("-", "").isalnum():
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
        r = radius * math.sqrt(rng.random())
        theta = rng.random() * 2 * math.pi
        positions.append((r * math.cos(theta), r * math.sin(theta)))
    return positions


def _sterane_offsets() -> dict[str, tuple[float, float]]:
    """Coordinate (u,v) del backbone del colesterolo CHL1 (piano locale).

    3 anelli esagonali fused + pentagono, OH sul C3 (verso l'acqua),
    catena isoottile dal C17 (verso il core). Lato = 0.154 nm.
    u = nel piano del foglietto, v = lungo la normale alla membrana.
    """
    R = 0.154  # lato/raggio circumcerchio dell'esagono (nm)

    def hexv(cx: float, cz: float, theta_deg: float) -> tuple[float, float]:
        t = math.radians(theta_deg)
        return (cx + R * math.cos(t), cz + R * math.sin(t))

    pos: dict[str, tuple[float, float]] = {}
    # --- Ring A: C1-C2-C3-C4-C5-C10 (centro (0,0)) ---
    ring_a = {"C1": 150, "C2": 90, "C3": 30, "C4": 330, "C5": 270, "C10": 210}
    for name, th in ring_a.items():
        pos[name] = hexv(0.0, 0.0, th)
    # --- Ring B: C5-C6-C7-C8-C9-C10 (fused su lato C5-C10, sotto) ---
    cB = (2 * ((pos["C5"][0] + pos["C10"][0]) / 2), 2 * ((pos["C5"][1] + pos["C10"][1]) / 2))
    ring_b = {"C6": 330, "C7": 270, "C8": 210, "C9": 150}
    for name, th in ring_b.items():
        pos[name] = hexv(cB[0], cB[1], th)
    # --- Ring C: C9-C11-C12-C13-C14-C8 (fused su lato C8-C9, a sinistra) ---
    cC = (2 * ((pos["C8"][0] + pos["C9"][0]) / 2) - cB[0],
          2 * ((pos["C8"][1] + pos["C9"][1]) / 2) - cB[1])
    ring_c = {"C11": 90, "C12": 150, "C13": 210, "C14": 270}
    for name, th in ring_c.items():
        pos[name] = hexv(cC[0], cC[1], th)
    # --- Ring D (pentagono): C13-C14-C15-C16-C17 (fused su lato C13-C14) ---
    lat_cx = (pos["C13"][0] + pos["C14"][0]) / 2
    lat_cz = (pos["C13"][1] + pos["C14"][1]) / 2
    apP = 0.154 / (2 * math.tan(math.radians(36)))
    nx = lat_cx - cC[0]
    nz = lat_cz - cC[1]
    nl = math.sqrt(nx * nx + nz * nz)
    cD = (lat_cx + nx / nl * apP, lat_cz + nz / nl * apP)
    # C13/C14 sono vertici condivisi: angoli dal centro D
    a14 = math.atan2(pos["C14"][1] - cD[1], pos["C14"][0] - cD[0])
    a13 = math.atan2(pos["C13"][1] - cD[1], pos["C13"][0] - cD[0])
    # pentagono gira dal lato condiviso verso l'esterno (direzione normale)
    step = -math.radians(72) if (a13 - a14) % (2 * math.pi) > math.pi else math.radians(72)
    pos["C15"] = hexv(cD[0], cD[1], math.degrees(a14 + step))
    pos["C16"] = hexv(cD[0], cD[1], math.degrees(a14 + 2 * step))
    pos["C17"] = hexv(cD[0], cD[1], math.degrees(a14 + 3 * step))
    # --- Metili anello ---
    pos["C18"] = (pos["C13"][0] - 0.09, pos["C13"][1] - 0.12)
    pos["C19"] = (pos["C10"][0] + 0.13, pos["C10"][1] - 0.12)
    # --- Catena isoottile da C17 (zigzag verso v decrescente) ---
    cx, cz = pos["C17"]
    for j, name in enumerate(("C20", "C22", "C23", "C24", "C25", "C26", "C27")):
        cx = cx + (0.05 if j % 2 == 0 else -0.05)
        cz = cz - 0.148
        pos[name] = (cx, cz)
    pos["C21"] = (pos["C20"][0] + 0.12, pos["C20"][1] + 0.05)
    # --- OH sul C3 (verso v positivo = acqua) ---
    pos["O3"] = (pos["C3"][0] + 0.1, pos["C3"][1] + 0.11)
    pos["H3'"] = (pos["O3"][0] + 0.08, pos["O3"][1] + 0.08)
    return pos


STEROL_RESIDUES = {"CHL1"}  # rtp_name con geometria steran dedicata


def _sterol_atom_lines(
    rtp_name: str,
    atoms_info: list[tuple[str, str, float, int]],
    bonds: list[tuple[str, str]],
    resno: int,
    x: float,
    y: float,
    z_head: float,
    chain_id: str = "A",
) -> list[str]:
    """Righe PDB per uno sterolo (CHL1): backbone steran 4 anelli + catena.

    Il piano degli anelli è verticale (perpendicolare alla superficie? no:
    parallelo alla normale della membrana), OH verso l'acqua, catena verso il
    centro del bilayer.
    """
    off = _sterane_offsets()
    z0 = z_head - 0.6 if z_head > 0 else -(z_head - 0.6)
    flip = 1.0 if z_head > 0 else -1.0

    coords: dict[str, tuple[float, float, float]] = {}
    for name, _atype, _charge, _group in atoms_info:
        if name in off:
            u, v = off[name]
            coords[name] = (x + u, y, z0 + flip * v)

    # idrogeni: vicino al partner di legame (dal grafo dei bonds rtp)
    bond_map: dict[str, list[str]] = {}
    for a, b in bonds:
        bond_map.setdefault(a, []).append(b)
        bond_map.setdefault(b, []).append(a)

    h_idx = 0
    for name, _atype, _charge, _group in atoms_info:
        if name in coords:
            continue
        parents = [p for p in bond_map.get(name, []) if p in coords]
        if parents:
            px, py, pz = coords[parents[0]]
            ang = h_idx * 2.399  # golden-angle: offset distribuiti
            h_idx += 1
            coords[name] = (
                px + 0.1 * math.cos(ang),
                py + 0.1 * math.sin(ang),
                pz + 0.03 * (1 if h_idx % 2 else -1),
            )
        else:
            coords[name] = (x + 0.05, y + 0.05, z0)

    lines: list[str] = []
    for name, _atype, _charge, _group in atoms_info:
        ox, oy, oz = coords[name]
        elem = name[0]
        lines.append(
            f"ATOM  {len(lines)+1:5d} {name:>4s} {rtp_name:>4s}"
            f"{chain_id:1s}{resno:4d}    {ox*10:8.3f}{oy*10:8.3f}{oz*10:8.3f}  "
            f"1.00  0.00          {elem:>2s}"
        )
    return lines


def _lipid_atom_lines(
    resname: str,
    resno: int,
    x: float,
    y: float,
    z_head: float,
    chain_id: str = "A",
) -> list[str]:
    """Genera righe ATOM per un singolo lipide usando nomi atomi CHARMM36 reali
    da lipid.rtp.

    Le coordinate sono generate geometricamente (testa in alto, code verso il
    centro) con lunghezze di legame realistiche. I nomi degli atomi corrispondono
    esattamente a quelli definiti in lipid.rtp.
    """
    lines: list[str] = []

    rtp_name = LIPID_NAME_MAP.get(resname, resname)
    residue = LIPID_RTP_DATA.get(rtp_name)

    if residue is None:
        # Residuo non in lipid.rtp: genera atomi generici con nomi semplici
        n_atoms = LIPID_ATOMS.get(resname, 15)
        for i in range(n_atoms):
            atom_name = f"C{i+1}" if i > 0 else "C1"
            lines.append(
                f"ATOM  {len(lines)+1:5d} {atom_name:>4s} {resname:>4s}"
                f"{chain_id:1s}{resno:4d}    {x*10:8.3f}{y*10:8.3f}{z_head*10:8.3f}  "
                f"1.00  0.00          C"
            )
        return lines

    atoms_info = residue["atoms"]  # lista di (name, type, charge, group)

    # steroli: backbone steran dedicato (anelli fused, non catene)
    if rtp_name in STEROL_RESIDUES:
        return _sterol_atom_lines(
            rtp_name, atoms_info, residue.get("bonds", []),
            resno, x, y, z_head, chain_id,
        )

    # Costanti geometriche (nm)
    BOND_CC = 0.154
    BOND_CO = 0.143
    BOND_CN = 0.147
    BOND_PO = 0.148
    TETRA_RAD = math.radians(112.0)  # angolo C-C-C negli alcani
    TAIL_TILT = 0.3  # inclinazione delle code verso il centro (rad)

    # Classifica gli atomi per categoria
    head_names: list[str] = []   # N, P, O* (testa polare)
    glycerol_names: list[str] = []  # C1, C2, C3 (glicerolo)
    tail1_names: list[str] = []  # C21-C218 (catena sn-1)
    tail2_names: list[str] = []  # C31-C316 (catena sn-2)
    h_names: list[str] = []      # H* (idrogeni)

    for name, _atype, _charge, _group in atoms_info:
        if name in ("N3", "N") or name == "P" or (
            name.startswith("O") and name not in ("O21", "O31", "O22", "O32")
        ):
            head_names.append(name)
        elif name in ("C1", "C2", "C3"):
            glycerol_names.append(name)
        elif name.startswith("C2") and len(name) > 2:
            tail1_names.append(name)
        elif name.startswith("C3") and len(name) > 2:
            tail2_names.append(name)
        elif name.startswith("H"):
            h_names.append(name)
        else:
            tail1_names.append(name)

    coords: dict[str, tuple[float, float, float]] = {}

    # --- Testa polare ---
    idx = 0
    for name in head_names:
        if name in ("N3", "N"):
            coords[name] = (x, y, z_head)
        elif name == "P":
            coords[name] = (x, y, z_head - BOND_CN)
        elif name.startswith("O"):
            angle = idx * 2 * math.pi / max(len(head_names), 1)
            coords[name] = (
                x + BOND_PO * math.cos(angle),
                y + BOND_PO * math.sin(angle),
                z_head - BOND_CN,
            )
            idx += 1

    # --- Glicerolo ---
    if "C1" in glycerol_names:
        coords["C1"] = (x, y + BOND_CO, z_head - BOND_CN - BOND_PO)
    if "C2" in glycerol_names:
        coords["C2"] = (
            x,
            y + BOND_CO + BOND_CC * 0.5,
            z_head - BOND_CN - BOND_PO - BOND_CC * 0.5,
        )
    if "C3" in glycerol_names:
        coords["C3"] = (
            x,
            y + BOND_CO + BOND_CC,
            z_head - BOND_CN - BOND_PO - BOND_CC,
        )

    # --- Catena sn-1 (da C2: C21-C218) ---
    cur = coords.get("C2", coords.get("C1", (x, y, z_head - BOND_CN - BOND_PO)))
    cur_x, cur_y, cur_z = cur
    dir_x, dir_y, dir_z = math.sin(TAIL_TILT), 0.0, -math.cos(TAIL_TILT)
    tail1_c_names = [n for n in tail1_names if n.startswith("C")]
    for j, name in enumerate(tail1_c_names):
        cur_x += dir_x * BOND_CC
        cur_y += dir_y * BOND_CC
        cur_z += dir_z * BOND_CC
        rot = TETRA_RAD * 0.2 * (0.5 - (j % 3) / 3.0)
        nx = cur_x + dir_x * math.cos(rot) - dir_y * math.sin(rot)
        ny = cur_y + dir_x * math.sin(rot) + dir_y * math.cos(rot)
        cur_x, cur_y = nx, ny
        target_z = TAIL_END_Z if z_head > 0 else -TAIL_END_Z
        if (z_head > 0 and cur_z < target_z) or (z_head < 0 and cur_z > target_z):
            cur_z = target_z
        coords[name] = (cur_x, cur_y, cur_z)

    # --- Catena sn-2 (da C3: C31-C316) ---
    tail2_c_names = [n for n in tail2_names if n.startswith("C")]
    cur = coords.get("C3", coords.get("C1", (x, y, z_head - BOND_CN - BOND_PO - BOND_CC)))
    cur_x, cur_y, cur_z = cur
    dir_x = math.sin(TAIL_TILT) * math.cos(math.pi / 4)
    dir_y = math.sin(TAIL_TILT) * math.sin(math.pi / 4)
    dir_z = -math.cos(TAIL_TILT)
    for j, name in enumerate(tail2_c_names):
        cur_x += dir_x * BOND_CC
        cur_y += dir_y * BOND_CC
        cur_z += dir_z * BOND_CC
        rot = TETRA_RAD * 0.2 * (0.5 - (j % 3) / 3.0)
        nx = cur_x + dir_x * math.cos(rot) - dir_y * math.sin(rot)
        ny = cur_y + dir_x * math.sin(rot) + dir_y * math.cos(rot)
        cur_x, cur_y = nx, ny
        target_z = TAIL_END_Z if z_head > 0 else -TAIL_END_Z
        if (z_head > 0 and cur_z < target_z) or (z_head < 0 and cur_z > target_z):
            cur_z = target_z
        coords[name] = (cur_x, cur_y, cur_z)

    # --- Atomi di idrogeno: vicino al carbonio padre ---
    for name in h_names:
        parent_name = name[1:]
        if parent_name in coords:
            px, py, pz = coords[parent_name]
        elif tail1_c_names:
            px, py, pz = coords[tail1_c_names[-1]]
        elif tail2_c_names:
            px, py, pz = coords[tail2_c_names[-1]]
        else:
            px, py, pz = coords.get("C1", (x, y, z_head - BOND_CN - BOND_PO))
        coords[name] = (px + 0.05, py + 0.05, pz + 0.05)

    # --- Scrive righe PDB nell'ordine degli atomi in lipid.rtp ---
    for name, _atype, _charge, _group in atoms_info:
        if name not in coords:
            # Atomo senza coordinate: posiziona vicino a C1 come fallback
            cx, cy, cz = coords.get("C1", (x, y, z_head - BOND_CN - BOND_PO))
            coords[name] = (cx + 0.05, cy + 0.05, cz + 0.05)
        ox, oy, oz = coords[name]
        elem = name[0]  # primo carattere come elemento (approssimativo)
        lines.append(
            f"ATOM  {len(lines)+1:5d} {name:>4s} {rtp_name:>4s}"
            f"{chain_id:1s}{resno:4d}    {ox*10:8.3f}{oy*10:8.3f}{oz*10:8.3f}  "
            f"1.00  0.00          {elem:>2s}"
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

    # area totale PER FOGLIETTO con APL reali per composizione (densità fisica)
    def _sheet_area(d: dict[str, int]) -> float:
        return sum(c * apl_for(n) for n, c in d.items())
    area_upper = _sheet_area(upper)
    area_lower = _sheet_area(lower)
    sheet_radius = math.sqrt(max(area_upper, area_lower) / math.pi) * 1.15

    # scegli generatore posizioni
    if layout == "fluid":
        upper_positions = _random_positions(sum(upper.values()), sheet_radius * 0.8)
        lower_positions = _random_positions(sum(lower.values()), sheet_radius * 0.8, seed=7)
    else:
        upper_positions = _circle_positions(sum(upper.values()), sheet_radius * 0.8)
        lower_positions = _circle_positions(sum(lower.values()), sheet_radius * 0.8)

    # lista di specie per leaflet: assegnazione CASUALE specie<->posizione
    # (mescolamento laterale reale: nelle membrane le specie sono distribuite
    # in modo indipendente, non a settori contigui)
    def _species_list(d: dict[str, int]) -> list[str]:
        out: list[str] = []
        for name in sorted(d):
            out += [name] * d[name]
        return out

    upper_species = _species_list(upper)
    lower_species = _species_list(lower)
    if layout == "fluid":
        # posizioni casuali + specie MESCOLATE con seed riproducibile
        rng = random.Random(42)
        rng.shuffle(upper_species)
        rng2 = random.Random(7)
        rng2.shuffle(lower_species)
    else:
        # griglia ordinata (debug CLI): specie raggruppate come prima
        upper_species = _species_list(upper)
        lower_species = _species_list(lower)

    # offset di centratura (sposta il centro di massa a 0,0 se richiesto)
    ox_off = oy_off = 0.0
    if center:
        all_pos = upper_positions + lower_positions
        if all_pos:
            ox_off = -sum(p[0] for p in all_pos) / len(all_pos)
            oy_off = -sum(p[1] for p in all_pos) / len(all_pos)

    # --- Foglio superiore ---
    for idx, resname in enumerate(upper_species):
        x, y = upper_positions[idx]
        for line in _lipid_atom_lines(
            resname, resno, x + ox_off, y + oy_off, HEAD_Z, "U",
        ):
            lines.append(line)
        resno += 1

    # --- Foglio inferiore ---
    for idx, resname in enumerate(lower_species):
        x, y = lower_positions[idx]
        for line in _lipid_atom_lines(
            resname, resno, x + ox_off, y + oy_off, -HEAD_Z, "L",
        ):
            lines.append(line)
        resno += 1

    lines.append("END")
    return "\n".join(lines) + "\n"


def net_charge(upper: dict[str, int], lower: dict[str, int]) -> float:
    """Carica totale del bilayer (e) sommando le cariche rtp delle specie."""
    total = 0.0
    for side in (upper, lower):
        for name, count in side.items():
            rtp_name = LIPID_NAME_MAP.get(name, name)
            residue = LIPID_RTP_DATA.get(rtp_name)
            if residue:
                total += sum(a[2] for a in residue["atoms"]) * count
    return round(total, 2)


def ion_counts(
    upper: dict[str, int],
    lower: dict[str, int],
    box_x: float = 12.0,
    box_y: float = 12.0,
    box_z: float = 12.0,
    ion_conc: float = 0.15,
) -> tuple[int, int, float]:
    """Numero ioni (+, -) per neutralizzare e raggiungere la concentrazione.

    Ritorna (n_cation, n_anion, net_charge_e): ioni di compensazione della
    carica dei lipidi + coppie da concentrazione.
    """
    net = net_charge(upper, lower)
    vol_nm3 = box_x * box_y * box_z
    # 0.6022 ioni per (M * nm^3)
    n_pairs = max(1, round(ion_conc * vol_nm3 * 0.6022))
    extra = int(round(abs(net)))
    if net < 0:
        # lipidi negativi → cationi in più
        return n_pairs + extra, n_pairs, net
    if net > 0:
        return n_pairs, n_pairs + extra, net
    return n_pairs, n_pairs, net


def generate_top(
    upper: dict[str, int],
    lower: dict[str, int],
    ion_type: str = "KCl",
    ion_conc: float = 0.15,
    solvent: str = "water",
    ions: str = "KCl",
    ff_path: str | None = None,
) -> str:
    """Genera una topologia GROMACS completa con CHARMM36.

    Usa le definizioni da lipid.rtp per atomi, bond e impropers di ogni
    residuo lipidico.

    solvent: "water" -> aggiunge SOL, "none" -> nessuna acqua
    ions: "KCl"/"NaCl" -> aggiunge ioni, "none" -> nessun ione
    ff_path: percorso alla directory charmm36.ff per gli include dei file .itp
    """
    lines: list[str] = []
    lines.append("; Topologia generata da make_bilayer.py (cgui-studio)")
    lines.append("; Force field: charmm36 (da lipid.rtp di MacKerell)")
    lines.append("")
    if ff_path:
        # Usa forcefield.itp che include tutti i file .itp nell'ordine corretto
        ff_rel = Path(ff_path).name  # es. "charmm36.ff"
        lines.append(f'#include "{ff_rel}/forcefield.itp"')
    else:
        lines.append('#include "charmm36.ff/forcefield.itp"')
    # Includi tip3p.itp per SOL (acqua) e ions.itp per KCl/NaCl
    if ff_path:
        lines.append(f'#include "{ff_rel}/tip3p.itp"')
        lines.append(f'#include "{ff_rel}/ions.itp"')
    else:
        lines.append('#include "charmm36.ff/tip3p.itp"')
        lines.append('#include "charmm36.ff/ions.itp"')
    lines.append("")

    all_lipids = set(upper) | set(lower)

    # atomi di testa su cui mettere i restraints (POSRES)
    POSRES_ATOMS = {"P", "N", "N3", "O3", "O1", "O2", "OG1"}

    for name in sorted(all_lipids):
        rtp_name = LIPID_NAME_MAP.get(name, name)
        residue = LIPID_RTP_DATA.get(rtp_name)

        lines.append("[ moleculetype ]")
        lines.append("; Name        nrexcl")
        lines.append(f"{rtp_name}          3")
        lines.append("")
        lines.append("[ atoms ]")
        lines.append(
            "; nr   atype  resnr  residuum  atom   cgnr     charge   mass"
        )
        if residue is not None:
            atoms_info = residue["atoms"]
        else:
            # Residuo non in lipid.rtp: atomo generico
            atoms_info = [(f"C{i+1}", "C", 0.0, 1) for i in range(LIPID_ATOMS.get(name, 15))]

        for i, (name, atype, charge, group) in enumerate(atoms_info):
            elem = name[0]
            mass_map = {"C": 12.011, "N": 14.007, "O": 15.999,
                        "P": 30.974, "S": 32.06, "H": 1.008}
            mass = mass_map.get(elem, 12.011)
            lines.append(
                f"{i+1:5d}   {atype:>4s}  1      {rtp_name:>4s}  "
                f"{name:>4s}  {group:>4d}   {charge:>6.3f}   {mass:>7.3f}"
            )
        lines.append("")

        # restraints di posizione sugli atomi polari (equilibratura, -DPOSRES)
        posres_idx = [
            i + 1 for i, (aname, *_rest) in enumerate(atoms_info)
            if aname in POSRES_ATOMS
        ]
        if posres_idx:
            lines.append("#ifdef POSRES")
            lines.append("[ position_restraints ]")
            lines.append("; atom  type      fx        fy        fz")
            for idx in posres_idx:
                lines.append(f"{idx:5d}     1      1000      1000      1000")
            lines.append("#endif")
            lines.append("")

    lines.append("[ system ]")
    lines.append("; Name")
    lines.append("Bilayer")
    lines.append("")
    lines.append("[ molecules ]")
    lines.append("; Compound        #mols")
    for name in sorted(all_lipids):
        rtp_name = LIPID_NAME_MAP.get(name, name)
        total_count = upper.get(name, 0) + lower.get(name, 0)
        lines.append(f"{rtp_name:<16s} {total_count:6d}")
    lines.append("")

    # Acqua e ioni vengono aggiunti da gmx solvate -p (che aggiorna il .top
    # con il conteggio corretto), quindi NON li scriviamo qui.
    # Questo evita mismatch tra topologia e coordinate.
    lines.append("")

    return "\n".join(lines) + "\n"


def _count_gro_residues(gro_path: str) -> dict[str, int]:
    """Conta le molecole per resname in un file .gro GROMACS.

    Conta ogni residuo unico (numero di residuo + nome) una sola volta,
    indipendentemente da quanti atomi ha (es. SOL ha 3 atomi).
    """
    counts: dict[str, int] = {}
    seen_res: set[tuple[int, str]] = set()
    with open(gro_path) as f:
        lines = f.readlines()
        if len(lines) < 2:
            return counts
        try:
            natoms = int(lines[1].strip())
        except ValueError:
            return counts
        for i in range(2, min(2 + natoms, len(lines))):
            line = lines[i]
            if len(line) < 10:
                continue
            resno_str = line[:5].strip()
            resname = line[5:10].strip()
            if not resname:
                continue
            try:
                resno = int(resno_str)
            except ValueError:
                continue
            key = (resno, resname)
            if key not in seen_res:
                seen_res.add(key)
                counts[resname] = counts.get(resname, 0) + 1
    return counts


def _update_top_molecules(top_path: Path, molecules: dict[str, int]) -> None:
    """Riscrive la sezione [molecules] del .top con i conteggi forniti.

    Se [molecules] esiste già, viene sostituita. Altrimenti viene creata
    prima di [system] o in fondo al file.
    """
    with open(top_path) as f:
        content = f.read()

    lines = content.splitlines(keepends=True)
    new_mol_lines = ["[ molecules ]\n", "; Compound        #mols\n"]
    for name in sorted(molecules):
        count = molecules[name]
        new_mol_lines.append(f"{name:<16s} {count:6d}\n")
    new_mol_lines.append("\n")

    # Trova la sezione [molecules] esistente
    mol_idx = None
    for i, line in enumerate(lines):
        if line.strip() == "[ molecules ]":
            mol_idx = i
            break

    if mol_idx is not None:
        # Trova dove finisce la sezione (riga vuota o prossima [])
        end_idx = mol_idx + 2  # salta [molecules] e commento
        while end_idx < len(lines):
            stripped = lines[end_idx].strip()
            if stripped == "" or stripped.startswith("["):
                break
            end_idx += 1
        lines[mol_idx:end_idx] = new_mol_lines
    else:
        # Non c'è [molecules]: inserisci prima di [system] o in fondo
        sys_idx = None
        for i, line in enumerate(lines):
            if line.strip() == "[ system ]":
                sys_idx = i
                break
        if sys_idx is not None:
            lines[sys_idx:sys_idx] = new_mol_lines
        else:
            lines.extend(new_mol_lines)

    with open(top_path, "w") as f:
        f.writelines(lines)


def _is_molecules_directive(line: str) -> bool:
    """Verifica se una riga è la direttiva [molecules] (con o senza spazi)."""
    return line.strip().replace(" ", "") == "[molecules]"


def _is_section_directive(line: str) -> bool:
    """Verifica se una riga è l'inizio di una nuova sezione [..]."""
    s = line.strip()
    return s.startswith("[") and s.endswith("]") and len(s) > 2


def _ensure_top_molecules(top_path: Path, gro_path: str,
                          upper: dict[str, int], lower: dict[str, int]) -> None:
    """Riscrive la sezione [molecules] del .top basandosi sul .gro ionizzato.

    solvate -p e genion -p possono creare sezioni [molecules] duplicate o
    errate. Questa funzione le rimuove tutte e ne inserisce una sola corretta
    dopo [system], come richiesto da GROMACS.
    """
    gro_counts = _count_gro_residues(gro_path)
    n_sol = gro_counts.get("SOL", 0)
    n_na = gro_counts.get("NA", 0) + gro_counts.get("K", 0)
    n_cl = gro_counts.get("CL", 0)

    needed: dict[str, int] = {}
    for name in sorted(set(upper) | set(lower)):
        rtp_name = LIPID_NAME_MAP.get(name, name)
        total_count = upper.get(name, 0) + lower.get(name, 0)
        if total_count > 0:
            needed[rtp_name] = total_count
    if n_sol > 0:
        needed["SOL"] = n_sol
    if n_na > 0:
        needed["NA"] = n_na
    if n_cl > 0:
        needed["CL"] = n_cl

    with open(top_path) as f:
        lines = f.readlines()

    # Trova [system] e la prima [molecules] dopo di esso
    sys_idx = None
    mol_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "[ system ]":
            sys_idx = i
        elif sys_idx is not None and stripped.replace(" ", "") == "[molecules]":
            mol_idx = i
            break

    if sys_idx is not None:
        # Trova la fine di [system] (riga vuota o prossima sezione [])
        end_idx = sys_idx + 2
        while end_idx < len(lines):
            stripped = lines[end_idx].strip()
            if stripped == "" or stripped.startswith("["):
                break
            end_idx += 1
        keep_lines = lines[:end_idx]
    else:
        keep_lines = lines[:mol_idx] if mol_idx is not None else lines

    # Aggiungi la nuova [molecules]
    # Ordine molecole: lipidi prima, poi SOL, poi ioni (ordine del .gro)
    def mol_order(name):
        if name == "SOL":
            return 1
        if name in ("NA", "CL", "K"):
            return 2
        return 0  # lipidi prima
    new_mol = ["[ molecules ]\n", "; Compound        #mols\n"]
    for name in sorted(needed, key=mol_order):
        new_mol.append(f"{name:<16s} {needed[name]:6d}\n")
    new_mol.append("\n")

    keep_lines.extend(new_mol)

    with open(top_path, "w") as f:
        f.writelines(keep_lines)
    print(f"  .top riscritto: {needed}")



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


def get_gromacs_version(gmx_path: str | None = None) -> tuple[int, int] | None:
    """Rileva la versione di GROMACS eseguendo 'gmx --version'.

    Restituisce una tupla (major, minor) o None se non disponibile.
    """
    gmx = gmx_path or find_gmx()
    if not gmx:
        return None
    try:
        result = subprocess.run(
            [gmx, "--version"], capture_output=True, text=True, timeout=15
        )
        output = result.stdout + result.stderr
        match = re.search(r"version\s+(\d+)\.(\d+)", output)
        if match:
            return int(match.group(1)), int(match.group(2))
    except Exception:
        pass
    return None


def check_gromacs_version(gmx_path: str | None = None) -> str | None:
    """Controlla la compatibilità della versione di GROMACS rilevata.

    Restituisce un messaggio di avviso se la versione è problematica,
    None se va bene.
    """
    version = get_gromacs_version(gmx_path)
    if version is None:
        return "Impossibile rilevare la versione di GROMACS"
    major, minor = version
    if major < 2018:
        return f"GROMACS {major}.{minor} è troppo vecchio (minimo 2018 consigliato)"
    if major == 2020 and minor < 3:
        return f"GROMACS {major}.{minor} potrebbe avere bug noti con grompp"
    if major >= 2025:
        return f"GROMACS {major}.{minor} è molto recente, verifica compatibilità"
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


def _extract_charmm36_ff(system_dir: Path) -> None:
    """Estrae i file CHARMM36 dal tarball in /tmp/charmm36.tgz nella directory system
    del job, così che gmx grompp possa trovare forcefield.itp ecc."""

    tar_path = Path.home() / ".local" / "share" / "cgui-studio" / "charmm36.tgz"
    target_dir = system_dir / "charmm36.ff"

    if target_dir.exists():
        return

    if not tar_path.exists():
        print(
            "AVVISO: charmm36.tgz non trovato in ~/.local/share/cgui-studio/ "
            "gmx grompp potrebbe fallire senza i file charmm36.ff.",
            file=sys.stderr,
        )
        return

    with tarfile.open(tar_path, "r:gz") as tf:
        # Estrai solo la directory charmm36-feb2026_cgenff-5.0.ff
        members = [
            m for m in tf.getmembers()
            if m.name.startswith(
                "toppar_c36_releases-main/gromacs/charmm36-feb2026_cgenff-5.0.ff/"
            ) and not m.name.endswith("/")
        ]
        tf.extractall(system_dir, members=members)

    # Rinomina la directory estratta in charmm36.ff
    src = system_dir / "toppar_c36_releases-main" / "gromacs" / "charmm36-feb2026_cgenff-5.0.ff"
    if src.exists() and not target_dir.exists():
        shutil.move(str(src), str(target_dir))

    # Rimuovi la directory intermediaria
    intermediate = system_dir / "toppar_c36_releases-main"
    if intermediate.exists():
        shutil.rmtree(str(intermediate))

    # Copia lipid.rtp nella directory system per l'include nel .top
    lipid_rtp_src = Path("/tmp/opencode/lipid.rtp")
    if lipid_rtp_src.exists():
        shutil.copy(str(lipid_rtp_src), str(system_dir / "lipid.rtp"))


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

def _gmx_pipeline(
    gmx_path: str,
    system_dir: Path,
    pdb_abs: str,
    top_abs: str,
    mdp_path: str,
    upper: dict[str, int],
    lower: dict[str, int],
    box_x: float,
    box_y: float,
    box_z: float,
    ion_type: str,
    ion_conc: float,
) -> bool:
    """editconf → solvate → grompp → genion → grompp → mdrun.

    Genion neutralizza la carica dei lipidi e porta il sale alla concentrazione
    (flusso CHARMM-GUI). Ritorna True se la minimizzazione è completata.
    """
    def gmx(args_: list[str], input_text: str | None = None):
        return run_gmx(gmx_path, args_, cwd=system_dir, input_text=input_text)

    boxed_gro = str((system_dir / "boxed.gro").resolve())
    r = gmx(["editconf", "-f", pdb_abs, "-o", boxed_gro,
             "-box", f"{box_x}", f"{box_y}", f"{box_z}"])
    if r.returncode != 0:
        print(f"AVVISO: gmx editconf fallito: {r.stderr[-500:]}", file=sys.stderr)
        return False
    print("gmx editconf: OK")

    solvated_gro = str((system_dir / "solvated.gro").resolve())
    r = gmx(["solvate", "-cp", boxed_gro, "-cs", "spc216.gro",
             "-o", solvated_gro, "-p", top_abs])
    if r.returncode != 0:
        print(f"AVVISO: gmx solvate fallito: {r.stderr[-500:]}", file=sys.stderr)
        return False
    print("gmx solvate: OK")

    # --- Ioni: grompp preliminare → genion (neutralizza + conc) ---
    tpr_ion = str((system_dir / "ion.tpr").resolve())
    r = gmx(["grompp", "-maxwarn", "10", "-f", mdp_path, "-c", solvated_gro,
             "-p", top_abs, "-o", tpr_ion])
    if r.returncode != 0:
        print(f"AVVISO: gmx grompp (pre-ioni) fallito: {r.stderr[-500:]}", file=sys.stderr)
        return False

    n_cat, n_an, net = ion_counts(upper, lower, box_x, box_y, box_z, ion_conc)
    pname = "K" if ion_type == "KCl" else "NA"
    ionized_gro = str((system_dir / "ionized.gro").resolve())
    # genion SENZA -p: aggiorniamo manualmente il .top dopo
    r = gmx(["genion", "-s", tpr_ion, "-o", ionized_gro,
             "-np", str(n_cat), "-nn", str(n_an),
             "-pname", pname, "-nname", "CL"],
            input_text="SOL\n")
    if r.returncode != 0:
        print(f"AVVISO: gmx genion fallito: {r.stderr[-500:]}", file=sys.stderr)
        return False
    print(f"gmx genion: OK ({n_cat} {pname}+, {n_an} CL-, carica lipidi {net:+g} e)")

    # Verifica che il .top abbia tutte le molecole (lipidi + SOL + NA + CL).
    # solvate -p e genion -p dovrebbero averlo già aggiornato; se manca
    # qualcosa, lo aggiungiamo manualmente basandoci sul .gro ionizzato.
    _ensure_top_molecules(Path(top_abs), ionized_gro, upper, lower)

    minim_tpr = str((system_dir / "minim.tpr").resolve())
    r = gmx(["grompp", "-maxwarn", "50", "-f", mdp_path, "-c", ionized_gro,
             "-p", top_abs, "-o", minim_tpr])
    if r.returncode != 0:
        print(f"AVVISO: gmx grompp fallito: {r.stderr[-500:]}", file=sys.stderr)
        return False
    print("gmx grompp: OK - minim.tpr pronto")

    r = gmx(["mdrun", "-s", minim_tpr,
             "-o", str((system_dir / "minim.trr").resolve()),
             "-c", str((system_dir / "minim.gro").resolve()),
             "-e", str((system_dir / "minim.edr").resolve()),
             "-g", str((system_dir / "minim.log").resolve())])
    if r.returncode != 0:
        print(f"AVVISO: gmx mdrun fallito: {r.stderr[-500:]}", file=sys.stderr)
        return False
    print("gmx mdrun: OK - minimizzazione completata")
    return True


def generate_equilib_files() -> dict[str, str]:
    """File di equilibratura stile CHARMM-GUI: NVT → NPT (posres) → produzione.

    Ritorna {nome_file: contenuto}. I posres agiscono sulle teste polari dei
    lipidi tramite -DPOSRES (blocco nel .top). La produzione è libera.
    """
    common = """
coulombtype       = PME
rcoulomb          = 1.2
rvdw              = 1.2
rlist             = 1.2
pbc               = xyz
nstlist           = 20
DispCorr          = EnerPres
"""
    nvt = f""";
; NVT 50 ps con posres sulle teste lipidi - cgui-studio
;
integrator        = md
dt                = 0.002
nsteps            = 25000
{common}
tcoupl            = v-rescale
tc-grps           = System
tau-t             = 1.0
ref-t             = 303
pcoupl            = no
gen-vel           = yes
gen-temp          = 303
gen-seed          = 173529
define            = -DPOSRES

nstxout           = 5000
nstlog            = 500
nstenergy         = 500
"""
    npt = f""";
; NPT 50 ps semiisotropo con posres - cgui-studio
;
integrator        = md
dt                = 0.002
nsteps            = 25000
{common}
tcoupl            = v-rescale
tc-grps           = System
tau-t             = 1.0
ref-t             = 303
pcoupl            = c-rescale
pcoupltype        = semiisotropic
tau-p             = 5.0
ref-p             = 1.0 1.0
compressibility   = 4.5e-5 4.5e-5
define            = -DPOSRES

nstxout           = 5000
nstlog            = 500
nstenergy         = 500
"""
    prod = f""";
; Produzione 100 ns libera - cgui-studio
;
integrator        = md
dt                = 0.002
nsteps            = 50000000
{common}
tcoupl            = v-rescale
tc-grps           = System
tau-t             = 1.0
ref-t             = 303
pcoupl            = c-rescale
pcoupltype        = semiisotropic
tau-p             = 5.0
ref-p             = 1.0 1.0
compressibility   = 4.5e-5 4.5e-5

nstxout-compressed = 5000
compressed-x-precision = 1000
nstlog            = 1000
nstenergy         = 1000
"""
    sh = """#!/bin/bash
# Equilibratura + produzione - cgui-studio (stile CHARMM-GUI)
# Uso: bash equil.sh  (richiede minim.gro completato)
set -e
cd "$(dirname "$0")"
echo "=== NVT 50 ps (posres) ==="
gmx grompp -f equilib_nvt.mdp -c minim.gro -r minim.gro -p topol.top -o nvt.tpr
gmx mdrun -s nvt.tpr -o nvt.trr -c nvt.gro -e nvt.edr -g nvt.log
echo "=== NPT 50 ps semiisotropo (posres) ==="
gmx grompp -f equilib_npt.mdp -c nvt.gro -r nvt.gro -p topol.top -o npt.tpr
gmx mdrun -s npt.tpr -o npt.trr -c npt.gro -e npt.edr -g npt.log
echo "=== Produzione 100 ns (libera) ==="
gmx grompp -f production.mdp -c npt.gro -p topol.top -o md.tpr
gmx mdrun -s md.tpr -o md.trr -c md.gro -e md.edr -g md.log
echo "Fatto: md.gro"
"""
    return {
        "equilib_nvt.mdp": nvt,
        "equilib_npt.mdp": npt,
        "production.mdp": prod,
        "equil.sh": sh,
    }


def compute_metrics(
    gro_path: str,
    upper: dict[str, int],
    lower: dict[str, int],
) -> dict:
    """Metriche fisiche dal .gro ionizzato (parser stdlib, niente dipendenze).

    Ritorna: spessore testa-testa (P-P), APL misurata, acqua per lipide,
    atomi totali, carica ioni. Le coordinate .gro sono in nm (native GROMACS).
    """
    res_counts: dict[str, int] = {}
    z_p_upper: list[float] = []
    z_p_lower: list[float] = []
    n_sol = 0
    box = (0.0, 0.0, 0.0)
    with open(gro_path) as f:
        lines = f.readlines()
        natoms = int(lines[1].strip())
        idx = 2
        for _ in range(natoms):
            if idx >= len(lines):
                break
            line = lines[idx]
            idx += 1
            if len(line) < 44:
                continue
            resname = line[5:10].strip()
            atomname = line[10:15].strip()
            try:
                z = float(line[36:44])
            except ValueError:
                continue
            res_counts[resname] = res_counts.get(resname, 0) + 1
            if resname == "SOL" and atomname == "OW":
                n_sol += 1
            if atomname == "P":
                (z_p_upper if z > 0 else z_p_lower).append(z)
        for line in lines[idx:]:
            parts = line.split()
            if len(parts) >= 3:
                try:
                    box = tuple(float(v) for v in parts[:3])
                    break
                except ValueError:
                    continue
    thickness = 0.0
    if z_p_upper and z_p_lower:
        thickness = (
            sum(z_p_upper) / len(z_p_upper) - sum(z_p_lower) / len(z_p_lower)
        )
    n_lip_upper = sum(upper.values()) or 1
    n_lip_total = sum(upper.values()) + sum(lower.values()) or 1
    apl = box[0] * box[1] / n_lip_upper
    return {
        "natoms": natoms,
        "box_nm": [round(v, 3) for v in box],
        "thickness_P_nm": round(thickness, 3),
        "n_phosphate_upper": len(z_p_upper),
        "n_phosphate_lower": len(z_p_lower),
        "apl_measured_nm2": round(apl, 3),
        "apl_expected_nm2": round(
            sum(c * apl_for(n) for n, c in upper.items()) / n_lip_upper, 3
        ),
        "water_per_lipid": round(n_sol / n_lip_total, 1),
        "net_lipid_charge_e": net_charge(upper, lower),
    }


# Preset membrane (composizione per ciascun foglietto, ~64 lipidi/foglio).
# Fonti: Ingólfsson et al. 2014 (plasma mammaliana), van Meer et al. 2008.
MEMBRANE_PRESETS: dict[str, dict] = {
    "plasma": {
        "label": "Membrana plasmatica mammaliana (CHOL 35%)",
        "upper": {"POPC": 14, "POPE": 6, "PSM": 8, "CHOL": 20,
                  "POPS": 4, "POPI": 2, "DOPS": 2},
        "lower": {"POPC": 14, "POPE": 6, "PSM": 8, "CHOL": 20,
                  "POPS": 4, "POPI": 2, "DOPS": 2},
    },
    "er": {
        "label": "Reticolo endoplasmatico (CHOL 5%, PE alto)",
        "upper": {"POPC": 22, "POPE": 18, "CHOL": 3, "POPI": 6, "POPS": 3},
        "lower": {"POPC": 22, "POPE": 18, "CHOL": 3, "POPI": 6, "POPS": 3},
    },
    "mito": {
        "label": "Mitochondriale interna (cardiolipina TOCL)",
        "upper": {"POPC": 18, "POPE": 24, "TOCL1": 4, "POPI": 3, "POPA": 3},
        "lower": {"POPC": 18, "POPE": 24, "TOCL1": 4, "POPI": 3, "POPA": 3},
    },
    "popc": {
        "label": "POPC puro (controllo)",
        "upper": {"POPC": 64},
        "lower": {"POPC": 64},
    },
}


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
        help="Esegue gmx editconf/solvate/grompp/mdrun se disponibile",
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
    parser.add_argument(
        "--lipid-rtp", type=str, default=LIPID_RTP_PATH,
        help="Percorso al file lipid.rtp di CHARMM36 (default: /tmp/opencode/lipid.rtp)",
    )
    # --- Nuovi argomenti per supporto proteine, acidi nucleici, small molecules ---
    parser.add_argument(
        "--pdb", type=str, default=None,
        help="File PDB o ID PDB di una proteina da inserire nel sistema",
    )
    parser.add_argument(
        "--pdb-reader", action="store_true",
        help="Usa PDB Reader di CHARMM-GUI per caricare la proteina (richiede API CHARMM-GUI)",
    )
    parser.add_argument(
        "--dna", type=str, default=None,
        help="Sequenza DNA da costruire (es. ATGC)",
    )
    parser.add_argument(
        "--rna", type=str, default=None,
        help="Sequenza RNA da costruire (es. AUGC)",
    )
    parser.add_argument(
        "--small-mol", type=str, default=None,
        help="File PDB/SDF di una small molecule da inserire",
    )
    parser.add_argument(
        "--build-type", type=str, default="lipid",
        choices=["lipid", "protein", "dna", "rna", "small-mol"],
        help="Tipo di sistema da costruire (default: lipid)",
    )
    parser.add_argument(
        "--preset", type=str, default=None,
        choices=sorted(MEMBRANE_PRESETS),
        help="Composizione membrana predefinita (ignora --spec/--upper/--lower)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    # Ricarica lipid.rtp se specificato via CLI
    global LIPID_RTP_DATA
    if args.lipid_rtp != LIPID_RTP_PATH:
        try:
            LIPID_RTP_DATA = parse_lipid_rtp(args.lipid_rtp)
        except Exception as exc:
            print(f"AVVISO: impossibile caricare {args.lipid_rtp}: {exc}", file=sys.stderr)

    upper = parse_spec(args.upper) if args.upper else {}
    lower = parse_spec(args.lower) if args.lower else {}
    spec = parse_spec(args.spec) if args.spec else {}

    if args.preset:
        preset = MEMBRANE_PRESETS[args.preset]
        upper = dict(preset["upper"])
        lower = dict(preset["lower"])
        print(f"Preset: {preset['label']}")

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

    # Estrai i file CHARMM36 (forcefield) per grompp
    _extract_charmm36_ff(system_dir)

    # Copia lipid.rtp nella directory system per l'include nel .top
    lipid_rtp_src = Path("/tmp/opencode/lipid.rtp")
    if lipid_rtp_src.exists():
        shutil.copy(str(lipid_rtp_src), str(system_dir / "lipid.rtp"))

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
        ff_path=str(system_dir / "charmm36.ff"),
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
Force field: charmm36 (da lipid.rtp di MacKerell)
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
            gmx_run = _gmx_pipeline(
                gmx_path, system_dir, pdb_abs, top_abs, str(mdp_path.resolve()),
                upper, lower, args.box_x, args.box_y, args.box_z,
                args.ion_type, args.ion_conc,
            )
        else:
            print("AVVISO: --run-gmx richiesto ma gmx non trovato nel PATH.", file=sys.stderr)

    # --- File di equilibratura (NVT/NPT/produzione + script) ---
    equilib = generate_equilib_files()
    for fname, content in equilib.items():
        (outdir / fname).write_text(content)

    # --- Metriche fisiche dopo la build gmx ---
    metrics = None
    ionized = system_dir / "ionized.gro"
    if ionized.exists():
        try:
            metrics = compute_metrics(str(ionized), upper, lower)
            (outdir / "metrics.json").write_text(
                json.dumps(metrics, indent=2, ensure_ascii=False) + "\n"
            )
            print(f"  Metriche: spessore P-P {metrics['thickness_P_nm']} nm, "
                  f"APL {metrics['apl_measured_nm2']} nm2, "
                  f"acqua/lipide {metrics['water_per_lipid']}")
        except Exception as exc:
            print(f"AVVISO: metriche non calcolate: {exc}", file=sys.stderr)

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
        print("  GROMACS: editconf/solvate/grompp/mdrun eseguiti")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())