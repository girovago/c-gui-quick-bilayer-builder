"""Test per scripts/protonation_qc.py — Modulo 2 QC protonazione."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from protonation_qc import (  # noqa: E402
    extract_pka,
    map_protonation,
)

# ---------------------------------------------------------------------------
# extract_pka
# ---------------------------------------------------------------------------


def test_extract_pka_explicit():
    assert extract_pka("pKa = 6.5") == 6.5
    assert extract_pka("pKa: 6.5") == 6.5
    assert extract_pka("pKa of 6.5") == 6.5
    assert extract_pka("6.5 pKa") == 6.5


def test_extract_pka_out_of_range():
    assert extract_pka("pKa = 15.0") is None
    assert extract_pka("pKa = -1.0") is None


def test_extract_pka_none():
    assert extract_pka("nessun valore qui") is None
    assert extract_pka("") is None


# ---------------------------------------------------------------------------
# map_protonation
# ---------------------------------------------------------------------------


def test_map_protonation_asp_protonated():
    # pH 7.4 < pKa 10 → protoneato (0)
    assert map_protonation("ASP", 10.0) == "0"


def test_map_protonation_asp_deprotonated():
    # pH 7.4 >= pKa 3.0 → deprotonato (-)
    assert map_protonation("ASP", 3.0) == "-"


def test_map_protonation_histidine_protonated():
    assert map_protonation("HIS", 8.0) == "+"


def test_map_protonation_histidine_deprotonated():
    assert map_protonation("HIS", 5.0) == "0"


def test_map_protonation_unknown():
    assert map_protonation("GLY", 5.0) == "incerto"
