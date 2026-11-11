#!/usr/bin/env python3
"""Modulo 4 — Embed proteina in bilayer (cgui-studio).

Orienta e centra una proteina rispetto al piano della membrana via OPM
(fallback PPM, poi passthrough), quindi esegue ``gmx pdb2gmx`` con
CHARMM36 per ottenere la topologia proteica pronta all'embedding.

Pipeline:
  1. Scarica il PDB da RCSB (``https://files.rcsb.org/download/<id>.pdb``).
  2. Orientamento:
     - OPM (``https://opm.phar.umich.edu/pdb/<id>.pdb``) se disponibile.
     - Altrimenti Modulo 1 (``scripts/ppm_orientation.py``, PPM server).
     - Altrimenti passthrough (z=0, warning nel manifest).
  3. Selezione chain + clean: tiene solo le chain richieste (default tutte),
     rimuove HETATM/acqua/ioni (pdb2gmx non gestisce ligandi non-standard).
  4. ``gmx pdb2gmx -ff charmm36 -water tip3p -ignh -merge no`` con la
     directory charmm36.ff estratta da ``~/.local/share/cgui-studio/charmm36.tgz``.
  5. Split di ``topol.top`` nelle sezioni ``[ moleculetype ]`` per generare
     ``topol_ProteinChain<X>.itp`` per chain.
  6. Manifesto con: pdb_id, source, chains, per_chain_atom_counts,
     protein_net_charge, z_span_nm, tm_z_min, tm_z_max, pdb2gmx_status,
     pdb2gmx_log_tail, timestamp.

Uso (CLI):
    PATH=~/miniforge3/envs/cgui-builder/bin:$PATH \
        python scripts/embed_protein.py --pdb 7CFN --out /tmp/opencode/embed_t1 \
        [--chain A] [--prefer OPM|PPM|none]

Uso (lib):
    from scripts.embed_protein import run
    manifest = run(pdb="7CFN", out_dir="/tmp/...", chain_filter=["A"], prefer="OPM")

Tre funzioni pure:
    orient_protein(pdb, prefer) -> (pdb_text, source, thickness)
    prepare_pdb2gmx(pdb_text, out_dir, gmx, ff_dir) -> CompletedProcess
    read_chain_info(pdb_text) -> list[dict]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

# Import del modulo paths (per data_dir): lo script sta in scripts/ e non e'
# un modulo del pacchetto (stesso pattern usato per l'import di ppm_orientation).
try:
    from cgui_studio import paths  # type: ignore
except ImportError:
    _src = Path(__file__).resolve().parents[1] / "src"
    if str(_src) not in sys.path:
        sys.path.insert(0, str(_src))
    from cgui_studio import paths  # type: ignore

# ---------------------------------------------------------------------------
# Costanti
# ---------------------------------------------------------------------------

RCSB_URL = "https://files.rcsb.org/download/{pdb_id}.pdb"
OPM_URL = "https://opm.phar.umich.edu/pdb/{pdb_id}.pdb"
NETWORK_TIMEOUT = 20  # secondi per OPM (per spec)
DEFAULT_HYDROPHOBIC_THICKNESS = 30.0  # Å
GMRX_WATER = "tip3p"
CHARMM36_TARBALL = paths.data_dir() / "charmm36.tgz"

# Residui del dominio transmembrana di GPBAR1/TGR5 (approssimati, per
# stimare tm_z_min / tm_z_max se non c'è orientamento OPM).
TM_RESIDUES_DEFAULT = list(range(30, 231))


# ---------------------------------------------------------------------------
# Rete
# ---------------------------------------------------------------------------

def _http_get(url: str, timeout: int = NETWORK_TIMEOUT) -> str | None:
    """GET HTTP; restituisce il testo o None su errore."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "cgui-studio/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        return None


def fetch_rcsb_pdb(pdb_id: str) -> str | None:
    """Scarica il PDB non orientato da RCSB."""
    return _http_get(RCSB_URL.format(pdb_id=pdb_id.upper()))


# ---------------------------------------------------------------------------
# Orientamento (OPM / PPM / passthrough)
# ---------------------------------------------------------------------------

