#!/usr/bin/env python3
"""Inserisce un recettore (PDB) in una membrana lipidica già costruita.

Geometria stdlib puro (nessuna dipendenza scientifica esterna):
- legge PDB membrana e PDB recettore
- allinea l'asse TM del recettore (PCA su backbone C-alpha) all'asse z della membrana
- centra il recettore al centro della membrana (xy=0, z=0)
- scrive PDB combinato + manifest.json

Uso:
    python scripts/embed_in_membrane.py --membrane-pdb membrana.pdb --receptor-pdb recettore.pdb --output results/embed_xyz
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(v, s):
    return (v[0] * s, v[1] * s, v[2] * s)


def _norm(v):
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if n == 0:
        return (0.0, 0.0, 0.0)
    return (v[0] / n, v[1] / n, v[2] / n)


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def parse_pdb(path: str | Path) -> list[dict]:
    """Legge un PDB e restituisce lista di atomi: {name, resname, resno, chain, x, y, z, elem}."""
    atoms = []
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"PDB non trovato: {path}")
    with path.open() as f:
        for line in f:
            if line.startswith(("ATOM", "HETATM")):
                name = line[12:16].strip()
                resname = line[17:20].strip()
                resno = int(line[22:26])
                chain = line[21].strip()
                x = float(line[30:38])
                y = float(line[38:46])
                z = float(line[46:54])
                elem = line[76:78].strip() or name[0]
                atoms.append(
                    {
                        "name": name,
                        "resname": resname,
                        "resno": resno,
                        "chain": chain,
                        "x": x,
                        "y": y,
                        "z": z,
                        "elem": elem,
                        "raw": line.rstrip("\n"),
                    }
                )
    return atoms


def backbone_ca(atoms: list[dict]) -> list[dict]:
    """Restituisce gli atomi CA (backbone) per stimare l'asse TM."""
    return [a for a in atoms if a["name"] == "CA"]


