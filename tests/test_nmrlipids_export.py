"""Test per scripts/nmrlipids_export.py — Modulo 3 export NMRlipids Databank."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from nmrlipids_export import (  # noqa: E402
    build_composition_yaml,
    parse_readme_yaml,
)

# ---------------------------------------------------------------------------
# build_composition_yaml
# ---------------------------------------------------------------------------


def test_build_composition_yaml_single_lipid():
    upper = {"POPC": 64}
    lower = {"POPC": 64}
    yaml = build_composition_yaml(upper, lower)
    assert "POPC:" in yaml
    assert "upper: 64" in yaml
    assert "lower: 64" in yaml
    assert "total: 128" in yaml


def test_build_composition_yaml_multi_lipid():
    upper = {"POPC": 32, "CHOL": 16}
    lower = {"POPC": 32, "CHOL": 16}
    yaml = build_composition_yaml(upper, lower)
    assert "POPC:" in yaml
    assert "CHOL:" in yaml
    assert "total: 64" in yaml  # POPC total
    assert "total: 32" in yaml  # CHOL total


def test_build_composition_yaml_asymmetric():
    upper = {"POPC": 40, "CHOL": 20}
    lower = {"POPC": 20, "CHOL": 10}
    yaml = build_composition_yaml(upper, lower)
    assert "upper: 40" in yaml
    assert "lower: 20" in yaml
    assert "total: 60" in yaml  # POPC total


def test_build_composition_yaml_empty():
    yaml = build_composition_yaml({}, {})
    assert yaml == ""


# ---------------------------------------------------------------------------
# parse_readme_yaml
# ---------------------------------------------------------------------------


def test_parse_readme_yaml_apl_thickness():
    raw = """\
title: "Test bilayer"
area_per_lipid_nm2: 0.683
thickness_pp_nm: 4.42
"""
    data = parse_readme_yaml(raw)
    assert data["apl"] == 0.683
    assert data["thickness"] == 4.42


def test_parse_readme_yaml_empty():
    data = parse_readme_yaml("")
    assert data == {}


def test_parse_readme_yaml_missing_fields():
    raw = "title: \"Solo titolo\"\n"
    data = parse_readme_yaml(raw)
    assert data == {}