def orient_protein(pdb_id: str, prefer: str = "OPM") -> tuple[str, str, float]:
    """Orienta la proteina rispetto al piano membrane (z=0 al centro).

    Returns:
        (pdb_text, source, hydrophobic_thickness)
        source in {"OPM", "PPM_server", "passthrough"}.
    """
    pdb_id = pdb_id.strip().upper()
    thickness = DEFAULT_HYDROPHOBIC_THICKNESS

    def _try_opm() -> tuple[str, str, float] | None:
        content = _http_get(OPM_URL.format(pdb_id=pdb_id), timeout=NETWORK_TIMEOUT)
        if content and content.strip().startswith(
            ("HEADER", "REMARK", "ATOM", "TITLE", "COMPND")
        ):
            opm_thick = _parse_opm_thickness(content)
            if opm_thick is not None:
                thickness = opm_thick
            return content, "OPM", thickness
        return None

    def _try_ppm(raw_pdb: str) -> tuple[str, str, float] | None:
        try:
            sys.path.insert(
                0, str(Path(__file__).resolve().parents[1] / "scripts")
            )
            from ppm_orientation import run_ppm_server  # type: ignore
            result = run_ppm_server(raw_pdb)
            if result is not None:
                ppm_text, ppm_thick = result
                if ppm_thick is not None:
                    thickness = ppm_thick
                return ppm_text, "PPM_server", thickness
        except Exception:  # noqa: BLE001
            pass
        return None

    # Ordine dei tentativi in base alla preferenza
    if prefer == "none":
        attempts: list[tuple[str, tuple[str, str, float] | None]] = []
    elif prefer == "PPM":
        raw_pdb = fetch_rcsb_pdb(pdb_id)
        attempts = [
            ("PPM", _try_ppm(raw_pdb) if raw_pdb is not None else None),
            ("OPM", _try_opm()),
        ]
    else:  # prefer == "OPM": OPM prima, poi PPM, poi passthrough
        attempts = [("OPM", _try_opm())]
        raw_pdb = fetch_rcsb_pdb(pdb_id)
        if attempts[0][1] is None:
            attempts.append(("PPM", _try_ppm(raw_pdb) if raw_pdb is not None else None))

    for _label, result in attempts:
        if result is not None:
            return result

    # --- Passthrough ---
    if "raw_pdb" not in locals() or raw_pdb is None:
        raw_pdb = fetch_rcsb_pdb(pdb_id)
    if raw_pdb is not None:
        return raw_pdb, "passthrough", thickness
    raise RuntimeError(f"Impossibile recuperare il PDB {pdb_id} da RCSB.")


def _parse_opm_thickness(pdb_content: str) -> float | None:
    """Estrae lo spessore idrofobico dai REMARK di un PDB orientato OPM."""
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


# ---------------------------------------------------------------------------
# PDB pulizia / chain
# ---------------------------------------------------------------------------

# Side chain canoniche (heavy atoms richiesti dall'rtp). Un residuo che ne
# manca anche solo uno non è accettato da pdb2gmx: con --fix-missing gly
# viene convertito in GLY (backbone preservato, side chain rimosso).
CANONICAL_SIDE_CHAINS: dict[str, set[str]] = {
    "ALA": {"CB"},
    "ARG": {"CB", "CG", "CD", "NE", "CZ", "NH1", "NH2"},
    "ASN": {"CB", "CG", "OD1", "ND2"},
    "ASP": {"CB", "CG", "OD1", "OD2"},
    "CYS": {"CB", "SG"},
    "GLU": {"CB", "CG", "CD", "OE1", "OE2"},
    "GLN": {"CB", "CG", "CD", "OE1", "NE2"},
    "GLY": set(),
    "HIS": {"CB", "CG", "ND1", "CD2", "CE1", "NE2"},
    "ILE": {"CB", "CG1", "CG2", "CD1"},
    "LEU": {"CB", "CG", "CD1", "CD2"},
    "LYS": {"CB", "CG", "CD", "CE", "NZ"},
    "MET": {"CB", "CG", "SD", "CE"},
    "PHE": {"CB", "CG", "CD1", "CD2", "CE1", "CE2", "CZ"},
    "PRO": {"CB", "CG", "CD"},
    "SER": {"CB", "OG"},
    "THR": {"CB", "OG1", "CG2"},
    "TRP": {"CB", "CG", "CD1", "CD2", "NE1", "CE2", "CE3", "CZ2", "CZ3", "CH2"},
    "TYR": {"CB", "CG", "CD1", "CD2", "CE1", "CE2", "CZ", "OH"},
    "VAL": {"CB", "CG1", "CG2"},
}
BACKBONE_ATOMS = {"N", "CA", "C", "O", "OXT"}


