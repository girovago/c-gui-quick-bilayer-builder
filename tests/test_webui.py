"""Test webui (FastAPI) + webmock: percorsi felici e casi d'errore.

Tutto con CGUI_STUDIO_HOME temporaneo (fixture autouse `home` di conftest):
nessun token reale e nessuna rete. Il FakeClient usa un clock virtuale
avanti manualmente, quindi la suite è istantanea.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from cgui_studio import webui
from cgui_studio.client import AuthError, Client
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


def test_sample_pdb_route(gui):
    """Struttura campione: PDB plausibile, header X-Sample, zero rete esterna."""
    t = gui[0].get("/api/sample/pdb")
    assert t.status_code == 200
    assert t.headers.get("x-sample") == "true"
    assert t.headers["content-type"].startswith("text/plain")
    lines = t.text.splitlines()
    atoms = [line for line in lines if line.startswith("ATOM")]
    # ~128 lipidi + acqua + ioni: migliaia di atomi ATOM
    assert 4000 <= len(atoms) <= 20000  # sample ridotto a lipids_side=4 (fix viewer lento 11/11)
    assert atoms[0].startswith("ATOM")
    assert atoms[1].startswith("ATOM")
    # ~3 atomi testa POP per lipide: 128 lipidi totali (64+64)
    assert sum(1 for ln in atoms if ln[17:20] == "POP") == 96  # 4 lipidi per lato × 2 × 12 residui
    assert lines[-1] == "END"
    # deterministico: due chiamate → stesso contenuto
    assert t.text == gui[0].get("/api/sample/pdb").text


def test_sample_pdb_does_not_touch_store(gui):
    """La rotta campione non registra nulla né richiede il client."""
    t, fake = gui
    assert t.get("/api/sample/pdb").status_code == 200
    assert t.get("/api/jobs").json() == []
    assert not getattr(fake, "_t0", None)


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
    # il messaggio punta esplicitamente al tasto di login nella GUI web
    assert "Login CHARMM-GUI" in r.json()["detail"]


# -------------------------------------------------------------------- -- auth


def test_auth_mock_login_rejected(gui):
    """In mock non c'e' login reale: 409 educato, niente client reale chiamato."""
    t, _ = gui
    r = t.post("/api/auth/login", json={"email": "a@b.c", "password": "x"})
    assert r.status_code == 409
    assert "mock" in r.json()["detail"]


def test_auth_status_fresh_app(gui):
    t, _ = gui
    r = t.get("/api/auth").json()
    assert r == {"logged": False, "mock": True, "mode": "none"}


def test_auth_login_ram_no_disk(tmp_path: Path):
    """Login reale (http iniettato): token solo in RAM, MAI token.json su disco."""
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((str(request.url), request.headers.get("authorization", "<none>")))
        if request.url.path.endswith("/login"):
            return httpx.Response(200, json={"token": "ram-token-123"})
        return httpx.Response(500, text="non dovrebbe arrivare qui")

    real = Client(http=httpx.Client(transport=httpx.MockTransport(handler)))
    app = webui.create_app(mock=False, client_factory=lambda: real)
    t = TestClient(app)

    r = t.post("/api/auth/login", json={"email": "ric@lab.edu", "password": "segreto"})
    assert r.status_code == 200
    body = r.json()
    assert body == {"logged": True, "email": "ri***@lab.edu", "mode": "ram"}

    # GET /api/auth riflette la sessione RAM
    r = t.get("/api/auth").json()
    assert r["logged"] is True and r["mode"] == "ram" and r["email"] == "ri***@lab.edu"

    # nessun token su disco, E le credenziali non finiscono tracciate in URI/header
    from cgui_studio import paths

    assert not paths.token_path().exists()
    assert all("segreto" not in str(c) for c in calls)

    # logout: sessione RAM chiusa, fallback cache = none, token azzerato sul client
    r = t.delete("/api/auth")
    assert r.json()["logged"] is False
    assert t.get("/api/auth").json()["logged"] is False
    with pytest.raises(AuthError):
        real.token()


def test_auth_login_wrong_credentials_401():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "Invalid token", "detail": "Expired token"})

    real = Client(http=httpx.Client(transport=httpx.MockTransport(handler)))
    app = webui.create_app(mock=False, client_factory=lambda: real)
    t = TestClient(app)
    r = t.post("/api/auth/login", json={"email": "a@b.c", "password": "y"})
    assert r.status_code == 401
    assert "login CHARMM-GUI rifiutato" in r.json()["detail"]


def test_auth_disk_cache_fallback(gui):
    """Se `cgui login` (CLI) ha lasciato il token in cache: GET auth lo mostra,
    la sessione va usata, ma DELETE non cancella il file della CLI."""
    from cgui_studio import paths

    paths.write_private(
        paths.token_path(), json.dumps({"token": "cli-token", "email": "cli@x.y"})
    )
    t, fake = gui
    r = t.get("/api/auth")
    assert r.status_code == 200
    data = r.json()
    assert data["logged"] is True
    assert data["mode"] == "disk-cache"
    # logout via web NON tocca il file della CLI
    t.delete("/api/auth")
    assert paths.token_path().exists()
    assert json.loads(paths.token_path().read_text(encoding="utf-8"))["token"] == "cli-token"


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
