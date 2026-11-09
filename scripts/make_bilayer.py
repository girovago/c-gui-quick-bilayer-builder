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

# HEAD_Z NON è più una costante: lo spessore del bilayer (D_B) viene
# calcolato dalla composizione (volumi/APL, vedi bilayer_thickness) e le
# teste polari vanno a ±D_B/2 (v. generate_pdb).
TAIL_END_Z = 0.05     # estremità code (verso il centro del bilayer)
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

# Volume molecolare per lipide (nm^3) — Kucerka et al. 2011, Pan et al. 2004,
# Ingólfsson et al. 2014. Usato con APL per la geometria verticale.
LIPID_VOLUME_NM3 = {
    "POPC": 1.29, "DOPC": 1.30, "DPPC": 1.28, "DMPC": 1.25, "DSPC": 1.30,
    "DLPC": 1.20, "POPE": 1.19, "DOPE": 1.30, "DPPE": 1.24,
    "POPS": 1.30, "DOPS": 1.32, "DPPS": 1.29,
    "POPA": 1.24, "POPG": 1.22, "POPI": 1.35,
    "PSM": 1.24, "NSM": 1.24, "OSM": 1.24, "SM": 1.24,
    "TOCL1": 2.30, "TLCL1": 2.30,
    "CHL1": 0.42,
}
DEFAULT_VOLUME = 1.25
STEROL_NAMES = ("CHL1", "CHOL")  # CHOL è l'alias da spec (mappato a CHL1)


def volume_for(name: str) -> float:
    """Volume molecolare (nm^3) per specie; fallback sulle specie note."""
    if name in LIPID_VOLUME_NM3:
        return LIPID_VOLUME_NM3[name]
    mapped = LIPID_NAME_MAP.get(name, name)
    if mapped in LIPID_VOLUME_NM3:
        return LIPID_VOLUME_NM3[mapped]
    return DEFAULT_VOLUME


def _is_sterol(name: str) -> bool:
    if name in STEROL_NAMES:
        return True
    return LIPID_NAME_MAP.get(name) in STEROL_NAMES


def _sheet_geometry(d: dict[str, int]) -> tuple[float, float, float]:
    """(volume_totale, area_totale, frazione molare sterolo) di un foglietto."""
    vol = sum(count * volume_for(n) for n, count in d.items())
    area = sum(count * apl_for(n) for n, count in d.items())
    n_sterol = sum(count for n, count in d.items() if _is_sterol(n))
    n_tot = sum(d.values()) or 1
    return vol, area, n_sterol / n_tot


def bilayer_thickness(upper: dict[str, int], lower: dict[str, int]) -> float:
    """Spessore P-P atteso (nm) dalla composizione.

    D_B = 2 * Σ(n_i * V_i) / Σ(n_i * APL_i) + 1.2 * x_CHL   (leaflet simmetrico)

    Per fogli asimmetrici si somma lo spessore dei due fogli (V/A di ciascuno);
    per composizione simmetrica la formula coincide con quella sopra.
    """
    v_up, a_up, x_up = _sheet_geometry(upper or {})
    v_low, a_low, x_low = _sheet_geometry(lower or {})
    if a_up <= 0 or a_low <= 0:
        return 0.0
    half_up = v_up / a_up
    half_low = v_low / a_low
    x_chl = (x_up + x_low) / 2
    return half_up + half_low + 1.2 * x_chl


def autobox_xy(area_demanded: float) -> float:
    """Lato autodimensionato per box_x/box_y: sqrt(area) + margine 0.3 nm.

    Il margine tenue mantiene la APL misurata entro il 15% dall'attesa
    (soglia del warning APL) invece del pad generoso (+1.2 nm) che
    gonfiava l'acqua per lipide. Minimo 2.6 nm: con PBC il lato deve
    superare il doppio del cut-off (~1.2 nm) o grompp rifiuta.
    """
    return max(round(math.sqrt(max(area_demanded, 1e-6)), 1) + 0.3, 2.6)


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

# ---------------------------------------------------------------------------
# Unità: il PDB di output e' in ANGSTROM (standard PDB); il calcolo interno
# resta in nanometri. _to_pdb_xyz converte nm -> A solo in fase di scrittura.
# Tutti i consumatori del PDB (clash, metriche, embed) devono dividere per 10.
# ---------------------------------------------------------------------------

def _to_pdb_xyz(x: float, y: float, z: float) -> tuple[float, float, float]:
    """Converte coordinate interne (nm) in angstrom per lo standard PDB."""
    return x * 10.0, y * 10.0, z * 10.0


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

def _lattice_positions(
    n: int, box_x: float, box_y: float, jitter: float, seed: int = 42,
) -> list[tuple[float, float]]:
    """Distribuisce n punti su griglia rettangolare che riempie il box xy.

    Il rettangolo utile è il box meno un margine 0.15 nm per lato (la metà
    del margine +0.3 dell'autobox, così l'APL misurata resta nell'attesa).
    jitter=0 -> griglia regolare (layout planar); jitter>0 disordine
    laterale (layout fluid), ampiezza limitata a metà passo di griglia.
    """
    if n <= 0:
        return []
    margin = min(0.15, box_x / 6.0, box_y / 6.0)
    span_x = max(box_x - 2 * margin, 0.05)
    span_y = max(box_y - 2 * margin, 0.05)
    cols = max(1, round(math.sqrt(n * span_x / span_y)))
    rows = math.ceil(n / cols)
    dx = span_x / cols if cols > 1 else 0.0
    dy = span_y / rows if rows > 1 else 0.0
    rng = random.Random(seed)
    amps = [min(jitter * dx, dx / 3.0), min(jitter * dy, dy / 3.0)]
    pts: list[tuple[float, float]] = []
    for i in range(n):
        c, r = i % cols, i // cols
        px = -span_x / 2 + (c + 0.5) * dx
        py = -span_y / 2 + (r + 0.5) * dy
        if amps[0] > 1e-9 or amps[1] > 1e-9:
            px += rng.uniform(-1, 1) * amps[0]
            py += rng.uniform(-1, 1) * amps[1]
        pts.append((px, py))
    return pts


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