def read_chain_info(pdb_text: str) -> list[dict]:
    """Lista di dict per chain: chain, n_atoms, n_residues, resnames."""
    chains: dict[str, dict] = {}
    for line in pdb_text.splitlines():
        if not line.startswith("ATOM  "):
            continue
        chain = line[21]
        res_no = int(line[22:26])
        res_name = line[17:20].strip()
        info = chains.setdefault(
            chain, {"chain": chain, "atoms": 0, "residues": set(), "resnames": set()}
        )
        info["atoms"] += 1
        info["residues"].add(res_no)
        info["resnames"].add(res_name)
    out = []
    for chain in sorted(chains):
        info = chains[chain]
        out.append({
            "chain": chain,
            "n_atoms": info["atoms"],
            "n_residues": len(info["residues"]),
            "resnames": sorted(info["resnames"]),
        })
    return out


def filter_chains(pdb_text: str, chain_filter: list[str] | None) -> str:
    """Tiene solo le chain in ``chain_filter`` (tutte se None)."""
    if not chain_filter:
        return pdb_text
    wanted = set(chain_filter)
    kept = [line for line in pdb_text.splitlines()
            if not line.startswith("ATOM  ") or line[21] in wanted]
    return "\n".join(kept) + "\n"


def strip_hetatm(pdb_text: str) -> str:
    """Rimuove righe HETATM (ligand, acqua, ioni)."""
    kept = [line for line in pdb_text.splitlines()
            if not line.startswith("HETATM")]
    return "\n".join(kept) + "\n"


def convert_incomplete_residues(pdb_text: str) -> tuple[str, list[str]]:
    """Converte a GLY i residui con side chain incompleta.

    Le strutture cryo-EM hanno spesso side chain parziali che fanno
    fallire pdb2gmx ("atom CG ... not found"). Ogni residuo incompleto
    viene rimosso dal side chain e rinominato GLY (backbone N/CA/C/O
    preservato). Ritorna (pdb_testo, lista_descrizioni).
    """
    lines = pdb_text.splitlines()
    atoms_per_res: dict[tuple[str, int], set[str]] = {}
    resname_per_res: dict[tuple[str, int], str] = {}
    for _idx, line in enumerate(lines):
        if not line.startswith("ATOM  "):
            continue
        name = line[12:16].strip()
        res_name = line[17:20].strip()
        chain = line[21]
        res_no = int(line[22:26])
        key = (chain, res_no)
        atoms_per_res.setdefault(key, set()).add(name)
        resname_per_res[key] = res_name

    to_fix: dict[tuple[str, int], str] = {}
    for key, atoms in atoms_per_res.items():
        res_name = resname_per_res[key]
        side = CANONICAL_SIDE_CHAINS.get(res_name)
        if not side:  # resname non standard: lo lasciamo a pdb2gmx
            continue
        missing = side - atoms
        if missing:
            to_fix[key] = res_name

    if not to_fix:
        return pdb_text, []

    out: list[str] = []
    for line in lines:
        if line.startswith("ATOM  "):
            res_name = line[17:20].strip()
            chain = line[21]
            res_no = int(line[22:26])
            if (chain, res_no) in to_fix:
                name = line[12:16].strip()
                if name not in BACKBONE_ATOMS:
                    continue  # side chain rimosso
                line = line[:17] + "GLY" + line[20:]  # resname -> GLY
        out.append(line)
    desc = [
        f"chain {chain} res {no}: {rn}->GLY"
        for (chain, no), rn in sorted(to_fix.items())
    ]
    return "\n".join(out) + "\n", desc


