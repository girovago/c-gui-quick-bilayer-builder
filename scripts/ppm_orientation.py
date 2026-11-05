#!/usr/bin/env python3
"""Modulo 1 — Orientamento proteina-membrana via OPM/PPM (cgui-studio).

Recupera o calcola l'orientamento di una proteina rispetto al piano del
bilayer e classifica i residui in base alla posizione relativa alla
membrana (core idrofobico / interfaccia / acqua), preparando l'input per
il Modulo 2 (QC agentico protonazione).

Pipeline:
  1. ``--pdb-id`` → prova a scaricare il PDB orientato dal database OPM
     (membrana centrata su z=0, spessore idrofobico nei REMARK).
  2. Se OPM non ha la proteina (HTTP 404) → invia il PDB originale al
     PPM server di OPM per il calcolo de novo dell'orientamento.
  3. Se anche PPM server fallisce e ``--pdb-file`` è fornito → usa il
     file locale assumendo che sia già orientato (l'utente è responsabile).

  4. Classifica ogni residuo in base alla coordinata z (asse normale della
     membrana, Å) rispetto al piano del bilayer::

         hydrophobic_core : |z| < hydrophobic_thickness / 2
         interface        : hydrophobic_thickness/2 <= |z| < thickness/2 + 5
         aqueous          : |z| >= hydrophobic_thickness / 2 + 5

  5. Scrive JSON in results/ (default) con orientamento, classificazione
     residui e flag sui residui carichi nel core idrofobico (candidati
     pKa shift per il Modulo 2).

Uso:
    python scripts/ppm_orientation.py --pdb-id 1ABC
    python scripts/ppm_orientation.py --pdb-file oriented.pdb
    python scripts/ppm_orientation.py --pdb-id 1ABC --output results/
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

# ---------------------------------------------------------------------------
# Costanti
# ---------------------------------------------------------------------------

OPM_BASE = "https://opm.phar.umich.edu"
DEFAULT_HYDROPHOBIC_THICKNESS = 30.0  # Å (tipico POPC/POPC-like)
HEADGROUP_REGION = 5.0  # Å oltre lo spessore idrofobico (regione headgroup)
NETWORK_TIMEOUT = 30  # secondi

# Scala di idrofobicità Kyte-Doolittle (standard, 1982)
KYTE_DOOLITTLE = {
    "ALA": 1.8, "ARG": -4.5, "ASN": -3.5, "ASP": -3.5, "CYS": 2.5,
    "GLN": -3.5, "GLU": -3.5, "GLY": -0.4, "HIS": -3.2, "ILE": 4.5,
    "LEU": 3.8, "LYS": -3.9, "MET": 1.9, "PHE": 2.8, "PRO": -1.6,
    "SER": -0.8, "THR": -0.7, "TRP": -0.9, "TYR": -1.3, "VAL": 4.2,
}

# Residui titolabili/carichi — candidati pKa shift se nel core idrofobico
TITRATABLE = {"ARG", "LYS", "HIS", "ASP", "GLU", "CYS", "TYR"}

REGION_BOUNDARIES = {
    "hydrophobic_core": 0.5,  # frazione di thickness/2
}


# ---------------------------------------------------------------------------
# Recupero PDB orientato
# ---------------------------------------------------------------------------

def fetch_opm_pdb(pdb_id: str) -> tuple[str, float | None] | None:
    """Scarica il PDB orientato da OPM per il pdb_id dato.

    Returns:
        Tupla (contenuto_pdb, hydrophobic_thickness) oppure None se la
        proteina non è presente in OPM o la rete non è raggiungibile.
    """
    url = f"{OPM_BASE}/pdb/{pdb_id.strip().lower()}.pdb"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "cgui-studio/1.0"})
        with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT) as resp:
            content = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            print(f"  OPM: {pdb_id} non presente (404).", file=sys.stderr)
            return None
        print(f"  OPM: HTTP {exc.code} per {url}.", file=sys.stderr)
        return None
    except (urllib.error.URLError, OSError) as exc:
        print(f"  OPM: rete non raggiungibile ({exc}).", file=sys.stderr)
        return None

    thickness = _parse_thickness(content)
    return content, thickness


def _parse_thickness(pdb_content: str) -> float | None:
    """Estrae lo spessore idrofobico dai REMARK del PDB orientato OPM.

    Cerca righe REMARK contenenti 'thickness' (case-insensitive) e
    restituisce il primo valore numerico float trovato. Se non trovato,
    restituisce None (il chiamante userà il default).
    """
    for line in pdb_content.splitlines():
        if not line.startswith("REMARK"):
            continue
        lowered = line.lower()
        if "thickness" in lowered:
            for token in line.split():
                try:
                    return float(token)
                except ValueError:
                    continue
    return None


def run_ppm_server(pdb_content: str) -> tuple[str, float | None] | None:
    """Invia il PDB al PPM server di OPM per orientamento de novo.

    Il PPM server accetta upload PDB e restituisce il PDB orientato con
    il bilayer centrato su z=0. Endpoint: {OPM_BASE}/ppm_server.

    Returns:
        Tupla (contenuto_pdb_orientato, hydrophobic_thickness) oppure
        None se il servizio non risponde o la risposta non è un PDB valido.
    """
    url = f"{OPM_BASE}/ppm_server"
    boundary = "----cgui-studio-boundary"
    body_parts = [
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="pdbfile"; filename="input.pdb"\r\n',
        b"Content-Type: chemical/x-pdb\r\n\r\n",
        pdb_content.encode(),
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    body = b"".join(body_parts)

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "cgui-studio/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT) as resp:
            content = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        print(f"  PPM server: richiesto fallito ({exc}).", file=sys.stderr)
        return None

    # La risposta del PPM server è una pagina HTML con il PDB embedded o
    # un redirect al file. Cerchiamo righe ATOM: se il corpo contiene un
    # PDB valido (righe ATOM), lo usiamo.
    if "ATOM" not in content and "HETATM" not in content:
        print("  PPM server: risposta senza righe ATOM, non un PDB valido.",
              file=sys.stderr)
        return None

    # Estraggo solo le righe PDB (ATOM/HETATM/REMARK/HEADER/TITLE/END)
    pdb_lines = []
    for line in content.splitlines():
        prefix = line[:6]
        if prefix.startswith(("ATOM  ", "HETATM", "REMARK", "HEADER", "TITLE ", "END")):
            pdb_lines.append(line)
    pdb_out = "\n".join(pdb_lines) + "\n"

    thickness = _parse_thickness(pdb_out)
    return pdb_out, thickness


# ---------------------------------------------------------------------------
# Parsing e classificazione residui
# ---------------------------------------------------------------------------

def parse_pdb_residues(pdb_content: str) -> list[dict]:
    """Estrae i residui dal PDB: per ogni residuo, z medio degli atomi.

    Usa le righe ATOM (proteine; ignora HETATM/acqua per semplicità).
    Raggruppa per (chain, resno) e calcola la z media del residuo.
    """
    residue_z: dict[tuple[str, int], list[float]] = {}
    residue_name: dict[tuple[str, int], str] = {}

    for line in pdb_content.splitlines():
        if not line.startswith("ATOM  "):
            continue
        try:
            chain = line[21]
            res_no = int(line[22:26])
            res_name = line[17:20].strip()
            z = float(line[46:54])
        except (ValueError, IndexError):
            continue
        key = (chain, res_no)
        residue_z.setdefault(key, []).append(z)
        residue_name[key] = res_name

    residues = []
    for (chain, res_no), zs in sorted(residue_z.items()):
        residues.append({
            "chain": chain,
            "resno": res_no,
            "resname": residue_name[(chain, res_no)],
            "z_mean": round(sum(zs) / len(zs), 2),
        })
    return residues


def classify_residues(
    residues: list[dict],
    hydrophobic_thickness: float,
) -> list[dict]:
    """Classifica ogni residuo per regione e calcola flag pKa-shift.

    Regione in base a |z_mean|:
        hydrophobic_core : |z| < thickness/2
        interface        : thickness/2 <= |z| < thickness/2 + HEADGROUP_REGION
        aqueous          : |z| >= thickness/2 + HEADGROUP_REGION

    Flag: residui titolabili (TITRATABLE) nel core idrofobico → candidati
    pKa shift (input per il Modulo 2).
    """
    half = hydrophobic_thickness / 2.0
    out = []
    for res in residues:
        z = res["z_mean"]
        abs_z = abs(z)
        resname = res["resname"]

        if abs_z < half:
            region = "hydrophobic_core"
        elif abs_z < half + HEADGROUP_REGION:
            region = "interface"
        else:
            region = "aqueous"

        hydrophobicity = KYTE_DOOLITTLE.get(resname)
        titratable = resname in TITRATABLE
        flagged = titratable and region == "hydrophobic_core"

        out.append({
            "chain": res["chain"],
            "resno": res["resno"],
            "resname": resname,
            "z_mean": z,
            "abs_z": abs_z,
            "region": region,
            "hydrophobicity": hydrophobicity,
            "titratable": titratable,
            "flagged_pka_shift": flagged,
        })
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Modulo 1 — orientamento proteina-membrana via OPM/PPM.",
    )
    parser.add_argument(
        "--pdb-id", type=str, default=None,
        help="PDB ID 4 caratteri (es. 1ABC). Recupera da OPM o calcola via PPM.",
    )
    parser.add_argument(
        "--pdb-file", type=str, default=None,
        help="File PDB locale già orientato (fallback se OPM/PPM falliscono).",
    )
    parser.add_argument(
        "--output", type=str, default="results",
        help="Directory base di output (default: results/)",
    )
    parser.add_argument(
        "--thickness", type=float, default=None,
        help="Override spessore idrofobico in Å (default: da OPM o 30 Å).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if not args.pdb_id and not args.pdb_file:
        print("ERRORE: specificare --pdb-id o --pdb-file.", file=sys.stderr)
        return 1

    pdb_id = args.pdb_id.strip().lower() if args.pdb_id else None
    source = None
    pdb_content = None
    thickness = args.thickness

    # --- 1. OPM ---
    if pdb_id:
        print(f"1. OPM: tentativo download per {pdb_id}…")
        result = fetch_opm_pdb(pdb_id)
        if result is not None:
            pdb_content, opm_thickness = result
            source = "OPM"
            if thickness is None:
                thickness = opm_thickness or DEFAULT_HYDROPHOBIC_THICKNESS
            print(f"  OPM: PDB orientato scaricato "
                  f"(spessore {thickness:.1f} Å).")
        else:
            print("  OPM: non disponibile per questa proteina.")

    # --- 2. PPM server (de novo) ---
    if pdb_content is None and pdb_id:
        # Serve un PDB non orientato: scarico dal RCSB
        rcsb_url = f"https://files.rcsb.org/download/{pdb_id.upper()}.pdb"
        print("2. PPM: scarico PDB non orientato da RCSB…")
        try:
            req = urllib.request.Request(rcsb_url, headers={"User-Agent": "cgui-studio/1.0"})
            with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT) as resp:
                raw_pdb = resp.read().decode("utf-8", errors="replace")
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
            print(f"  RCSB: impossibile scaricare {rcsb_url} ({exc}).", file=sys.stderr)
            raw_pdb = None

        if raw_pdb is not None:
            print("  PPM server: invio per orientamento de novo…")
            result = run_ppm_server(raw_pdb)
            if result is not None:
                pdb_content, ppm_thickness = result
                source = "PPM_server"
                if thickness is None:
                    thickness = ppm_thickness or DEFAULT_HYDROPHOBIC_THICKNESS
                print(f"  PPM server: PDB orientato ricevuto "
                      f"(spessore {thickness:.1f} Å).")

    # --- 3. Fallback file locale ---
    if pdb_content is None:
        if args.pdb_file:
            pdb_path = Path(args.pdb_file)
            if not pdb_path.exists():
                print(f"ERRORE: file non trovato: {pdb_path}", file=sys.stderr)
                return 1
            pdb_content = pdb_path.read_text()
            source = "local_file"
            if thickness is None:
                thickness = _parse_thickness(pdb_content) or DEFAULT_HYDROPHOBIC_THICKNESS
            print(f"3. Fallback: uso file locale {pdb_path} "
                  f"(assunto già orientato, spessore {thickness:.1f} Å).")
        else:
            print("ERRORE: nessuna fonte PDB disponibile. "
                  "Specificare --pdb-file come fallback.", file=sys.stderr)
            return 1

    if thickness is None:
        thickness = DEFAULT_HYDROPHOBIC_THICKNESS

    # --- 4. Classificazione residui ---
    residues = parse_pdb_residues(pdb_content)
    classified = classify_residues(residues, thickness)

    summary = {
        "hydrophobic_core": sum(1 for r in classified if r["region"] == "hydrophobic_core"),
        "interface": sum(1 for r in classified if r["region"] == "interface"),
        "aqueous": sum(1 for r in classified if r["region"] == "aqueous"),
        "flagged_pka_shift": sum(1 for r in classified if r["flagged_pka_shift"]),
    }

    # --- 5. Output JSON ---
    outdir = Path(args.output) / f"{date.today().isoformat()}_ppm_orientation_{pdb_id or 'local'}"
    outdir.mkdir(parents=True, exist_ok=True)
    outdir.joinpath("oriented.pdb").write_text(pdb_content)

    result = {
        "pdb_id": pdb_id,
        "source": source,
        "hydrophobic_thickness_angstrom": thickness,
        "headgroup_region_angstrom": HEADGROUP_REGION,
        "n_residues": len(classified),
        "summary": summary,
        "residues": classified,
    }
    out_file = outdir / "orientation.json"
    out_file.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")

    print(f"\nOrientamento completo: {source}")
    print(f"  Residui: {len(classified)}")
    print(f"  Core idrofobico: {summary['hydrophobic_core']}")
    print(f"  Interfaccia: {summary['interface']}")
    print(f"  Acqueo: {summary['aqueous']}")
    print(f"  Residui titolabili nel core (flag pKa shift): "
          f"{summary['flagged_pka_shift']}")
    print(f"  Output: {out_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