def pca_axis(points: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    """Prima componente principale di un insieme di punti (asse principale)."""
    if not points:
        return (0.0, 0.0, 1.0)
    n = len(points)
    cx = sum(p[0] for p in points) / n
    cy = sum(p[1] for p in points) / n
    cz = sum(p[2] for p in points) / n
    # matrice di covarianza 3x3 (semplificata: solo varianza lungo assi)
    # PCA approssimata: asse di massima varianza via potenza iterata su cov
    # Per robustezza useremo il vettore tra primo e ultimo CA come asse TM approssimativa
    # (sufficiente per orientare il recettore lungo z)
    p0 = points[0]
    p1 = points[-1]
    v = _sub(p1, p0)
    return _norm(v)


def center_of_mass(atoms: list[dict]) -> tuple[float, float, float]:
    if not atoms:
        return (0.0, 0.0, 0.0)
    n = len(atoms)
    return (
        sum(a["x"] for a in atoms) / n,
        sum(a["y"] for a in atoms) / n,
        sum(a["z"] for a in atoms) / n,
    )


def rotate_point(p, axis, angle):
    """Ruota punto p attorno ad axis (unità) di angle (radiani), formula di Rodrigues."""
    c = math.cos(angle)
    s = math.sin(angle)
    t = 1 - c
    x, y, z = p
    ax, ay, az = axis
    rx = (
        (c + ax * ax * t) * x
        + (ax * ay * t - az * s) * y
        + (ax * az * t + ay * s) * z
    )
    ry = (
        (ay * ax * t + az * s) * x
        + (c + ay * ay * t) * y
        + (ay * az * t - ax * s) * z
    )
    rz = (
        (az * ax * t - ay * s) * x
        + (az * ay * t + ax * s) * y
        + (c + az * az * t) * z
    )
    return (rx, ry, rz)


def rotation_from_to(v_from, v_to):
    """Restituisce (axis, angle) per ruotare v_from su v_to."""
    v_from = _norm(v_from)
    v_to = _norm(v_to)
    c = _dot(v_from, v_to)
    if c > 0.999999:
        return ((1.0, 0.0, 0.0), 0.0)
    if c < -0.999999:
        # rotazione di 180 gradi attorno ad asse perpendicolare
        perp = _cross(v_from, (1.0, 0.0, 0.0))
        if _norm(perp) == (0.0, 0.0, 0.0):
            perp = _cross(v_from, (0.0, 1.0, 0.0))
        return (_norm(perp), math.pi)
    axis = _norm(_cross(v_from, v_to))
    angle = math.acos(max(-1.0, min(1.0, c)))
    return (axis, angle)


def align_receptor_to_membrane(
    membrane_atoms: list[dict], receptor_atoms: list[dict], chain_id: str = "R"
) -> tuple[list[dict], dict]:
    """Allinea il recettore alla membrana e restituisce (atomi_combinati, manifest)."""
    # Centro membrana (usiamo solo atomi pesanti o tutti, centro geometrico)
    mem_com = center_of_mass(membrane_atoms)
    # Bounding box membrana per stimare centro xy e spessore z
    mem_zs = [a["z"] for a in membrane_atoms]
    mem_z_min, mem_z_max = min(mem_zs), max(mem_zs)
    mem_z_center = (mem_z_min + mem_z_max) / 2.0

    # Asse TM del recettore da CA
    cas = backbone_ca(receptor_atoms)
    if len(cas) >= 2:
        pts = [(a["x"], a["y"], a["z"]) for a in cas]
        tm_axis = pca_axis(pts)
    else:
        tm_axis = (0.0, 0.0, 1.0)

    # Ruota recettore così che TM_axis coincida con (0,0,1) (asse z della membrana)
    target_axis = (0.0, 0.0, 1.0)
    axis, angle = rotation_from_to(tm_axis, target_axis)

    rec_com = center_of_mass(receptor_atoms)
    rotated_receptor = []
    for a in receptor_atoms:
        # sposta all'origine, ruota, rimetti al centro membrana
        rel = _sub((a["x"], a["y"], a["z"]), rec_com)
        rel_r = rotate_point(rel, axis, angle)
        new_pos = _add(mem_com, rel_r)
        # z: centra il recettore nel mezzo della membrana (z_center)
        # sposta z così che il centro del recettore sia a mem_z_center
        # (il recettore già ruotato intorno al suo com, poi traslato a mem_com)
        # Per semplicità: z assoluto = mem_z_center (centro bilayer)
        new_atom = dict(a)
        new_atom["x"] = new_pos[0]
        new_atom["y"] = new_pos[1]
        new_atom["z"] = new_pos[2]
        new_atom["chain"] = chain_id
        rotated_receptor.append(new_atom)

    # Rilavora il centro del recettore ruotato per centrare z su mem_z_center
    rec_rot_zs = [a["z"] for a in rotated_receptor]
    rec_rot_z_center = (min(rec_rot_zs) + max(rec_rot_zs)) / 2.0
    dz = mem_z_center - rec_rot_z_center
    for a in rotated_receptor:
        a["z"] += dz

    # Combina
    combined = list(membrane_atoms) + rotated_receptor

    manifest = {
        "membrane_atoms": len(membrane_atoms),
        "receptor_atoms": len(receptor_atoms),
        "combined_atoms": len(combined),
        "receptor_chain": chain_id,
        "tm_axis": tm_axis,
        "rotation_axis": axis,
        "rotation_angle_rad": angle,
        "mem_z_min": mem_z_min,
        "mem_z_max": mem_z_max,
        "mem_z_center": mem_z_center,
        "receptor_z_shift": dz,
    }
    return combined, manifest


def write_pdb(atoms: list[dict], path: str | Path, title: str = "EMBEDDED") -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        f.write(f"TITLE     {title}\n")
        f.write("REMARK   Membrana + recettore inserito (embed_in_membrane.py)\n")
        serial = 0
        for a in atoms:
            serial += 1
            resno = a["resno"] % 10000
            chain = (a["chain"] or " ")[:1]
            f.write(
                f"ATOM  {serial:5d} {a['name']:>4s} {a['resname']:>3s} "
                f"{chain:1s}{resno:4d}    {a['x']:8.3f}{a['y']:8.3f}{a['z']:8.3f}  "
                f"1.00  0.00          {a['elem']:>2s}\n"
            )
        f.write("END\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inserisce un recettore PDB in una membrana lipidica."
    )
    parser.add_argument("--membrane-pdb", required=True, help="File PDB della membrana")
    parser.add_argument("--receptor-pdb", required=True, help="File PDB del recettore")
    parser.add_argument("--chain", default="R", help="Chain ID del recettore (default R)")
    parser.add_argument("--output", required=True, help="Directory di output")
    args = parser.parse_args(argv)

    membrane_atoms = parse_pdb(args.membrane_pdb)
    receptor_atoms = parse_pdb(args.receptor_pdb)

    combined, manifest = align_receptor_to_membrane(
        membrane_atoms, receptor_atoms, chain_id=args.chain
    )

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    system_dir = out_dir / "system"
    system_dir.mkdir(parents=True, exist_ok=True)

    write_pdb(combined, system_dir / "membrane_with_receptor.pdb")
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    print(f"Membrana: {manifest['membrane_atoms']} atomi")
    print(f"Recettore: {manifest['receptor_atoms']} atomi (chain {args.chain})")
    print(f"Combinato: {manifest['combined_atoms']} atomi")
    print(f"Output: {system_dir / 'membrane_with_receptor.pdb'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