# ---------------------------------------------------------------------------
# Coordinate / carica / TM range
# ---------------------------------------------------------------------------

def _atom_coords(pdb_text: str) -> list[tuple[str, int, float, float, float]]:
    """Restituisce (chain, resno, x, y, z) per ogni atomo ATOM.

    Usa ``split()`` (piuttosto che colonne fisse) per tollerare PDB con
    spaziature non standard.
    """
    out = []
    for line in pdb_text.splitlines():
        if not line.startswith("ATOM"):
            continue
        parts = line.split()
        if len(parts) < 9:
            continue
        try:
            chain = parts[4]
            res_no = int(parts[5])
            x = float(parts[6])
            y = float(parts[7])
            z = float(parts[8])
        except (ValueError, IndexError):
            continue
        out.append((chain, res_no, x, y, z))
    return out


def protein_net_charge(pdb_text: str) -> float:
    """Stima la carica netta della proteina dai residui terminali/titolabili.

    Stima grossolana basata sui residui (non sulle cariche assegnate da
    pdb2gmx, che dipendono dalla protontazione). Usata come indicatore nel
    manifest.
    """
    from collections import Counter
    res_count: Counter[tuple[str, int]] = Counter()
    res_name: dict[tuple[str, int], str] = {}
    for line in pdb_text.splitlines():
        if not line.startswith("ATOM  "):
            continue
        chain = line[21]
        res_no = int(line[22:26])
        rn = line[17:20].strip()
        res_count[(chain, res_no)] += 1
        res_name[(chain, res_no)] = rn
    # Cariche a pH ~7.4 (stima): termini +1/-1, laterali titolabili.
    pos = {"ARG", "LYS", "HIS"}
    neg = {"ASP", "GLU"}
    charge = 0.0
    seen = set()
    for (chain, res_no), rn in res_name.items():
        key = (chain, res_no)
        if key in seen:
            continue
        seen.add(key)
        if rn in pos:
            charge += 1.0
        elif rn in neg:
            charge -= 1.0
    # Termini N e C (una catena polipeptidica)
    charge += 1.0  # N-terminale NH3+
    charge -= 1.0  # C-terminale COO-
    return round(charge, 2)


def z_span_nm(pdb_text: str) -> float:
    """Spessore z (max-min) della proteina orientata, in nm."""
    coords = _atom_coords(pdb_text)
    if not coords:
        return 0.0
    zs = [c[4] for c in coords]
    return round((max(zs) - min(zs)) / 10.0, 4)


def tm_z_range_nm(pdb_text: str) -> tuple[float, float]:
    """Range z del dominio TM (residui ~30-230), in nm."""
    coords = _atom_coords(pdb_text)
    if not coords:
        return (0.0, 0.0)
    zs = [c[4] for c in coords if TM_RESIDUES_DEFAULT[0] <= c[1] <= TM_RESIDUES_DEFAULT[-1]]
    if not zs:
        zs = [c[3] for c in coords]
    return (round(min(zs) / 10.0, 4), round(max(zs) / 10.0, 4))


# ---------------------------------------------------------------------------
# gmx / force field
# ---------------------------------------------------------------------------

def get_gmx_binary() -> str:
    """Restituisce il path di gmx (PATH o env cgui-builder)."""
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
    raise RuntimeError("Eseguibile gmx non trovato nel PATH.")