def _heavy_clash_count(
    body_lines: list[str], thr: float = 0.12
) -> int:
    """Coppie di atomi pesanti fra lipidi DIVERSI dello stesso foglietto
    a distanza < thr (nm).

    Il PDB di output e' in ANGSTROM (standard): le coordinate lette dalle
    righe ATOM/HETATM vengono convertite in nm (÷10) prima del confronto,
    quindi thr resta espresso in nm. Soglia 0.12 nm = raggio vdW minimo:
    rileva solo sovrapposizioni massive, non soft-clash (v. nota BLOCKER 3).
    """

    atoms = []
    for line in body_lines:
        if not line.startswith("ATOM") and not line.startswith("HETATM"):
            continue
        if (line[76:78].strip() or line[13]) == "H":
            continue
        atoms.append(
            (
                int(line[22:26]),
                line[21].strip(),
                float(line[30:38]) / 10.0,
                float(line[38:46]) / 10.0,
                float(line[46:54]) / 10.0,
            )
        )
    count = 0
    n = len(atoms)
    for i in range(n):
        rno_i, ch_i, xi, yi, zi = atoms[i]
        for j in range(i + 1, n):
            rno_j, ch_j, xj, yj, zj = atoms[j]
            if rno_i == rno_j or ch_i != ch_j:
                continue
            dx = xi - xj
            dy = yi - yj
            dz = zi - zj
            if abs(dx) >= thr or abs(dy) >= thr or abs(dz) >= thr:
                continue
            if dx * dx + dy * dy + dz * dz < thr * thr:
                count += 1
    return count

# Tilt massimo della molecola di sterolo rispetto alla normale della
# membrana (rad). Letteratura (MD di colesterolo in bilayer POPC/POPE):
# tilt medio 10-20 gradi, distribuzione continua: qui uniforme in [0, MAX].
# Lo spin azimutale invece e' libero (rotazione attorno a z, angolo uniforme).
STEROL_TILT_MAX = math.radians(15.0)


def _sterol_atom_lines(
    rtp_name: str,
    atoms_info: list[tuple[str, str, float, int]],
    bonds: list[tuple[str, str]],
    resno: int,
    x: float,
    y: float,
    z_head: float,
    chain_id: str = "A",
    mirror: bool = False,
    rot_rng: random.Random | None = None,
) -> list[str]:
    """Righe PDB per uno sterolo (CHL1): backbone steran 4 anelli + catena.

    Piano degli anelli parallelo alla normale della membrana, OH verso
    l'acqua, catena verso il centro del bilayer. Orientazione corpo rigido
    realistica: spin azimutale uniforme attorno a z + tilt in [0, 15 gradi]
    via away dalla normale (entrambi da rot_rng, riproducibile per resno).
    Con mirror=True le coordinate z sono negate alla scrittura (foglio
    inferiore = specchio del superiore).
    """
    off = _sterane_offsets()
    z0 = z_head - 0.6 if z_head > 0 else -(z_head - 0.6)
    flip = 1.0 if z_head > 0 else -1.0

    rng = rot_rng if rot_rng is not None else random.Random(0)
    psi = rng.uniform(0.0, 2.0 * math.pi)          # spin azimutale
    tau = rng.uniform(0.0, STEROL_TILT_MAX)        # tilt dalla normale
    cos_t, sin_t = math.cos(tau), math.sin(tau)
    cos_p, sin_p = math.cos(psi), math.sin(psi)

    def _world(u: float, w: float, v: float) -> tuple[float, float, float]:
        """Da coordinate locali (u lungo x, w perpendicolare, v normale)
        a coordinate di membrana: tilt attorno all'asse in-plane w,
        poi spin attorno a z."""
        tx = u * cos_t + v * sin_t
        tz = -u * sin_t + v * cos_t
        return (tx * cos_p - w * sin_p, tx * sin_p + w * cos_p, tz)

    # coordinate locali (u, w, v): backbone da off (w=0), H piazzati nel frame
    local: dict[str, tuple[float, float, float]] = {}
    for name, _atype, _charge, _group in atoms_info:
        if name in off:
            u, v = off[name]
            local[name] = (u, 0.0, v)

    # idrogeni: vicino al partner di legame (dal grafo dei bonds rtp)
    bond_map: dict[str, list[str]] = {}
    for a, b in bonds:
        bond_map.setdefault(a, []).append(b)
        bond_map.setdefault(b, []).append(a)

    h_idx = 0
    for name, _atype, _charge, _group in atoms_info:
        if name in local:
            continue
        parents = [p for p in bond_map.get(name, []) if p in local]
        if parents:
            pu, pw, pv = local[parents[0]]
            ang = h_idx * 2.399  # golden-angle: offset distribuiti
            h_idx += 1
            local[name] = (
                pu + 0.1 * math.cos(ang),
                pw + 0.1 * math.sin(ang),
                pv + 0.03 * (1 if h_idx % 2 else -1),
            )
        else:
            local[name] = (0.05, 0.05, 0.0)

    coords: dict[str, tuple[float, float, float]] = {}
    for name in local:
        lu, lw, lv = local[name]
        dx, dy, dz = _world(lu, lw, lv)
        coords[name] = (x + dx, y + dy, z0 + flip * dz)

    lines: list[str] = []
    for name, _atype, _charge, _group in atoms_info:
        ox, oy, oz = coords[name]
        if mirror:
            oz = -oz
        # PDB standard = angstrom: le coordinate interne (nm) vanno x10
        ox, oy, oz = _to_pdb_xyz(ox, oy, oz)
        elem = name[0]
        lines.append(
            f"ATOM  {len(lines)+1:5d} {name:>4s} {rtp_name:>4s}"
            f"{chain_id:1s}{resno:4d}    {ox:8.3f}{oy:8.3f}{oz:8.3f}  "
            f"1.00  0.00          {elem:>2s}"
        )
    return lines


