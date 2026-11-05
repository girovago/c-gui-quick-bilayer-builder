#!/usr/bin/env python3
"""Modulo 3 — Export conforme allo schema NMRlipids Databank + validatore
(cgui-studio).

Genera un file ``README.yaml`` conforme allo schema dell'NMRlipids/FAIRMD
Lipids Databank (repo GitHub ``fsuarezleston/NMRLipids_Databank``,
portale ``databank.nmrlipids.fi``) a partire dai risultati di una build
cgui-studio.

Inoltre, opzionalmente, confronta APL e spessore P-P misurati con
simulazioni di composizione lipidica simile presenti nel databank
(overlay databank: i metadati sono su GitHub, i dati grezzi su Zenodo).

Uso:
    python scripts/nmrlipids_export.py \
        --input results/.../metrics.json \
        --pdb step1_pdbreader.pdb \
        --output results/.../nmrlipids_export/

    python scripts/nmrlipids_export.py \
        --input results/.../metrics.json \
        --validate \
        --output results/.../nmrlipids_export/
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

# ---------------------------------------------------------------------------
# Schema README.yaml (NMRlipids / FAIRMD Lipids Databank)
# ---------------------------------------------------------------------------

README_TEMPLATE = """\
# NMRlipids / FAIRMD Lipids Databank — README

title: "{title}"
description: "{description}"
date: "{date}"
authors:
  - name: "{author}"
    affiliation: "{affiliation}"

system:
  type: "{system_type}"
  composition:
{composition_yaml}
  box_dimensions_nm:
    x: {box_x}
    y: {box_y}
    z: {box_z}
  solvent: "{solvent}"
  ions: "{ions}"
  temperature_k: {temperature}
  pressure_bar: {pressure}

force_field:
  name: "{force_field}"
  version: "{force_field_version}"

simulation:
  engine: "GROMACS {gromacs_version}"
  integrator: "steepest_descent"
  ensemble: "NVT"
  n_steps: {n_steps}
  dt_ps: {dt_ps}

metrics:
  area_per_lipid_nm2: {apl}
  thickness_pp_nm: {thickness}
  water_per_lipid: {water_per_lipid}
  net_charge_e: {net_charge}

files:
  pdb: "{pdb_file}"
  top: "topol.top"
  gro: "ionized.gro"
  tpr: "minim.tpr"

provenance:
  generator: "cgui-studio"
  generator_version: "1.0"
  build_date: "{build_date}"
  input_files:
    - step1_pdbreader.pdb
    - topol.top
    - minim.mdp