def ensure_charmm36_ff() -> Path:
    """Estrae charmm36.ff da <data_dir>/charmm36.tgz se serve."""
    local_ff = paths.data_dir() / "charmm36.ff"
    if local_ff.exists() and (local_ff / "forcefield.itp").exists():
        return local_ff
    tarball = CHARMM36_TARBALL
    if not tarball.exists():
        raise FileNotFoundError(
            f"charmm36.tgz non trovato: {tarball}. "
            "Eseguire prima lo scaricamento del force field CHARMM36."
        )
    local_ff.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball, "r:gz") as tf:
        members = [
            m for m in tf.getmembers()
            if m.name.startswith(
                "toppar_c36_releases-main/gromacs/charmm36-feb2026_cgenff-5.0.ff/"
            ) and not m.name.endswith("/")
        ]
        tf.extractall(local_ff, members=members, filter="data")
    # Sposta i file un livello su (rimuovi il sottoprefix gromacs/...ff/)
    src_dir = local_ff / "toppar_c36_releases-main" / "gromacs" / "charmm36-feb2026_cgenff-5.0.ff"
    if src_dir.exists():
        for item in src_dir.iterdir():
            item.rename(local_ff / item.name)
        shutil_rmtree_safe(local_ff / "toppar_c36_releases-main")
    if not (local_ff / "forcefield.itp").exists():
        raise RuntimeError("forcefield.itp non trovato dopo estrazione charmm36.tgz.")
    return local_ff


def shutil_rmtree_safe(path: Path) -> None:
    import shutil
    shutil.rmtree(path, ignore_errors=True)


def prepare_pdb2gmx(
    pdb_text: str,
    out_dir: Path,
    gmx: str | None = None,
    ff_dir: Path | None = None,
) -> subprocess.CompletedProcess:
    """Esegue gmx pdb2gmx su ``pdb_text`` in ``out_dir``.

    Non interattivo: legge da /dev/null. Ritorna il CompletedProcess
    (anche in caso di fallimento; il chiamante interpreta il codice di uscita).
    """
    if gmx is None:
        gmx = get_gmx_binary()
    if ff_dir is None:
        ff_dir = ensure_charmm36_ff()

    out_dir.mkdir(parents=True, exist_ok=True)
    in_pdb = out_dir / "input.pdb"
    in_pdb.write_text(pdb_text, encoding="utf-8")

    env = os.environ.copy()
    # GMXLIB deve puntare alla directory che CONTIENE charmm36.ff,
    # non alla directory del force field (GROMACS cerca <GMXLIB>/charmm36.ff)
    gmxlib_dir = ff_dir.parent if ff_dir.name.endswith(".ff") else ff_dir
    env["GMXLIB"] = str(gmxlib_dir)

    cmd = [
        gmx, "pdb2gmx",
        "-f", str(in_pdb),
        "-o", str(out_dir / "protein.gro"),
        "-p", str(out_dir / "topol.top"),
        "-ff", "charmm36",
        "-water", GMRX_WATER,
        "-ignh",
        "-merge", "no",
    ]
    return subprocess.run(
        cmd, cwd=out_dir, input="\n", capture_output=True, text=True, timeout=300,
        env=env,
    )


# ---------------------------------------------------------------------------
# Split topol.top per chain
# ---------------------------------------------------------------------------

def split_topol_by_chain(
    topol_path: Path,
    chains: list[str],
    out_dir: Path,
) -> dict[str, Path]:
    """Estrae da topol.top un .itp per chain (sezione [ moleculetype ]).

    Il matching è per nome moleculetype (``Protein_chain<X>``), non per
    indice posizionale, così le chain assenti vengono saltate.
    """
    text = topol_path.read_text()
    parts = re.split(r"\n\s*\[ moleculetype \]\s*\n", text)
    sections: list[tuple[str | None, str]] = []
    for body in parts[1:]:
        name: str | None = None
        for line in body.splitlines():
            s = line.strip()
            if not s or s.startswith(";"):
                continue
            name = s.split()[0]
            break
        sections.append((name, body))

    results: dict[str, Path] = {}
    for chain in chains:
        wanted = f"Protein_chain_{chain}"
        for name, body in sections:
            if name == wanted:
                itp_path = out_dir / f"topol_ProteinChain{chain}.itp"
                itp_path.write_text("[ moleculetype ]\n" + body, encoding="utf-8")
                results[chain] = itp_path
                break
    return results


# ---------------------------------------------------------------------------
# Orchestratore
# ---------------------------------------------------------------------------

