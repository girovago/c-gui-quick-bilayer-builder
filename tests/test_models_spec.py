"""Test di Spec: validazioni e separazione query/form."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cgui_studio.models import Spec, format_number
from tests.conftest import leaflet_spec


def test_upper_lower_valid_and_form_has_them():
    sp = leaflet_spec(margin=20.0, ion_conc=0.15)
    form = sp.to_form()
    assert form["upper"] == "POPC=64"
    assert form["lower"] == "POPC=64"
    # minuscolo: i maiuscoli della doc ufficiale sono IGNORATI dal server
    # (bug verificato su early-step2.2_ions_count.str di un job reale)
    assert form["ion_conc"] == "0.15"
    assert form["ion_type"] == "NaCl"
    assert "Ion_conc" not in form and "Ion_type" not in form


def test_membtype_only_valid():
    sp = Spec(membtype="PMm")
    assert sp.to_form()["membtype"] == "PMm"
    assert "upper" not in sp.to_form()
    assert "lower" not in sp.to_form()


def test_both_modes_rejected():
    with pytest.raises(ValidationError, match="UNA sola modalità"):
        Spec(upper="POPC=64", lower="POPC=64", membtype="PMm")


def test_upper_without_lower_rejected():
    with pytest.raises(ValidationError, match="entrambi"):
        Spec(upper="POPC=64")


def test_lower_without_upper_rejected():
    with pytest.raises(ValidationError, match="entrambi"):
        Spec(lower="POPC=64")


def test_no_mode_rejected():
    with pytest.raises(ValidationError, match="Nessuna composizione"):
        Spec(label="vuoto")


def test_margin_must_be_positive():
    with pytest.raises(ValidationError, match="margin"):
        Spec(upper="POPC=64", lower="POPC=64", margin=0)
    with pytest.raises(ValidationError, match="margin"):
        Spec(upper="POPC=64", lower="POPC=64", margin=-5.0)


def test_membrane_only_false_requires_clone():
    with pytest.raises(ValidationError, match="PDB Reader"):
        Spec(upper="POPC=64", lower="POPC=64", membrane_only=False)
    sp = Spec(upper="POPC=64", lower="POPC=64", membrane_only=False, clone_job_from="123")
    assert sp.membrane_only is False


def test_extra_field_forbidden():
    with pytest.raises(ValidationError):
        leaflet_spec(margn=20.0)


def test_query_vs_form_separation():
    """Verifica esplicita della separazione: margin MAI nel form, upper MAI nella query."""
    sp = leaflet_spec(
        wdist=2.5, membrane_only=False, clone_job_from="9285768142", heteroatoms=True, ppm=True
    )
    q = sp.to_query()
    form = sp.to_form()

    assert "margin" in q and "margin" not in form
    assert q["margin"] == "20"
    assert q["membrane_only"] == "false"
    assert q["run_ffconverter"] == "true"
    assert q["wdist"] == "2.5"
    assert q["clone_job"] == "true"

    assert "upper" in form and "upper" not in q
    assert "lower" in form and "lower" not in q
    assert form["jobid"] == "9285768142" and "jobid" not in q
    assert "ion_conc" in form and "ion_conc" not in q
    assert "ion_type" in form and "ion_type" not in q
    # flag non verificati: nel form, solo se True
    assert form["heteroatoms"] == "true" and form["ppm"] == "true"
    assert "prot_projection_upper" not in form and "prot_projection_lower" not in form
    # il default True di topology_in finisce nel form
    assert form["topology_in"] == "true"


def test_flag_defaults_get_false_query():
    sp = leaflet_spec()
    q = sp.to_query()
    assert q == {"margin": "20", "membrane_only": "true", "run_ffconverter": "true"}


def test_format_number():
    assert format_number(20.0) == "20"
    assert format_number(22.5) == "22.5"
    assert format_number(0.15) == "0.15"
    assert format_number(1) == "1"