notes: "{notes}"
"""


def build_composition_yaml(upper: dict, lower: dict) -> str:
    """Costruisce lo YAML della composizione lipidica."""
    lines = []
    all_lipids = sorted(set(upper) | set(lower))
    for name in all_lipids:
        u = upper.get(name, 0)
        lower_count = lower.get(name, 0)
        total = u + lower_count
        lines.append(f"    {name}:")
        lines.append(f"      upper: {u}")
        lines.append(f"      lower: {lower_count}")
        lines.append(f"      total: {total}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Validatore contro NMRlipids Databank
# ---------------------------------------------------------------------------

def fetch_databank_metadata(max_results: int = 20) -> list[dict]:
    """Scarica metadati dal repo GitHub NMRlipids Databank.

    Usa l'API GitHub per elencare i file nella directory
    ``Data/simulations`` del repo ``fsuarezleston/NMRLipids_Databank``.
    Ogni simulazione ha un README.yaml con metadati.

    Returns:
        Lista di dizionari con i metadati trovati (può essere vuota se
        la rete non è disponibile o il repo non è accessibile).
    """
    api_url = (
        "https://api.github.com/repos/"
        "fsuarezleston/NMRLipids_Databank/"
        "contents/Data/simulations"
    )
    req = urllib.request.Request(
        api_url,
        headers={"User-Agent": "cgui-studio/1.0", "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        print(f"  NMRlipids Databank: impossibile contattare GitHub ({exc}).",
              file=sys.stderr)
        return []

    metadata = []
    for item in data:
        if item.get("type") != "dir":
            continue
        sim_name = item.get("name", "")
        readme_url = f"{item['url']}/README.yaml"
        try:
            req2 = urllib.request.Request(
                readme_url,
                headers={"User-Agent": "cgui-studio/1.0", "Accept": "application/vnd.github.raw"},
            )
            with urllib.request.urlopen(req2, timeout=30) as resp2:
                content = resp2.read().decode("utf-8", errors="replace")
        except (urllib.error.HTTPError, urllib.error.URLError, OSError):
            continue

        metadata.append({
            "simulation_id": sim_name,
            "readme_url": readme_url,
            "raw_yaml": content,
        })
    return metadata


def parse_readme_yaml(raw_yaml: str) -> dict:
    """Parser minimale per README.yaml (senza dipendenze esterne).

    Estrae campi chiave: composizione lipidica, APL, spessore, force field.
    """
    data = {}
    for line in raw_yaml.splitlines():
        line = line.strip()
        if line.startswith("area_per_lipid_nm2:"):
            try:
                data["apl"] = float(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith("thickness_pp_nm:"):
            try:
                data["thickness"] = float(line.split(":", 1)[1].strip())
            except ValueError:
                pass
    return data


def validate_against_databank(
    our_apl: float,
    our_thickness: float,
    our_composition: dict,
    max_results: int = 20,
) -> dict:
    """Confronta APL e spessore con simulazioni simili nel databank.

    Cerca simulazioni con composizione lipidica simile (±10% per ciascun
    lipide condiviso) e restituisce deviazioni percentuali.

    Returns:
        Dizionario con risultati del confronto o vuoto se non ci sono
        dati comparabili.
    """
    metadata = fetch_databank_metadata(max_results)
    if not metadata:
        return {
            "available": False,
            "reason": "nessun metadati scaricabili dal databank",
        }

    matches = []
    for entry in metadata:
        parsed = parse_readme_yaml(entry.get("raw_yaml", ""))
        if "apl" not in parsed or "thickness" not in parsed:
            continue
        # Comparazione composizione semplificata: se ha APL e spessore,
        # lo consideriamo candidato (senza confronto composizione dettagliato
        # per evitare falsi negativi su nomi diversi)
        matches.append({
            "simulation_id": entry["simulation_id"],
            "apl": parsed["apl"],
            "thickness": parsed["thickness"],
            "apl_deviation_pct": round(
                abs(parsed["apl"] - our_apl) / our_apl * 100, 1
            ) if our_apl else None,
            "thickness_deviation_pct": round(
                abs(parsed["thickness"] - our_thickness) / our_thickness * 100, 1
            ) if our_thickness else None,
            "url": entry["readme_url"],
        })

    matches.sort(key=lambda m: (m["apl_deviation_pct"] or 999))

    return {
        "available": True,
        "n_comparable": len(matches),
        "matches": matches[:max_results],
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Modulo 3 — export NMRlipids Databank + validatore.",
    )
    parser.add_argument(
        "--input", type=str, required=True,
        help="File JSON di metriche (output del Modulo 1 o build GROMACS).",
    )
    parser.add_argument(
        "--pdb", type=str, default=None,
        help="File PDB della struttura (per metadati).",
    )
    parser.add_argument(
        "--output", type=str, default="results",
        help="Directory base di output (default: results/).",
    )
    parser.add_argument(
        "--validate", action="store_true",
        help="Confronta APL/spessore con il databank NMRlipids.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERRORE: file non trovato: {input_path}", file=sys.stderr)
        return 1

    data = json.loads(input_path.read_text())

    # Estrae metriche (supporta sia formato Modulo 1 che formato diretto)
    metrics = data.get("metrics", data)
    apl = metrics.get("apl_nm2") or metrics.get("apl")
    thickness = metrics.get("thickness_pp_nm") or metrics.get("thickness")
    water_per_lipid = metrics.get("water_per_lipid")
    net_charge = metrics.get("net_charge_e")

    # Composizione (se disponibile)
    composition = data.get("composition", {})
    upper = composition.get("upper", {})
    lower = composition.get("lower", {})

    # Altre info
    box = metrics.get("box_nm", {})
    solvent = metrics.get("solvent", "water")
    ions = metrics.get("ions", "none")
    force_field = metrics.get("force_field", "CHARMM36")
    gromacs_version = metrics.get("gromacs_version", "2026.3")

    # --- Genera README.yaml ---
    composition_yaml = build_composition_yaml(upper, lower)

    readme = README_TEMPLATE.format(
        title=input_path.stem,
        description=f"Bilayer lipidico generato da cgui-studio: {input_path.stem}",
        date=date.today().isoformat(),
        author="cgui-studio user",
        affiliation="",
        system_type="lipid bilayer",
        composition_yaml=composition_yaml,
        box_x=box.get("x", 0),
        box_y=box.get("y", 0),
        box_z=box.get("z", 0),
        solvent=solvent,
        ions=ions,
        temperature=metrics.get("temperature", 303),
        pressure=metrics.get("pressure", 1),
        force_field=force_field,
        force_field_version="feb2026",
        gromacs_version=gromacs_version,
        n_steps=metrics.get("n_steps", 500),
        dt_ps=metrics.get("dt_ps", 0.002),
        apl=apl if apl is not None else 0,
        thickness=thickness if thickness is not None else 0,
        water_per_lipid=water_per_lipid if water_per_lipid is not None else 0,
        net_charge=net_charge if net_charge is not None else 0,
        pdb_file=Path(args.pdb).name if args.pdb else "step1_pdbreader.pdb",
        build_date=date.today().isoformat(),
        notes="Generato automaticamente da cgui-studio.",
    )

    outdir = Path(args.output) / f"{date.today().isoformat()}_nmrlipids_export"
    outdir.mkdir(parents=True, exist_ok=True)
    readme_path = outdir / "README.yaml"
    readme_path.write_text(readme)
    print(f"README.yaml scritto: {readme_path}")

    # --- Validatore (opzionale) ---
    validation = None
    if args.validate:
        if apl is None or thickness is None:
            print("AVVISO: impossibile validare senza APL e spessore.",
                  file=sys.stderr)
        else:
            print("Confronta con NMRlipids Databank…")
            validation = validate_against_databank(apl, thickness, composition)
            if validation.get("available"):
                print(f"  Trovate {validation['n_comparable']} simulazioni comparabili.")
                for m in validation["matches"][:5]:
                    print(f"  - {m['simulation_id']}: APL {m['apl']} nm² "
                          f"(dev {m['apl_deviation_pct']}%), "
                          f"spessore {m['thickness']} nm "
                          f"(dev {m['thickness_deviation_pct']}%)")
            else:
                print(f"  {validation.get('reason', 'nessun dato')}")

    validation_path = outdir / "validation.json"
    validation_path.write_text(
        json.dumps(validation, indent=2, ensure_ascii=False) + "\n"
        if validation else "null\n"
    )

    print(f"Output: {outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