def run(
    pdb: str = "7CFN",
    out_dir: str | Path = "results",
    chain_filter: list[str] | None = None,
    prefer: str = "OPM",
    fix_missing: bool = False,
) -> dict:
    """Esegue la pipeline embed_protein e restituisce il manifest."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Orientamento
    pdb_text, source, thickness = orient_protein(pdb, prefer=prefer)

    # 2. Pulizia: solo chain richieste, niente HETATM, side chain mancanti
    pdb_text = filter_chains(pdb_text, chain_filter)
    pdb_text = strip_hetatm(pdb_text)
    fixed_residues: list[str] = []
    if fix_missing:
        pdb_text, fixed_residues = convert_incomplete_residues(pdb_text)

    chain_info = read_chain_info(pdb_text)
    chains = [c["chain"] for c in chain_info]
    per_chain_atom_counts = {c["chain"]: c["n_atoms"] for c in chain_info}

    # Salva PDB orientato
    (out_dir / "opm_oriented.pdb").write_text(pdb_text, encoding="utf-8")

    # Coordinate / carica / TM range
    z_span = z_span_nm(pdb_text)
    tm_z_min, tm_z_max = tm_z_range_nm(pdb_text)
    net_charge = protein_net_charge(pdb_text)

    # 3. pdb2gmx
    gmx = get_gmx_binary()
    try:
        cp = prepare_pdb2gmx(pdb_text, out_dir, gmx=gmx)
        pdb2gmx_status = "ok" if cp.returncode == 0 else "fail"
    except Exception as exc:  # noqa: BLE001
        cp = subprocess.CompletedProcess(args=[], returncode=1,
                                         stdout="", stderr=str(exc))
        pdb2gmx_status = "fail"

    log_tail = "\n".join((cp.stderr + "\n" + cp.stdout).splitlines()[-12:])

    # 4. Split topologia per chain
    itp_results: dict[str, Path] = {}
    topol_path = out_dir / "topol.top"
    if topol_path.exists() and chains:
        itp_results = split_topol_by_chain(topol_path, chains, out_dir)

    manifest = {
        "pdb_id": pdb.upper(),
        "source": source,
        "chains": chains,
        "per_chain_atom_counts": per_chain_atom_counts,
        "protein_net_charge": net_charge,
        "z_span_nm": z_span,
        "tm_z_min_nm": tm_z_min,
        "tm_z_max_nm": tm_z_max,
        "hydrophobic_thickness_angstrom": thickness,
        "pdb2gmx_status": pdb2gmx_status,
        "pdb2gmx_log_tail": log_tail,
        "itp_files": {k: str(v.relative_to(out_dir)) for k, v in itp_results.items()},
        "fixed_residues": fixed_residues,
        "timestamp": (
            datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        ),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    print(f"Embed proteina completato: {pdb} (source={source})")
    print(f"  Chain: {chains}")
    print(f"  Atomi per chain: {per_chain_atom_counts}")
    print(f"  Carica netta stimata: {net_charge}")
    print(f"  z_span: {z_span} nm | TM z: [{tm_z_min}, {tm_z_max}] nm")
    print(f"  pdb2gmx: {pdb2gmx_status}")
    print(f"  Output: {out_dir}")
    return manifest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Modulo 4 — orienta e centra la proteina via OPM/PPM, "
        "poi gmx pdb2gmx (CHARMM36) per l'embedding nel bilayer.",
    )
    parser.add_argument("--pdb", type=str, default="7CFN",
                        help="PDB ID (default: 7CFN).")
    parser.add_argument("--out", type=str, default="results",
                        help="Directory di output (default: results/).")
    parser.add_argument(
        "--chain", type=str, default=None,
        help="Chain da tenere (es. A). Default: tutte le chain.")
    parser.add_argument(
        "--prefer", type=str, default="OPM",
        choices=["OPM", "PPM", "none"],
        help="Precedenza orientamento (default: OPM).")
    parser.add_argument(
        "--fix-missing", action="store_true",
        help="Converte a GLY i residui con side chain incompleta "
             "(tipico delle strutture cryo-EM) per far passare pdb2gmx.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    chain_filter = [args.chain.strip().upper()] if args.chain else None
    try:
        run(pdb=args.pdb, out_dir=args.out,
            chain_filter=chain_filter, prefer=args.prefer,
            fix_missing=args.fix_missing)
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"ERRORE: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
