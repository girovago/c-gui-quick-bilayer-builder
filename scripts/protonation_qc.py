#!/usr/bin/env python3
"""Modulo 2 — QC agentico protonazione context-aware (cgui-studio).

Per ogni residuo identificato dal Modulo 1 come titolabile e posizionato nel
core idrofobico o all'interfaccia membrana, cerca in letteratura dati su pKa
shift indotti dall'ambiente membranoso e propone lo stato di protonazione a
pH fisiologico (7.4), con citazione della fonte.

Pipeline:
  1. Legge il JSON di output del Modulo 1 (orientation.json).
  2. Filtra i residui con ``flagged_pka_shift == True``.
  3. Per ciascuno, cerca su PubMed (E-utilities ESearch/EFetch) o sul web
     la stringa ``"<residuo>" pKa shift membrane <posizione>``.
  4. Estrae dal abstract/full text il valore di pKa riportato e il contesto
     (core idrofobico vs interfaccia).
  5. Mappa lo stato di protonazione a pH 7.4:
       pH < pKa  → protoneato (carico)
       pH ≥ pKa  → deprotonato (neutro/negativo a seconda del residuo)
  6. Scrive JSON in results/ con citazione, confidenza e flag ambiguità.

Uso:
    python scripts/protonation_qc.py --input results/.../orientation.json
    python scripts/protonation_qc.py --input results/.../orientation.json \
        --email giorgi.veneruso@studenti.unina.it

Nota: per ricerche su PubMed è consigliato passare --email (NCBI richiede
un contatto per rate-limit elevati). Senza email, le query vanno comunque
ma con limiti più stretti.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

PHYSIOLOGICAL_PH = 7.4
NETWORK_TIMEOUT = 30

# Residui titolabili con stato di protonazione a pH fisiologico
PROTONATION_STATES = {
    "ARG":  {"name": "arginina",   "protonated": "+",   "deprotonated": "+"},
    "LYS":  {"name": "lisina",     "protonated": "+",   "deprotonated": "+"},
    "HIS":  {"name": "istidina",   "protonated": "+",   "deprotonated": "0"},
    "ASP":  {"name": "acido aspartico", "protonated": "0", "deprotonated": "-"},
    "GLU":  {"name": "acido glutamico", "protonated": "0",  "deprotonated": "-"},
    "CYS":  {"name": "cisteina",   "protonated": "0",   "deprotonated": "-"},
    "TYR":  {"name": "tirosina",   "protonated": "0",   "deprotonated": "-"},
}

POSITION_LABELS = {
    "hydrophobic_core": "core idrofobico",
    "interface": "regione interfacciale",
    "aqueous": "regione acquosa",
}


# ---------------------------------------------------------------------------
# Ricerca letteratura
# ---------------------------------------------------------------------------

def search_pubmed(term: str, email: str | None = None) -> list[dict]:
    """Cerca su PubMed tramite E-utilities e restituisce lista di record.

    Ogni record ha: pmid, title, abstract (se disponibile).
    """
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    params = {
        "db": "pubmed",
        "term": term,
        "retmode": "json",
        "retmax": "5",
        "sort": "relevance",
    }
    if email:
        params["email"] = email

    url = base + "esearch.fcgi?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=NETWORK_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        print(f"  PubMed ESearch fallito: {exc}", file=sys.stderr)
        return []

    id_list = data.get("esearchresult", {}).get("idlist", [])
    if not id_list:
        return []

    # Fetch dettagli
    id_param = ",".join(id_list)
    fetch_url = (
        base + "efetch.fcgi?" +
        urllib.parse.urlencode({
            "db": "pubmed",
            "id": id_param,
            "retmode": "xml",
        })
    )
    try:
        with urllib.request.urlopen(fetch_url, timeout=NETWORK_TIMEOUT) as resp:
            xml = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        print(f"  PubMed EFetch fallito: {exc}", file=sys.stderr)
        return []

    return _parse_pubmed_xml(xml)


def _parse_pubmed_xml(xml: str) -> list[dict]:
    """Estrae PMID, titolo e abstract dal XML di EFetch."""
    results = []
    # Parsing minimale senza dipendenze esterne: split su tag
    articles = re.split(r"<PubmedArticle>", xml)
    for block in articles[1:]:  # salta il primo vuoto
        pmid_match = re.search(r"<PMID[^>]*>(\d+)</PMID>", block)
        title_match = re.search(r"<ArticleTitle[^>]*>(.*?)</ArticleTitle>", block, re.DOTALL)
        abstract_match = re.search(r"<AbstractText[^>]*>(.*?)</AbstractText>", block, re.DOTALL)

        pmid = pmid_match.group(1) if pmid_match else ""
        title = _strip_tags(title_match.group(1)) if title_match else ""
        abstract = _strip_tags(abstract_match.group(1)) if abstract_match else ""

        results.append({
            "pmid": pmid,
            "title": title,
            "abstract": abstract,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
        })
    return results


def _strip_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def search_web(term: str) -> list[dict]:
    """Fallback: ricerca web generica tramite Tavily CLI se disponibile, altrimenti vuoto."""
    import shutil
    import subprocess

    tvly = shutil.which("tvly")
    if not tvly:
        print("  TVLY CLI non disponibile, salto ricerca web.", file=sys.stderr)
        return []

    try:
        result = subprocess.run(
            [tvly, "search", term, "--client-name", "opencode", "--max-results", "3"],
            capture_output=True, text=True, timeout=NETWORK_TIMEOUT,
        )
        if result.returncode != 0:
            print(f"  TVLY search fallito: {result.stderr[:200]}", file=sys.stderr)
            return []
    except (subprocess.TimeoutExpired, OSError) as exc:
        print(f"  TVLY: errore esecuzione ({exc}).", file=sys.stderr)
        return []

    # Parsing output TVLY: ogni risultato è separato da righe vuote
    results = []
    blocks = result.stdout.strip().split("\n\n")
    for block in blocks:
        lines = block.splitlines()
        title = lines[0] if lines else ""
        url = ""
        snippet = ""
        for line in lines[1:]:
            if line.startswith("URL:"):
                url = line[4:].strip()
            elif line.startswith("Content:"):
                snippet = line[8:].strip()
        results.append({
            "pmid": "",
            "title": title,
            "abstract": snippet,
            "url": url,
        })
    return results


# ---------------------------------------------------------------------------
# Estrazione pKa
# ---------------------------------------------------------------------------

def extract_pka(text: str) -> float | None:
    """Estrae il primo valore pKa plausibile da un testo.

    Cerca pattern come "pKa 6.5", "pKa=6.5", "pKa of 6.5", "6.5 pKa".
    Accetta valori tra 0 e 14.
    """
    patterns = [
        r"pKa\s*[=:]\s*(\d+(?:\.\d+)?)",
        r"pKa\s+of\s+(\d+(?:\.\d+)?)",
        r"(\d+(?:\.\d+)?)\s+pKa",
        r"pKa\s+(\d+(?:\.\d+)?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                val = float(match.group(1))
                if 0.0 <= val <= 14.0:
                    return val
            except ValueError:
                continue
    return None


def map_protonation(resname: str, pka: float, ph: float = PHYSIOLOGICAL_PH) -> str:
    """Mappa pKa a stato di protonazione a pH fisiologico.

    Returns: "protoneato" / "deprotonato" / "incerto" / "nessun_cambio"
    """
    info = PROTONATION_STATES.get(resname)
    if not info:
        return "incerto"

    if ph < pka:
        return info["protonated"]
    else:
        return info["deprotonated"]


# ---------------------------------------------------------------------------
# Analisi per residuo
# ---------------------------------------------------------------------------

def analyze_residue(
    residue: dict,
    email: str | None = None,
) -> dict:
    """Analizza un singolo residuo flagged e restituisce il risultato QC."""
    resname = residue["resname"]
    resno = residue["resno"]
    region = residue["region"]

    position_label = POSITION_LABELS.get(region, region)
    query = f"{resname} pKa shift membrane {position_label}"

    print(f"  Cerca: {query}…")

    results = search_pubmed(query, email=email)
    if not results:
        results = search_web(query)

    best = None
    for r in results:
        pka = extract_pka(r.get("abstract", "") or r.get("title", ""))
        if pka is not None:
            best = {"source": r, "pka": pka}
            break

    outcome = {
        "resno": resno,
        "resname": resname,
        "region": region,
        "position_label": position_label,
        "pka_found": best["pka"] if best else None,
        "pka_source": best["source"] if best else None,
        "protonation_state": None,
        "confidence": "nessuna",
        "ambiguous": True,
        "note": "",
    }

    if best:
        pka = best["pka"]
        state = map_protonation(resname, pka)
        outcome["protonation_state"] = state
        outcome["note"] = (
            f"pKa {pka} trovato in {best['source']['title'][:80]}… "
            f"({best['source']['url']})"
        )
        if abs(pka - PHYSIOLOGICAL_PH) < 1.0:
            outcome["confidence"] = "media"
            outcome["ambiguous"] = True
            outcome["note"] += " — pKa vicino a pH fisiologico, incerto."
        else:
            outcome["confidence"] = "alta"
            outcome["ambiguous"] = False
    else:
        outcome["note"] = (
            "Nessun dato pKa trovato in letteratura per questo residuo "
            f"in {position_label}. Revisione manuale richiesta."
        )

    return outcome


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Modulo 2 — QC agentico protonazione context-aware.",
    )
    parser.add_argument(
        "--input", type=str, required=True,
        help="File JSON di output del Modulo 1 (orientation.json).",
    )
    parser.add_argument(
        "--output", type=str, default="results",
        help="Directory base di output (default: results/).",
    )
    parser.add_argument(
        "--email", type=str, default=None,
        help="Email per NCBI E-utilities (opzionale, consigliata).",
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
    residues = data.get("residues", [])
    flagged = [r for r in residues if r.get("flagged_pka_shift")]

    if not flagged:
        print("Nessun residuo flagged pKa shift nel file di input.")
        return 0

    print(f"Analisi QC su {len(flagged)} residuo/i flagged…\n")

    outcomes = []
    for res in flagged:
        outcome = analyze_residue(res, email=args.email)
        outcomes.append(outcome)
        state = outcome["protonation_state"] or "non determinato"
        conf = outcome["confidence"]
        print(
            f"  Residuo {res['resno']} {res['resname']} "
            f"({res['region']}): {state} [{conf}]"
        )

    # Scrive output JSON
    outdir = Path(args.output) / f"{date.today().isoformat()}_protonation_qc_{input_path.stem}"
    outdir.mkdir(parents=True, exist_ok=True)

    report = {
        "input_file": str(input_path),
        "n_residues_analyzed": len(outcomes),
        "outcomes": outcomes,
    }
    out_file = outdir / "protonation_qc.json"
    out_file.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    print(f"\nOutput: {out_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
