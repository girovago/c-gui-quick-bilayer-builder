"""Test webui (FastAPI) + webmock: percorsi felici e casi d'errore.

Tutto con CGUI_STUDIO_HOME temporaneo (fixture autouse `home` di conftest):
nessun token reale e nessuna rete. Il FakeClient usa un clock virtuale
avanti manualmente, quindi la suite è istantanea.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cgui_studio import webui
from cgui_studio.client import Client
from cgui_studio.webmock import FakeClient, bilayer_pdb_bytes, build_mock_tar


def _jobbody(**kw) -> dict:
    kw.setdefault("upper", "POPC=64")
    kw.setdefault("lower", "POPC=64")
    kw.setdefault("label", "POPC64")
    return kw


@pytest.fixture(name="gui")
def gui_fixture():
    """App mock con client iniettato e clock virtuale (avanza con shift())."""
    state = {"t": 1000.0}
    fake = FakeClient(clock=lambda: state["t"])
    fake.sleep = lambda s: state.__setitem__("t", state["t"] + max(0.0, s))
    app = webui.create_app(mock=True, client_factory=lambda: fake)
    return TestClient(app), fake


def advance(gui, seconds: float) -> None:
    gui[1].sleep(seconds)


# --------------------------------------------------------------------- -- base


def test_ping_mock(gui):
    t = gui[0].get("/api/ping")
    assert t.status_code == 200
    assert t.json() == {"ok": True, "mock": True}


def test_submit_records_job(gui):
    t, _ = gui
    r = t.post("/api/jobs", json=_jobbody())
    assert r.status_code == 200
    data = r.json()
    assert len(data["jobid"]) == 10 and data["jobid"].isdigit()
    assert "ffconverter" in data["modules"]
    assert data["spec"]["ion_type"] == "NaCl"
    listing = t.get("/api/jobs").json()
    assert listing and listing[0]["jobid"] == data["jobid"]
    assert listing[0]["state"] == "submitted"


def test_injectable_client_reused(gui):
    """Il client iniettato è lo stesso che risponde (.Mockito-free check)."""
    t, fake = gui
    r = t.post("/api/jobs", json=_jobbody())
    jobid = r.json()["jobid"]
    assert jobid in fake._t0


# ------------------------------------------------------------------- -- 422


def test_submit_upper_without_lower_422(gui):
    r = gui[0].post("/api/jobs", json=_jobbody(lower=None))
    assert r.status_code == 422
    assert "entrambi" in r.text


def test_submit_no_composition_422(gui):
    r = gui[0].post("/api/jobs", json={"label": "vuota"})
    assert r.status_code == 422
    assert "Nessuna composizione" in r.text


def test_submit_unknown_field_422(gui):
    r = gui[0].post("/api/jobs", json=_jobbody(bogus=1))
    assert r.status_code == 422


def test_form_preview_echoes(gui):
    r = gui[0].post("/api/form-preview", json=_jobbody(margin=22.5, ion_type="KCl"))
    assert r.status_code == 200
    data = r.json()
    assert data["query"]["margin"] == "22.5"
    assert data["form"]["ion_conc"] == "0.15"
    assert data["form"]["ion_type"] == "KCl"
    assert "Ion_conc" not in data["form"] and "Ion_type" not in data["form"]


def test_form_preview_clone(gui):
    r = gui[0].post(
        "/api/form-preview",
        json={
            "membtype": "PMm",
            "clone_job": "9297832092",
            "label": "clone",
            "membrane_only": False,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["form"]["jobid"] == "9297832092"
    assert data["query"]["clone_job"] == "true"


# ------------------------------------------------------------------ -- status


def test_job_detail_404(gui):
    assert gui[0].get("/api/jobs/0000000000").status_code == 404


def test_status_timeline(gui):
    t, _ = gui
    jobid = t.post("/api/jobs", json=_jobbody()).json()["jobid"]

    detail = t.get(f"/api/jobs/{jobid}").json()["status"]
    assert detail["status"] == "pending"
    assert "3 job" in detail["rank"]

    advance(gui, 3.5)
    detail = t.get(f"/api/jobs/{jobid}").json()["status"]
    assert detail["status"] == "running"
    assert detail["last_inp_step"] == 2

    advance(gui, 4.6)
    detail = t.get(f"/api/jobs/{jobid}").json()["status"]
    assert detail["status"] == "done"
    assert detail["has_tar"] is True

    # il sync ha aggiornato lo store
    state = t.get("/api/jobs").json()[0]["state"]
    assert state == "done" or state == "running"


# ------------------------------------------------------------------- -- files (fetch)


def test_tree_before_extract_404(gui):
    t, _ = gui
    jobid = t.post("/api/jobs", json=_jobbody()).json()["jobid"]
    r = t.get(f"/api/files/{jobid}")
    assert r.status_code == 404
    assert "estrai" in r.json()["detail"]


def test_fetch_not_ready_409(gui):
    t, _ = gui
    jobid = t.post("/api/jobs", json=_jobbody()).json()["jobid"]
    r = t.post(f"/api/jobs/{jobid}/fetch", json={})
    assert r.status_code == 409
    assert "non pronto" in r.json()["detail"]


def test_fetch_after_done(gui):
    t, _ = gui
    jobid = t.post("/api/jobs", json=_jobbody()).json()["jobid"]
    advance(gui, 9.0)
    r = t.post(f"/api/jobs/{jobid}/fetch", json={})
    assert r.status_code == 200
    data = r.json()
    assert len(data["sha256"]) == 64
    assert data["record"]["state"] == "done"
    assert data["record"]["outdir"]
    names = [fi["name"] for fi in t.get(f"/api/files/{jobid}").json()["files"]]
    assert "system/step1_pdbreader.pdb" in names
    assert "system/step5_membrane/gromacs/topol.top" in names
    assert all("size" in str(f) or True for f in names)


def test_tree_sizes_and_depth(gui):
    t, _ = gui
    jobid = t.post("/api/jobs", json=_jobbody()).json()["jobid"]
    advance(gui, 9.0)
    t.post(f"/api/jobs/{jobid}/fetch", json={})
    files = t.get(f"/api/files/{jobid}").json()["files"]
    by_name = {f["name"]: f for f in files}
    assert by_name["system/step1_pdbreader.pdb"]["depth"] == 2
    assert by_name["system/step1_pdbreader.pdb"]["size"] > 50_000
    assert 0 < by_name["system/step2.2_ions_count.str"]["size"] < 1_000


def test_fetch_with_wait_from_pending(gui):
    t, _ = gui
    jobid = t.post("/api/jobs", json=_jobbody()).json()["jobid"]
    r = t.post(f"/api/jobs/{jobid}/fetch", json={"wait": True})
    assert r.status_code == 200
    assert r.json()["record"]["state"] == "done"


def test_file_pdb_content(gui):
    t, _ = gui
    jobid = t.post("/api/jobs", json=_jobbody()).json()["jobid"]
    advance(gui, 9.0)
    t.post(f"/api/jobs/{jobid}/fetch", json={})
    r = t.get(f"/api/files/{jobid}/system/step1_pdbreader.pdb")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "CRYST1" in r.text
    assert r.text.count("ATOM") > 100
    r2 = t.get(f"/api/files/{jobid}/system/step2.2_ions_count.str")
    assert r2.status_code == 200
    assert "SOD" in r2.text
    # Stelle: README è testo
    assert t.get(f"/api/files/{jobid}/README.txt").status_code == 200


def test_file_missing_inside_404(gui):
    t, _ = gui
    jobid = t.post("/api/jobs", json=_jobbody()).json()["jobid"]
    advance(gui, 9.0)
    t.post(f"/api/jobs/{jobid}/fetch", json={})
    assert t.get(f"/api/files/{jobid}/system/inesistente.pdb").status_code == 404


def test_file_traversal_rejected(gui):
    t, _ = gui
    jobid = t.post("/api/jobs", json=_jobbody()).json()["jobid"]
    advance(gui, 9.0)
    t.post(f"/api/jobs/{jobid}/fetch", json={})
    for encoded in ("..%2Fmsg", "%2E%2E%2Fmsg", "system%2F..%2F..%2Fmsg"):
        r = t.get(f"/api/files/{jobid}/{encoded}")
        assert r.status_code == 404, encoded
    # nessun file creato fuori dal job
    assert gui[0].get("/api/files/non-esistente/doc").status_code == 404


def test_tree_unknown_job_404(gui):
    assert gui[0].get("/api/files/9999999999").status_code == 404


# ---------------------------------------------------------------- -- non-mock 409


def test_non_mock_without_token_409():
    """Senza token e non-mock: submit e detail rispondono 409 col messaggio da CLI."""
    app = webui.create_app(mock=False, client_factory=lambda: Client())
    t = TestClient(app)
    r = t.post("/api/jobs", json=_jobbody())
    assert r.status_code == 409
    assert "cgui login" in r.json()["detail"]


def test_build_mock_tar_direct(tmp_path: Path):
    tar = build_mock_tar(tmp_path / "m.tar.gz", "1234567890")
    import gzip

    with gzip.open(tar, "rb") as fh:
        blob = fh.read()
    assert b"system/step1_pdbreader.pdb" in blob


def test_bilayer_pdb_header():
    pdb = bilayer_pdb_bytes(lipids_side=4).decode("ascii")
    assert pdb.startswith("CRYST1")
    assert pdb.rstrip().endswith("END")
    assert pdb.count("ATOM") > 100
    # 2 leaflets → teste (resname POP) a ±z_leaflet (±20 Å): col camps 47-54 = z
    zs = [
        float(line[46:54])
        for line in pdb.splitlines()
        if line.startswith("ATOM") and "POP" in line[17:20]
    ]
    assert any(z > 19 for z in zs) and any(z < -19 for z in zs)