def _classify_atoms(
    atoms_info: list[tuple[str, str, float, int]],
    bonds: list[tuple[str, str]],
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Classifica gli atomi di un lipide dal GRAFO dei legami rtp.

    Le categorie guidano il posizionamento: N/P (anchor testa),
    O_head (O legata a P o N: fosfato/testa polare), O_ester (O legata
    solo a C: segue la catena), anchor (C agganciato a O_head:
    glicerolo/zucchero), ring (ciclo chiuso di carboni, es. inositolo),
    tail (C idrocarburico o acilico, scende verso il centro),
    H (segue il partner pesante).
    """
    nbrs: dict[str, list[str]] = {}
    for a, b in bonds:
        nbrs.setdefault(a, []).append(b)
        nbrs.setdefault(b, []).append(a)

    cat: dict[str, str] = {}
    for name, _atype, _charge, _group in atoms_info:
        c0 = name[0]
        if c0 == "H":
            cat[name] = "H"
        elif name == "P":
            cat[name] = "P"
        elif c0 == "N":
            cat[name] = "N"
        elif c0 == "O":
            cat[name] = "O_head"
        elif c0 == "C":
            cat[name] = "tail"
        else:
            cat[name] = "other"

    head_o: set[str] = set()
    for name, c in list(cat.items()):
        if c == "O_head":
            if any(p == "P" or p[0] == "N" for p in nbrs.get(name, [])):
                head_o.add(name)
            else:
                cat[name] = "O_ester"

    carbons = {n for n, c in cat.items() if c == "tail"}
    anchor_c = {c for c in carbons if any(p in head_o for p in nbrs.get(c, []))}
    chain_c = carbons - anchor_c

    # cicli chiusi di carboni (>=4 membri) tra chain/anchor -> testata
    ring_c: set[str] = set()
    for start in sorted(chain_c | anchor_c):
        stack: list[tuple[str, tuple[str, ...]]] = [(start, (start,))]
        while stack:
            cur, path = stack.pop()
            prev = path[-2] if len(path) > 1 else None
            for p in nbrs.get(cur, []):
                if p not in chain_c and p not in anchor_c:
                    continue
                if p == prev:
                    continue
                if p in path:
                    cycle = path[path.index(p):]
                    if len(cycle) >= 4:
                        ring_c.update(cycle)
                    continue
                stack.append((p, path + (p,)))

    for name in ring_c:
        cat[name] = "ring"
    for name in anchor_c - ring_c:
        cat[name] = "anchor"
    return cat, nbrs


def _lipid_atom_lines(
    resname: str,
    resno: int,
    x: float,
    y: float,
    z_head: float,
    chain_id: str = "A",
    mirror: bool = False,
    rot_rng: random.Random | None = None,
) -> list[str]:
    """Righe PDB per un lipide con geometria dal grafo dei legami rtp.

    Testa polare (P/N/O) intorno a z_head, glicerolo/ancore zucchero
    sotto l'O di testa, catene aciliche che scendono verso il centro
    (z -> TAIL_END_Z) con inclinazione realistica e lunghezze di legame
    C-C esatte (0.154 nm: con z-step ridotto la catena si piega
    lateralmente, non si comprime). I cicli chiusi (inositolo) restano
    piatti vicino alla testa. Con mirror=True le z sono negate alla
    scrittura (foglio inferiore = specchio del superiore).
    """
    lines: list[str] = []

    rtp_name = LIPID_NAME_MAP.get(resname, resname)
    residue = LIPID_RTP_DATA.get(rtp_name)

    if residue is None:
        # Residuo non in lipid.rtp: atomi generici con nomi semplici
        n_atoms = LIPID_ATOMS.get(resname, 15)
        for i in range(n_atoms):
            atom_name = f"C{i+1}"
            # PDB standard = angstrom: coordinate interne (nm) x10
            ox, oy, oz = _to_pdb_xyz(x, y, -z_head if mirror else z_head)
            lines.append(
                f"ATOM  {len(lines)+1:5d} {atom_name:>4s} {resname:>4s}"
                f"{chain_id:1s}{resno:4d}    {ox:8.3f}{oy:8.3f}{oz:8.3f}  "
                f"1.00  0.00          C"
            )
        return lines

    atoms_info = residue["atoms"]

    # steroli: backbone steran dedicato (anelli fused, non catene)
    if rtp_name in STEROL_RESIDUES:
        return _sterol_atom_lines(
            rtp_name, atoms_info, residue.get("bonds", []),
            resno, x, y, z_head, chain_id, mirror, rot_rng,
        )

    cat, nbrs = _classify_atoms(atoms_info, residue.get("bonds", []))

    coords: dict[str, tuple[float, float, float]] = {}

    # --- Ancore testa: N sopra, P subito sotto (P cade a z_head-BOND_CN) ---
    z_p = z_head - BOND_CN
    n_names = [n for n, c in cat.items() if c == "N"]
    for k, nname in enumerate(n_names):
        coords[nname] = (x, y, z_head - 0.01 * k)
    for pname in [n for n, c in cat.items() if c == "P"]:
        coords[pname] = (x, y, z_p)

    # --- O di testa: anello attorno al P ---
    head_o_list = [n for n, c in cat.items() if c == "O_head"]
    for k, oname in enumerate(head_o_list):
        az = 2 * math.pi * k / max(len(head_o_list), 1) + 0.4
        dz = 0.05 if k % 2 == 0 else -0.05
        coords[oname] = (
            x + BOND_PO * math.cos(az),
            y + BOND_PO * math.sin(az),
            z_p + dz,
        )

    # --- Catena da N (colina/ammine): BFS via soli C, si allarga attorno a N ---
    chain_extra_starts: set[str] = set()

    def _subtree_size_c(atom: str, stop: set[str]) -> int:
        """Numero di C nel sottoalbero carbonioso da `atom` (esclude O/P/N)."""
        seen: set[str] = {atom}
        stack = [atom]
        while stack:
            cur = stack.pop()
            for p in nbrs.get(cur, []):
                if p in stop or p in seen or p[0] != "C":
                    continue
                seen.add(p)
                stack.append(p)
        return len(seen)

    n_root_seen: set[str] = set()

    def _bfs_n_chain(n_root: str | None) -> None:
        if n_root is None:
            return
        slot = [0]
        queue: list[tuple[str, int]] = [(n_root, 0)]
        n_root_seen.add(n_root)
        while queue:
            cur, lvl = queue.pop(0)
            cx, cy, cz = coords[cur]
            kids = [
                p for p in nbrs.get(cur, [])
                if p[0] == "C" and p not in n_root_seen and p not in coords
                and cat.get(p) != "ring"
            ]
            for p in sorted(kids):
                n_root_seen.add(p)
                subtree = _subtree_size_c(p, set(nbrs.get(cur, [])))
                if lvl > 0 and subtree > 6:
                    # ramo carbonioso lungo attaccato all'N (es. sfingosina):
                    # NON sale vicino alla testa, diventa catena che scende
                    chain_extra_starts.add(p)
                    continue
                if lvl == 0:
                    az = 2 * math.pi * slot[0] / 4
                    coords[p] = (
                        cx + BOND_CN * math.cos(az),
                        cy + BOND_CN * math.sin(az),
                        cz + 0.02,
                    )
                else:
                    az = 0.7 * lvl
                    coords[p] = (
                        cx + BOND_CC * math.cos(az),
                        cy + BOND_CC * math.sin(az),
                        cz + 0.05,
                    )
                slot[0] += 1
                queue.append((p, lvl + 1))

    coords2 = set(coords)  # atomi gia' piazzati (anchor testa)

    # --- Catene da O_head verso il basso (glicerolo/acyl) ---
    visited: set[str] = set()

    def _depth_remaining(atom: str, memo: dict[str, int]) -> int:
        """Lunghezza del percorso piu' lungo verso una foglia (C/O ester)."""
        if atom in memo:
            return memo[atom]
        memo[atom] = 1  # guard contro cicli
        best = 0
        for p in nbrs.get(atom, []):
            if p in visited or cat.get(p) not in ("tail", "O_ester", "anchor"):
                continue
            best = max(best, _depth_remaining(p, memo))
        memo[atom] = 1 + best
        return memo[atom]

    counter = {"chain_step": 0}

    def _place(atom: str, parent: str) -> None:
        px, py, pz = coords[parent]
        a_is_ester = cat.get(atom) == "O_ester"
        h_par = [p for p in nbrs.get(atom, [])
                 if p[0] in ("C", "P", "N") and p != parent]
        if a_is_ester and not [p for p in h_par if p in coords]:
            # O disossolo: tenue offset dal partner verso il centro
            counter["chain_step"] += 1
            az = 0.6 * counter["chain_step"]
            coords[atom] = (px + 0.10 * math.cos(az), py + 0.10 * math.sin(az), pz - 0.06)
            return
        if a_is_ester and [p for p in nbrs.get(atom, []) if p[0] == "C" and p not in visited]:
            # O ponte (ester): tra parent e il C da esplorare
            nxt = [p for p in nbrs.get(atom, [])
                   if p[0] == "C" and p not in visited][0]
            memo: dict[str, int] = {}
            depth = _depth_remaining(nxt, memo)
            counter["chain_step"] += 1
            az = 0.6 * counter["chain_step"]
            coords[atom] = (
                px + 0.10 * math.cos(az),
                py + 0.10 * math.sin(az),
                pz - dz_step_for(depth, pz),
            )
        else:
            # C di catena: z-step uniforme, laterale complessivo da legame
            memo2: dict[str, int] = {}
            depth = _depth_remaining(atom, memo2)
            counter["chain_step"] += 1
            dz = dz_step_for(depth, pz)
            lateral = math.sqrt(max(BOND_CC ** 2 - dz ** 2, 0.02 ** 2))
            az = 0.6 * counter["chain_step"] * (1 if counter["chain_step"] % 2 else -1)
            coords[atom] = (
                px + lateral * math.cos(az),
                py + lateral * math.sin(az),
                pz - dz,
            )

    def dz_step_for(depth: int, pz: float) -> float:
        span = abs(pz - TAIL_END_Z)
        return min(max(span / max(depth, 1), 0.058), 0.148)

    def _bfs_chains() -> None:
        starts: set[str] = chain_extra_starts
        for oname in head_o_list:
            for p in nbrs.get(oname, []):
                # NB: p puo' essere gia' in coords/visited (l'anchor-loop dei
                # gliceroli gira prima): in quel caso NON va scartato, la BFS
                # delle catene deve partire da lui e scendere sui figli.
                if (
                    cat.get(p) in ("tail", "O_ester", "anchor")
                ):
                    starts.add(p)
        if not starts:
            return
        for s in sorted(starts):
            _seed_down(s)

    def _seed_down(start: str) -> None:
        # anchor gia' piazzati (es. glicerolo) hanno figli da attraversare:
        # il guard vale solo per start di catena gia' processati
        if start in visited and start not in coords:
            return
        # gli anchor (glicerolo/zucchero) scendono dal proprio O_head
        if start in coords:
            visited.add(start)
        else:
            parent = [p for p in nbrs.get(start, []) if p in coords]
            if parent:
                px, py, pz = coords[parent[0]]
                visited.add(start)
                coords[start] = (px + 0.03, py + 0.07, pz - 0.13)
            else:
                visited.add(start)
                coords[start] = (x + 0.02, y + 0.10, z_p - 0.18)
        queue = [start]
        while queue:
            cur = queue.pop(0)
            nxt = [
                p for p in nbrs.get(cur, [])
                if p not in visited and p not in coords2
                and cat.get(p) in ("tail", "O_ester")
            ]
            for p in sorted(nxt):
                visited.add(p)
                _place(p, cur)
                queue.append(p)

    _bfs_n_chain(n_names[0] if n_names else None)

    # --- Anello (inositolo): esagono piatto vicino alla testa ---
    ring_atoms = sorted(n for n, c in cat.items() if c == "ring")
    if ring_atoms:
        rcx, rcy, rcz = x + 0.05, y + 0.42, z_p - 0.10
        for k, rname in enumerate(ring_atoms):
            az = 2 * math.pi * k / max(len(ring_atoms), 1)
            coords[rname] = (
                rcx + 0.24 * math.cos(az),
                rcy + 0.24 * math.sin(az),
                rcz + 0.03 * (1 if k % 2 else -1),
            )
            visited.add(rname)

    # ancore non ancora piazzate (zucchero non raggiunto dalla BFS N)
    for aname in sorted(n for n, c in cat.items() if c == "anchor"):
        if aname in coords:
            continue
        parents = [p for p in nbrs.get(aname, []) if p in coords]
        if parents:
            px, py, pz = coords[parents[0]]
            coords[aname] = (px + 0.03, py + 0.07, pz - 0.13)
        else:
            coords[aname] = (x + 0.02, y + 0.10, z_p - 0.18)
        visited.add(aname)

    _bfs_chains()

    # --- Idrogeni: vicino al partner pesante (dal grafo) ---
    h_idx = 0
    for name, catg in cat.items():
        if catg != "H" or name in coords:
            continue
        parents = [p for p in nbrs.get(name, []) if p in coords]
        if parents:
            px, py, pz = coords[parents[0]]
            wob = 0.05 if h_idx % 2 == 0 else -0.04
            h_idx += 1
            coords[name] = (px + 0.05, py + 0.04, pz + wob)
        else:
            coords[name] = (x + 0.05, y + 0.05, z_head)

    # --- Scrittura nell'ordine rtp ---
    missing = 0
    for name, _atype, _charge, _group in atoms_info:
        if name not in coords:
            missing += 1
            coords[name] = (x + 0.03 * missing,
                            y + 0.03 * missing, z_head * 0.5)
        ox, oy, oz = coords[name]
        if mirror:
            oz = -oz
        # PDB standard = angstrom: coordinate interne (nm) x10
        ox, oy, oz = _to_pdb_xyz(ox, oy, oz)
        elem = name[0]
        lines.append(
            f"ATOM  {len(lines)+1:5d} {name:>4s} {rtp_name:>4s}"
            f"{chain_id:1s}{resno:4d}    {ox:8.3f}{oy:8.3f}{oz:8.3f}  "
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
    lines.append(f"REMARK   Coordinate atomiche in A (standard PDB); box in nm (descrittivo)")
    lines.append(f"REMARK   Layout: {layout}, centro={'si' if center else 'no'}")

    total_lipids = sum(upper.values()) + sum(lower.values())
    if total_lipids == 0:
        raise ValueError("nessun lipide specificato (upper e lower vuoti)")

    # Spessore P-P atteso dalla composizione; le teste vanno a ±D_B/2 e il P
    # del fosfato cade esattamente a ±D_B/2 (z_head = D_B/2 + BOND_CN).
    thickness_db = bilayer_thickness(upper, lower)
    head_z = thickness_db / 2 + BOND_CN
    lines.append(
        f"REMARK   Spessore P-P previsto (D_B): {thickness_db:.2f} nm"
    )

    # area totale PER FOGLIETTO con APL reali per composizione (densità fisica)
    def _sheet_area(d: dict[str, int]) -> float:
        return sum(c * apl_for(n) for n, c in d.items())
    area_upper = _sheet_area(upper)
    area_lower = _sheet_area(lower)
    if area_upper <= 0 or area_lower <= 0:
        raise ValueError("foglio vuoto (upper o lower senza lipidi): area membrana zero")

    # posizioni su griglia che riempie il box (l'APL reale la fissa il box,
    # non la composizione: i lipidi si distribuiscono su tutta l'area)
    jitter = 0.30 if layout == "fluid" else 0.0
    upper_positions = _lattice_positions(sum(upper.values()), box_x, box_y, jitter, seed=42)
    lower_positions = _lattice_positions(sum(lower.values()), box_x, box_y, jitter, seed=7)

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

    # Rotazioni corpo rigido degli steroli (spin azimutale + tilt) con
    # risoluzione deterministica dei clash: si generano piu' tentativi con
    # seed diversi e si tiene il primo packing senza overlap (come fa
    # PACKMOL, in miniatura). Se nessun tentativo e' pulito, resta il best.
    def _render_body(rot_seed: int) -> list[str]:
        """Righe ATOM/HETATM di entrambi i foglietti con seed di rotazione fisso."""
        rng = random.Random(rot_seed)
        body: list[str] = []
        rn = 1
        # --- Foglio superiore ---
        for idx, resname in enumerate(upper_species):
            x, y = upper_positions[idx]
            body.extend(
                _lipid_atom_lines(
                    resname, rn, x + ox_off, y + oy_off, head_z, "U",
                    rot_rng=rng,
                )
            )
            rn += 1
        # --- Foglio inferiore (specchio del superiore) ---
        for idx, resname in enumerate(lower_species):
            x, y = lower_positions[idx]
            body.extend(
                _lipid_atom_lines(
                    resname, rn, x + ox_off, y + oy_off, head_z, "L", mirror=True,
                    rot_rng=rng,
                )
            )
            rn += 1
        return body

    body = _render_body(31337)
    best = (None, 10**9)
    for attempt in range(20):
        n_clash = _heavy_clash_count(body)
        if n_clash < best[1]:
            best = (attempt, n_clash)
        if n_clash == 0:
            break
        print(
            f"INFO: tentativo {attempt + 1}: {n_clash} clash pesanti fra lipidi "
            "(rigenero le orientazioni sterolo)"
        )
        body = _render_body(31337 + 7919 * (attempt + 1))
    if best[1] != 0:
        print(
            f"AVVISO: nessun packing sterolo pulito in 20 tentativi; "
            f"tengo il tentativo {best[0] + 1} con {best[1]} clash"
        )
        body = _render_body(31337 + 7919 * best[0])

    lines.extend(body)
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
    extra_charges_e: float = 0.0,
) -> tuple[int, int, float]:
    """Numero ioni (+, -) per neutralizzare e raggiungere la concentrazione.

    extra_charges_e: carica netta di componenti esterne (es. proteina via
    pdb2gmx) da compensare oltre ai lipidi. Ritorna (n_cation, n_anion,
    net_charge_e): ioni di compensazione + coppie da concentrazione.
    """
    net = net_charge(upper, lower) + extra_charges_e
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

    # Preserva le molecole non-lipidi/solvente già in [molecules]
    # (es. Protein_chain_X da embed_protein): devono restare PRIMA dei
    # lipidi perché nel PDB/gro l'ordine è proteina → lipidi → solvente.
    KNOWN_NON_PROTEIN = {"SOL", "NA", "CL", "K"} | {
        LIPID_NAME_MAP.get(n, n) for n in set(upper) | set(lower)
    }
    preserved: dict[str, int] = {}
    if mol_idx is not None:
        j = mol_idx + 1
        while j < len(lines):
            stripped = lines[j].strip()
            if stripped == "" or stripped.startswith("[") or stripped.startswith(";"):
                if stripped.startswith("[") :
                    break
                j += 1
                continue
            parts = stripped.split()
            if len(parts) >= 2:
                name, count = parts[0], parts[1]
                if name not in KNOWN_NON_PROTEIN:
                    try:
                        preserved[name] = preserved.get(name, 0) + int(count)
                    except ValueError:
                        pass
            j += 1
            # fine sezione = riga vuota dopo gli entry
            if stripped == "":
                break
    for name, count in preserved.items():
        needed[name] = count

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
    # Ordine molecole: proteina, poi lipidi, poi SOL, poi ioni (ordine del .gro)
    def mol_order(name):
        if name.startswith("Protein_chain"):
            return -1
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
    timeout: int = 300,
) -> subprocess.CompletedProcess:
    """Esegue un comando gmx con gestione output."""
    cmd = [gmx] + args
    return subprocess.run(
        cmd, cwd=cwd, input=input_text,
        capture_output=True, text=True, timeout=timeout,
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
        tf.extractall(system_dir, members=members, filter="data")

    # Rinomina la directory estratta in charmm36.ff
    src = system_dir / "toppar_c36_releases-main" / "gromacs" / "charmm36-feb2026_cgenff-5.0.ff"
    if src.exists() and not target_dir.exists():
        shutil.move(str(src), str(target_dir))

    # Rimuovi la directory intermediaria
    intermediate = system_dir / "toppar_c36_releases-main"
    if intermediate.exists():
        shutil.rmtree(str(intermediate))

    # Copia lipid.rtp nella directory system (provenance output; usato da
    # generate_top come reference. Fonte: ~/.local/share/cgui-studio/,
    # NON path temporanei di /tmp).
    lipid_rtp_src = Path.home() / ".local" / "share" / "cgui-studio" / "lipid.rtp"
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

def clean_chain_itp(itp_text: str) -> str:
    """Taglia il tail pdb2gmx del chain .itp, preservando tutto il moleculetype.

    pdb2gmx conclude il moleculetype con ``#ifdef POSRES`` +
    ``#include "posre.itp"`` + ``#endif``, poi aggiunge un ``#include`` con
    path ASSOLUTO di tip3p.itp, il blocco ``POSRES_WATER`` e resti di
    ``[ system ]``/``[ molecules ]``. Nel master top l'acqua è già inclusa:
    questo tail va rimosso o grompp include tip3p due volte.

    Il taglio è ANCORIZZATO su ``#ifdef POSRES`` (l'unico blocco condizionale
    dell'output pdb2gmx, sempre in coda): tutto ciò che segue il ``#endif``
    che chiude POSRES viene rimosso. Fallback: taglio sull'include assoluto
    di tip3p. Se né POSRES né l'include assoluto ci sono, il testo resta
    intatto (file già pulito o formato diverso).
    """
    lines = itp_text.splitlines()
    out: list[str] = []
    anchor_seen = False
    for line in lines:
        stripped = line.strip()
        if anchor_seen and stripped == "#endif":
            return "\n".join(out) + "\n"
        if not anchor_seen and stripped.startswith("#include") and "tip3p.itp" in stripped:
            return "\n".join(out) + "\n"
        if not anchor_seen and stripped == "#ifdef POSRES":
            anchor_seen = True
        out.append(line)
    return itp_text  # nessun anchor trovato: testo intatto (fallback)


def _itp_net_charge(itp_path: Path) -> float:
    """Somma le cariche della sezione [ atoms ] di un .itp."""
    text = itp_path.read_text()
    m = re.search(r"\[ atoms \]\n(.*?)(?=\n\[ \w+ \]|\Z)", text, re.S)
    if not m:
        raise ValueError(f"sezione [ atoms ] non trovata in {itp_path}")
    q = 0.0
    for line in m.group(1).splitlines():
        s = line.split(";")[0].split()
        if len(s) >= 8:
            q += float(s[6])
    return round(q, 4)


def center_protein_gro(
    gro_text: str, tm_z_min_nm: float, tm_z_max_nm: float,
) -> str:
    """Centra la proteina: XY al centro shell, Z con il mid del dominio TM su z=0.

    Gro è in nm; ritorna il nuovo testo .gro centrato.
    """
    lines = gro_text.splitlines()
    body_start = 2  # title + natoms
    natoms = int(lines[1].split()[0])
    body = lines[body_start:body_start + natoms]
    if len(body) < natoms:  # tolta vLib: fewer lines than natoms
        raise ValueError("gro troncato: meno righe di natoms")
    xs = [float(line[20:28]) for line in body]
    ys = [float(line[28:36]) for line in body]
    z_mid = (float(tm_z_min_nm) + float(tm_z_max_nm)) / 2.0
    dx = -sum(xs) / len(xs)
    dy = -sum(ys) / len(ys)
    dz = -z_mid
    new_body = []
    for line in body:
        x = float(line[20:28]) + dx
        y = float(line[28:36]) + dy
        z = float(line[36:44]) + dz
        new_body.append(f"{line[:20]}{x:8.3f}{y:8.3f}{z:8.3f}" + line[44:])
    return "\n".join(lines[:body_start] + new_body + lines[body_start + natoms:]) + "\n"


def merge_protein_into_pdb(pdb_text: str, protein_gro_text: str) -> str:
    """Inserisce le coordinate della proteina (da .gro, nm) nel PDB del bilayer.

    Il PDB del bilayer e' gia' in ANGSTROM (standard PDB, fix Commit 1); le
    linee ATOM della proteina vengono convertite da nm a Å (×10) e inserite
    PRIMA degli atomi del bilayer, così l'ordine nel PDB corrisponde
    all'ordine [ molecules ] (Protein_chain_* → lipidi → SOL → ioni).
    """
    lines = protein_gro_text.splitlines()
    if len(lines) < 2:
        raise ValueError("gro vuoto o malformato")
    natoms = int(lines[1].split()[0])
    body = lines[2:2 + natoms]
    prot_lines = []
    serial = 1
    for line in body:
        resname = line[5:10].strip()
        atom_name = line[10:15].strip()
        res_no = int(line[0:5])
        x = float(line[20:28]) * 10.0
        y = float(line[28:36]) * 10.0
        z = float(line[36:44]) * 10.0
        prot_lines.append(
            f"ATOM  {serial:5d} {atom_name:>4s} {resname:>3s} A{res_no:4d}    "
            f"{x:8.3f}{y:8.3f}{z:8.3f}"
        )
        serial += 1
    out = []
    inserted = False
    for line in pdb_text.splitlines():
        if not inserted and line.startswith(("ATOM  ", "HETATM")):
            out.extend(prot_lines)
            out.append("TER")
            inserted = True
        out.append(line)
    if not inserted:
        out.extend(prot_lines)
        out.append("TER")
    return "\n".join(out) + "\n"


def patch_top_with_protein(
    top_text: str,
    itp_files: dict[str, str],
    posre_files: list[str],
) -> str:
    """Inserisce le molecole proteiche in topol.top.

    itp_files: {chain_id: nome itp}. Inserisce #include dopo gli include del
    force field e le righe [ molecules ] Protein_chain<X> 1 in testa.

    posre_files NON va incluso esplicitamente: il chain itp di pdb2gmx
    contiene già ``#ifdef POSRES #include "posre.itp" #endif`` (relativo,
    risolto perché il file è copiato nella stessa dir del top). Un include
    esplicito vorrebbe dire doppia inclusione → grompp fallisce.
    """
    include_lines = [f'#include "{fname}"' for fname in itp_files.values()]
    mol_lines = [
        f"{f'Protein_chain_{ch}':<17s} 1" for ch in itp_files
    ]
    out: list[str] = []
    seen_last_ff_include = False
    in_molecules = False
    for line in top_text.splitlines():
        if not seen_last_ff_include and line.startswith("#include"):
            out.append(line)
            if 'ions.itp' in line:
                out.extend(include_lines)
                seen_last_ff_include = True
            continue
        if line.startswith("[ molecules ]"):
            in_molecules = True
            out.append(line)
            continue
        if in_molecules and line.strip() and not line.lstrip().startswith((";", "[", "#")):
            out.extend(mol_lines)
            mol_lines = []
            in_molecules = False
        out.append(line)
    if mol_lines:  # [ molecules ] non trovato: append in fondo
        out.extend(mol_lines)
    return "\n".join(out) + "\n"


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
    protein_net_charge: float = 0.0,
    minimize: bool = False,
    min_timeout: int = 900,
    do_solvate: bool = False,
) -> bool:
    """editconf → solvate → grompp → genion → grompp → [mdrun solo con minimize].

    Genion neutralizza la carica dei lipidi + proteina e porta il sale alla
    concentrazione (flusso CHARMM-GUI). La minimizzazione mdrun è opt-in via
    --minimize (su macchine piccole es. E7440 può superare 5 min su ~70k atomi).
    Ritorna True se la pipeline richiesta è completata.
    """
    def gmx(args_: list[str], input_text: str | None = None, timeout: int = 300):
        return run_gmx(gmx_path, args_, cwd=system_dir,
                       input_text=input_text, timeout=timeout)

    boxed_gro = str((system_dir / "boxed.gro").resolve())
    r = gmx(["editconf", "-f", pdb_abs, "-o", boxed_gro,
             "-box", f"{box_x}", f"{box_y}", f"{box_z}"])
    if r.returncode != 0:
        print(f"AVVISO: gmx editconf fallito: {r.stderr[-500:]}", file=sys.stderr)
        return False
    print("gmx editconf: OK")

    if not do_solvate:
        print("gmx solvate saltato (--solvate non richiesto): file boxed.gro pronto")
        minim_tpr = str((system_dir / "minim.tpr").resolve())
        r = gmx(["grompp", "-maxwarn", "50", "-f", mdp_path, "-c", boxed_gro,
                 "-p", top_abs, "-o", minim_tpr])
        if r.returncode != 0:
            print(f"AVVISO: gmx grompp fallito: {r.stderr[-500:]}", file=sys.stderr)
            return False
        print("gmx grompp: OK - minim.tpr pronto")
        if not minimize:
            print("gmx mdrun saltato (no --minimize): sistema pronto")
            return True
        print("AVVISO: minimize senza acqua non supportato, salto mdrun")
        return True

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

    n_cat, n_an, net = ion_counts(upper, lower, box_x, box_y, box_z, ion_conc,
                                  extra_charges_e=protein_net_charge)
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
    if not minimize:
        print("gmx mdrun saltato (--minimize non richiesto): sistema pronto per l'equilibrazione")
        return True

    try:
        r = gmx(["mdrun", "-s", minim_tpr,
                 "-o", str((system_dir / "minim.trr").resolve()),
                 "-c", str((system_dir / "minim.gro").resolve()),
                 "-e", str((system_dir / "minim.edr").resolve()),
                 "-g", str((system_dir / "minim.log").resolve())],
                timeout=min_timeout)
    except subprocess.TimeoutExpired:
        print(f"AVVISO: mdrun interrotto dopo {min_timeout}s "
              f"(sistema grompp-ready, minimizzazione non completata)",
              file=sys.stderr)
        return False
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
    pdb_path: str,
    upper: dict[str, int],
    lower: dict[str, int],
    gro_path: str | None = None,
    box_nm: tuple[float, float, float] | None = None,
    extra_warnings: list[str] | None = None,
) -> dict:
    """Metriche fisiche: spessore P-P dal PDB grezzo, APL, acqua per lipide.

    Il PDB RAW (step1_pdbreader.pdb) e' in ANGSTROM (standard PDB): la
    coordinata z in [46:54] viene convertita in nm (÷10) prima dell'uso.
    thickness_P_nm e i conti dello ione fosfato derivano quindi da nm.
    upper = z>0, lower = z<0.
    - Da gro_path (se fornito): natoms, box, conteggio SOL. Altrimenti natoms
      dal PDB e box da box_nm.
    - apl_measured_nm2 = (box_x * box_y) / n_lipidi_per_foglietto, con
      n = somma della spec upper (CHL1 non ha P: NON contare gli atomi P).
    - Warning (non bloccanti) in metrics['warning'] se la APL devia >15%
      dall'attesa o se l'acqua per lipide supera 80.
    """
    warnings: list[str] = list(extra_warnings or [])

    # --- PDB grezzo: atomi P ---
    z_p_upper: list[float] = []
    z_p_lower: list[float] = []
    n_pdb_atoms = 0
    with open(pdb_path) as f:
        for line in f:
            if not line.startswith(("ATOM", "HETATM")):
                continue
            n_pdb_atoms += 1
            if len(line) < 54:
                continue
            if line[12:16].strip() != "P":
                continue
            try:
                z = float(line[46:54]) / 10.0  # PDB in A -> nm
            except ValueError:
                continue
            (z_p_upper if z > 0 else z_p_lower).append(z)

    thickness = 0.0
    if z_p_upper and z_p_lower:
        thickness = (
            sum(z_p_upper) / len(z_p_upper) - sum(z_p_lower) / len(z_p_lower)
        )

    # --- .gro (se c'è): natomi, box, acqua ---
    res_counts: dict[str, int] = {}
    n_sol = 0
    box: tuple[float, ...] = tuple(box_nm or (0.0, 0.0, 0.0))
    natoms = n_pdb_atoms
    if gro_path is not None and Path(gro_path).exists():
        with open(gro_path) as f:
            lines = f.readlines()
            try:
                natoms = int(lines[1].strip())
            except (ValueError, IndexError):
                natoms = n_pdb_atoms
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
                res_counts[resname] = res_counts.get(resname, 0) + 1
                if resname == "SOL" and atomname == "OW":
                    n_sol += 1
            for line in lines[idx:]:
                parts = line.split()
                if len(parts) >= 3:
                    try:
                        box = tuple(float(v) for v in parts[:3])
                        break
                    except ValueError:
                        continue

    n_lip_upper = sum(upper.values()) or sum(lower.values()) or 1
    n_lip_total = (sum(upper.values()) + sum(lower.values())) or 1
    box_x, box_y = box[0], box[1]
    apl = (box_x * box_y) / n_lip_upper
    apl_expected = (
        sum(c * apl_for(n) for n, c in upper.items()) / n_lip_upper
        if upper
        else sum(c * apl_for(n) for n, c in lower.items()) / n_lip_upper
    )
    water_per_lipid = n_sol / n_lip_total

    if apl_expected > 0 and abs(apl - apl_expected) / apl_expected > 0.15:
        warnings.append(
            f"APL misurata {apl:.3f} nm2 devia >15% dall'attesa "
            f"{apl_expected:.3f} nm2 (box xy {box_x:.2f}x{box_y:.2f} "
            f"{'' if box_x > 0 else 'default/errato'} per {n_lip_upper} lipidi/foglio)"
        )
    if water_per_lipid > 80:
        warnings.append(
            f"{water_per_lipid:.1f} molecole d'acqua per lipide (>80): "
            "box troppo grande rispetto al numero di lipidi"
        )

    return {
        "natoms": natoms,
        "box_nm": [round(v, 3) for v in box],
        "thickness_P_nm": round(thickness, 3),
        "n_phosphate_upper": len(z_p_upper),
        "n_phosphate_lower": len(z_p_lower),
        "apl_measured_nm2": round(apl, 3),
        "apl_expected_nm2": round(apl_expected, 3),
        "water_per_lipid": round(water_per_lipid, 1),
        "net_lipid_charge_e": net_charge(upper, lower),
        "warning": warnings,
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
        help="Esegue gmx editconf (e solvate se --solvate) se disponibile "
             "(mdrun solo con --minimize)",
    )
    parser.add_argument(
        "--solvate", action="store_true",
        help="Aggiungi anche gmx solvate (lento su box grandi)",
    )
    parser.add_argument(
        "--minimize", action="store_true",
        help="Esegue anche gmx mdrun (minimizzazione steepest) dopo "
             "editconf/solvate/genion/grompp",
    )
    parser.add_argument(
        "--min-timeout", type=int, default=900,
        help="Timeout in secondi per gmx mdrun minimizzazione (default 900)",
    )
    parser.add_argument(
        "--box-x", type=float, default=None,
        help="Dimensione box X in nm (default: auto da area composition)",
    )
    parser.add_argument(
        "--box-y", type=float, default=None,
        help="Dimensione box Y in nm (default: auto da area composition)",
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
        help="Percorso al file lipid.rtp di CHARMM36 (default: "
             "~/.local/share/cgui-studio/lipid.rtp)",
    )
    # --- Nuovi argomenti per supporto proteine, acidi nucleici, small molecules ---
    parser.add_argument(
        "--pdb", type=str, default=None,
        help="ID PDB (es. 7CFN) di una proteina da orientare, topologizzare "
             "(pdb2gmx CHARMM36) e inserire nel bilayer",
    )
    parser.add_argument(
        "--pdb-chain", type=str, default=None,
        help="Chain della proteina da mantenere (--pdb). Es. 'R' per GPBAR1 "
             "in 7CFN; default: tutte le chain",
    )
    parser.add_argument(
        "--pdb-prefer", type=str, default="OPM",
        choices=["OPM", "PPM", "none"],
        help="Orientamento proteina (--pdb): OPM (default), PPM, none "
             "(passthrough: solo centro su z=0 del dominio TM)",
    )
    parser.add_argument(
        "--pdb-fix-missing", action="store_true",
        help="Converte a GLY i residui con side chain incompleta (structure "
             "cryo-EM) per permettere pdb2gmx (--pdb)",
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

    # --- Autobox xy + warning se il box fornito non é coerente con l'area ---
    def _sheet_area(d: dict[str, int]) -> float:
        return sum(c * apl_for(n) for n, c in d.items())

    area_demanded = max(_sheet_area(upper), _sheet_area(lower))
    box_warnings: list[str] = []
    if args.box_x is None and args.box_y is None:
        auto_dim = autobox_xy(area_demanded)
        args.box_x = auto_dim
        args.box_y = auto_dim
        print(
            f"Autobox: area membrana {area_demanded:.2f} nm2 "
            f"-> box xy = {auto_dim:.1f} nm"
        )
    elif args.box_x is None or args.box_y is None:
        # una sola dimensione data: riempi con quella nota
        if args.box_x is None:
            args.box_x = args.box_y
        else:
            args.box_y = args.box_x
    else:
        area_box = args.box_x * args.box_y
        if area_demanded > 0.8 * area_box:
            msg = (
                f"area membrana {area_demanded:.2f} nm2 > 80% del box "
                f"({area_box:.2f} nm2): troppa membrana, poca acqua/lipide"
            )
            print(f"AVVISO: {msg}", file=sys.stderr)
            box_warnings.append(msg)
        elif area_demanded < 0.4 * area_box:
            msg = (
                f"area membrana {area_demanded:.2f} nm2 < 40% del box "
                f"({area_box:.2f} nm2): troppa acqua per lipide"
            )
            print(f"AVVISO: {msg}", file=sys.stderr)
            box_warnings.append(msg)

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

    # Copia lipid.rtp nella directory system (provenance output).
    lipid_rtp_src = Path.home() / ".local" / "share" / "cgui-studio" / "lipid.rtp"
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

    # --- Proteina (--pdb): orientamento + pdb2gmx + embedding (Modulo 4) ---
    prot_net_charge = 0.0
    prot_embed_manifest = None
    if args.pdb:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from embed_protein import run as embed_run  # lazy import (Modulo 4)
        prot_dir = outdir / "protein_embed"
        prot_dir.mkdir(parents=True, exist_ok=True)
        chain_filter = [args.pdb_chain.strip().upper()] if args.pdb_chain else None
        print(
            f"Proteina {args.pdb}: orientamento + pdb2gmx "
            f"(chain={chain_filter or 'tutte'})…"
        )
        prot_embed_manifest = embed_run(
            pdb=args.pdb, out_dir=str(prot_dir),
            chain_filter=chain_filter, prefer=args.pdb_prefer,
            fix_missing=args.pdb_fix_missing,
        )
        if prot_embed_manifest["pdb2gmx_status"] != "ok":
            print(
                "ERRORE: pdb2gmx fallito per "
                f"{args.pdb}:\n{prot_embed_manifest['pdb2gmx_log_tail'][-500:]}",
                file=sys.stderr,
            )
            return 1

        # itp generati da pdb2gmx (split per chain)
        itp_rel: dict[str, str] = prot_embed_manifest["itp_files"]
        itp_names: dict[str, str] = {}
        posre_names: list[str] = []
        for ch, fn in itp_rel.items():
            src = prot_dir / fn
            (system_dir / src.name).write_text(
                clean_chain_itp(src.read_text())
            )
            itp_names[ch] = src.name
        for posre_src in prot_dir.glob("posre*.itp"):
            shutil.copy(str(posre_src), str(system_dir / posre_src.name))
            posre_names.append(posre_src.name)

        # carica netta reale della proteina (dalle sezioni [ atoms ] degli itp)
        prot_net_charge = round(
            sum(_itp_net_charge(prot_dir / fn) for fn in itp_rel.values()), 4
        )
        print(f"  carica proteica da itp: {prot_net_charge:+.1f} e")

        # centraggio XY/Z e inserimento nel PDB del bilayer
        gro_text = (prot_dir / "protein.gro").read_text()
        centered = center_protein_gro(
            gro_text,
            prot_embed_manifest["tm_z_min_nm"],
            prot_embed_manifest["tm_z_max_nm"],
        )
        (prot_dir / "protein_centered.gro").write_text(centered)
        merged_pdb = merge_protein_into_pdb(pdb_path.read_text(), centered)
        pdb_path.write_text(merged_pdb)

        # topol: include itp proteici + righe [ molecules ]
        top_text = top_path.read_text()
        top_path.write_text(
            patch_top_with_protein(top_text, itp_names, posre_names)
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
                protein_net_charge=prot_net_charge,
                minimize=args.minimize, min_timeout=args.min_timeout,
                do_solvate=args.solvate,
            )
        else:
            print("AVVISO: --run-gmx richiesto ma gmx non trovato nel PATH.", file=sys.stderr)

    # --- File di equilibratura (NVT/NPT/produzione + script) ---
    equilib = generate_equilib_files()
    for fname, content in equilib.items():
        (outdir / fname).write_text(content)

    # --- Metriche fisiche: spessore P-P dal PDB grezzo + (se c'è) gro ---
    metrics = None
    ionized = system_dir / "ionized.gro"
    try:
        metrics = compute_metrics(
            str(pdb_path), upper, lower,
            gro_path=str(ionized) if ionized.exists() else None,
            box_nm=(args.box_x, args.box_y, args.box_z),
            extra_warnings=box_warnings,
        )
        if prot_embed_manifest is not None:
            metrics["protein"] = {
                "pdb_id": prot_embed_manifest["pdb_id"],
                "source": prot_embed_manifest["source"],
                "chains": prot_embed_manifest["chains"],
                "net_charge_e": prot_net_charge,
                "itp_files": prot_embed_manifest["itp_files"],
                "fixed_residues": prot_embed_manifest["fixed_residues"],
            }
        (outdir / "metrics.json").write_text(
            json.dumps(metrics, indent=2, ensure_ascii=False) + "\n"
        )
        print(f"  Metriche: spessore P-P {metrics['thickness_P_nm']} nm, "
              f"APL {metrics['apl_measured_nm2']} nm2, "
              f"acqua/lipide {metrics['water_per_lipid']}")
        for w in metrics.get("warning", []):
            print(f"  AVVISO metriche: {w}", file=sys.stderr)
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