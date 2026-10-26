"""Test di load_spec_file: singolo, multiplo, errori con suggerimenti."""

from __future__ import annotations

import pytest

from cgui_studio.spec import SpecFileError, load_spec_file
from tests.conftest import leaflet_spec


def write_toml(tmp_path, name: str, text: str):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_single_build_block(home, tmp_path):
    p = write_toml(
        tmp_path,
        "one.toml",
        """
[build]
label = "POPC64"
upper = "POPC=64"
lower = "POPC=64"
margin = 25.0
""",
    )
    specs = load_spec_file(p)
    assert len(specs) == 1
    assert specs[0].label == "POPC64"
    assert specs[0].margin == 25.0
    assert specs[0].to_form()["lower"] == "POPC=64"


def test_multiple_build_blocks(home, tmp_path):
    p = write_toml(
        tmp_path,
        "many.toml",
        """
[[build]]
label = "A"
upper = "POPC=64"
lower = "POPC=64"

[[build]]
label = "B"
membtype = "PMm"
""",
    )
    specs = load_spec_file(p)
    assert [s.label for s in specs] == ["A", "B"]
    assert specs[1].membtype == "PMm"


def test_unknown_key_with_suggestion(home, tmp_path):
    p = write_toml(
        tmp_path,
        "typo.toml",
        """
[build]
upper = "POPC=64"
lower = "POPC=64"
margn = 20.0
""",
    )
    with pytest.raises(SpecFileError, match="margn") as exc:
        load_spec_file(p)
    assert "margin" in str(exc.value)  # suggerimento via difflib


def test_unknown_top_level_key(home, tmp_path):
    p = write_toml(
        tmp_path,
        "bad.toml",
        """
[altro]
x = 1
""",
    )
    with pytest.raises(SpecFileError, match="sezion"):
        load_spec_file(p)


def test_validation_error_reported_per_block(home, tmp_path):
    p = write_toml(
        tmp_path,
        "inv.toml",
        """
[[build]]
label = "ok"
upper = "POPC=64"
lower = "POPC=64"

[[build]]
label = "rotto"
upper = "POPC=64"
lower = "POPC=64"
margin = 0
""",
    )
    with pytest.raises(SpecFileError, match="build\\[1\\]") as exc:
        load_spec_file(p)
    assert "margin" in str(exc.value)


def test_missing_file(home, tmp_path):
    with pytest.raises(SpecFileError, match="non trovato"):
        load_spec_file(tmp_path / "nope.toml")


def test_invalid_toml(home, tmp_path):
    p = write_toml(tmp_path, "broken.toml", "[build\nbroken = ")
    with pytest.raises(SpecFileError, match="TOML"):
        load_spec_file(p)


def test_spec_model_roundtrip_matches_direct(home):
    sp = load_spec_file(write_toml(home, "s.toml", '[build]\nupper="POPC=32"\nlower="POPC=32"\n'))[
        0
    ]
    assert sp == leaflet_spec(upper="POPC=32", lower="POPC=32", label=None)
