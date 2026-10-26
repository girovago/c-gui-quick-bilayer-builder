"""Test CLI (typer CliRunner): build end-to-end, login, ls, doctor, gestione errori."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qsl

import httpx
from typer.testing import CliRunner

import cgui_studio
import cgui_studio.cli as cli_mod
from cgui_studio import paths as paths_mod
from cgui_studio.cli import app
from cgui_studio.store import JobStore
from tests.conftest import MOCK_BASE
from tests.test_tar_store import write_tar

runner = CliRunner()


def _json(data, status=200):
    return httpx.Response(
        status, headers={"content-type": "application/json"}, content=json.dumps(data)
    )


def make_tar(path: Path):
    write_tar(path, {"README": b"hello"})


def test_ls_empty(home):
    result = runner.invoke(app, ["ls"])
    assert result.exit_code == 0
    assert "Archivio vuoto" in result.output


def test_ls_table(home):
    spec_json = '{"upper": "POPC=64", "lower": "POPC=64"}'
    with JobStore() as store:
        store.add("100", json.loads(spec_json), label="POPC")
        store.update("100", state="done")
    result = runner.invoke(app, ["ls"])
    assert result.exit_code == 0
    assert "100" in result.output and "done" in result.output and "POPC" in result.output


def test_build_end_to_end(home, sleep_noop, tmp_path):
    """build --wait: submit → jobid → store → download → manifest (zero rete vera)."""
    tar_file = tmp_path / "payload.tar.gz"
    make_tar(tar_file)
    payload = tar_file.read_bytes()
    seen = {}

    def handler(req):
        path = req.url.path
        if path.endswith("/quick_bilayer"):
            seen["form"] = dict(parse_qsl(req.content.decode()))
            return _json({"jobid": "9285768142", "modules": "membuilder", "submitted": True})
        if path.endswith("/check_status"):
            return _json(
                {"jobid": "9285768142", "status": "done", "hasTarFile": True, "lastInpStep": 5}
            )
        if path.endswith("/download"):
            return httpx.Response(200, content=payload)
        raise AssertionError(path)

    fake = httpx.Client(transport=httpx.MockTransport(handler), base_url=MOCK_BASE)

    class FakeClient(cli_mod.Client):
        def __init__(self, *args, **kw):
            kw.setdefault("http", fake)
            super().__init__(*args, **kw)

    original = cli_mod.Client
    paths_mod.save_token("tok", "e@x")
    cli_mod.Client = FakeClient

    spec_file = tmp_path / "spec.toml"
    spec_file.write_text('[build]\nlabel = "POPC64"\nupper = "POPC=64"\nlower = "POPC=64"\n')
    outdir = home / "res"
    try:
        result = runner.invoke(app, ["build", str(spec_file), "--wait", "--outdir", str(outdir)])
    finally:
        cli_mod.Client = original

    assert result.exit_code == 0, result.output
    assert "9285768142" in result.output
    assert seen["form"]["upper"] == "POPC=64" and seen["form"]["lower"] == "POPC=64"

    with JobStore() as store:
        rec = store.get_by_jobid("9285768142")
    assert rec.state == "done" and rec.archive_sha256 and rec.label == "POPC64"

    out = Path(rec.outdir) / "manifest.json"
    manifest = json.loads(out.read_text())
    assert manifest["jobid"] == "9285768142"
    assert manifest["spec"]["label"] == "POPC64"
    assert out.parent.name.startswith("2026-") or out.parent.name.startswith("2025-")
    assert (out.parent / "system" / "README").read_bytes() == b"hello"


def test_login_with_env_password(home, monkeypatch):
    monkeypatch.setenv("CGUI_PASS", "secret")
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return _json({"token": "jwt-abc"})

    fake = httpx.Client(transport=httpx.MockTransport(handler), base_url=MOCK_BASE)

    class FakeClient(cli_mod.Client):
        def __init__(self, *args, **kw):
            kw.setdefault("http", fake)
            super().__init__(*args, **kw)

    original = cli_mod.Client
    cli_mod.Client = FakeClient
    try:
        result = runner.invoke(app, ["login", "giorgi.veneruso@studenti.unina.it"])
    finally:
        cli_mod.Client = original

    assert result.exit_code == 0, result.output
    assert "token salvato" in result.output
    assert seen["body"]["password"] == "secret"
    token_file = home / "cache" / "token.json"
    assert json.loads(token_file.read_text())["token"] == "jwt-abc"
    assert (token_file.stat().st_mode & 0o777) == 0o600


def test_build_spec_error_no_stack_trace(home, tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text("[build]\nmargn = 20.0\n")
    result = runner.invoke(app, ["build", str(bad)])
    assert result.exit_code == 1
    assert "margn" in result.output
    assert "Traceback" not in result.output

    debug = runner.invoke(app, ["--debug", "build", str(bad)])
    assert debug.exit_code == 1
    # in --debug l'eccezione non viene intercettata (CliRunner la conserva)
    assert debug.exception is not None
    assert "margn" in str(debug.exception)


def test_status_requires_token_without_cache(home):
    result = runner.invoke(app, ["status", "123"])
    assert result.exit_code == 1
    assert "login" in result.output


def test_doctor_reports_missing_token(home):
    """Doctor con ping mockato: dir ok, token mancante spiegato, exit 1."""
    with_doctor_ping(401)
    try:
        result = runner.invoke(app, ["doctor"])
    finally:
        restore_ping()
    assert result.exit_code == 1
    out = result.output
    assert "Python" in out
    assert "token" in out and "login" in out
    assert "HTTP 401" in out
    assert (home / "cache").is_dir() and (home / "data").is_dir()


def test_doctor_all_ok(home, sleep_noop):
    paths_mod.save_token("tok", "e@x")
    with_doctor_ping(401)
    try:
        result = runner.invoke(app, ["doctor"])
    finally:
        restore_ping()
    assert result.exit_code == 0
    assert "ambiente ok" in result.output


def test_doctor_network_down(home):
    with_doctor_ping(None)
    try:
        result = runner.invoke(app, ["doctor"])
    finally:
        restore_ping()
    assert result.exit_code == 1
    assert "raggiungibile" in result.output


_pings = []


def with_doctor_ping(code):
    """Sostituisce Client.ping per evitare rete nei test doctor."""
    _pings.append(cli_mod.Client.ping)
    cli_mod.Client.ping = staticmethod(lambda *a, **kw: code)


def restore_ping():
    if _pings:
        cli_mod.Client.ping = _pings.pop()


def test_package_metadata():
    assert cgui_studio.__version__
    from cgui_studio.models import TERMINAL_STATUS

    assert TERMINAL_STATUS == frozenset({"done", "error"})


def test_wait_fetches_existing_job(home, sleep_noop, tmp_path):
    """cgui wait JOBID: job già in archivio → attesa finta + download + manifest."""
    tar_file = tmp_path / "payload.tar.gz"
    write_tar(tar_file, {"README": b"from-wait"})
    payload = tar_file.read_bytes()

    def handler(req):
        path = req.url.path
        if path.endswith("/check_status"):
            return _json({"jobid": "777", "status": "done", "hasTarFile": True, "lastInpStep": 9})
        if path.endswith("/download"):
            return httpx.Response(200, content=payload)
        raise AssertionError(path)

    fake = httpx.Client(transport=httpx.MockTransport(handler), base_url=MOCK_BASE)

    class FakeClient(cli_mod.Client):
        def __init__(self, *args, **kw):
            kw.setdefault("http", fake)
            super().__init__(*args, **kw)

    original = cli_mod.Client
    paths_mod.save_token("tok", "e@x")
    with JobStore() as store:
        store.add("777", '{"upper": "POPC=64", "lower": "POPC=64"}', label="POPC64")
    cli_mod.Client = FakeClient
    outdir = home / "res"
    try:
        result = runner.invoke(app, ["wait", "777", "--outdir", str(outdir)])
    finally:
        cli_mod.Client = original

    assert result.exit_code == 0, result.output
    with JobStore() as store:
        rec = store.get_by_jobid("777")
    assert rec.state == "done" and rec.archive_sha256
    manifest = json.loads((Path(rec.outdir) / "manifest.json").read_text())
    assert manifest["jobid"] == "777"
    assert manifest["spec"]["upper"] == "POPC=64"
    assert (Path(rec.outdir) / "system" / "README").read_bytes() == b"from-wait"


def test_wait_job_not_in_store_uses_defaults(home, sleep_noop, tmp_path):
    """cgui wait senza record: label generica 'job', nessun crash."""
    tar_file = tmp_path / "payload.tar.gz"
    write_tar(tar_file, {"README": b"x"})
    payload = tar_file.read_bytes()

    def handler(req):
        if req.url.path.endswith("/check_status"):
            return _json({"jobid": "888", "status": "done", "hasTarFile": True})
        return httpx.Response(200, content=payload)

    fake = httpx.Client(transport=httpx.MockTransport(handler), base_url=MOCK_BASE)

    class FakeClient(cli_mod.Client):
        def __init__(self, *args, **kw):
            kw.setdefault("http", fake)
            super().__init__(*args, **kw)

    original = cli_mod.Client
    paths_mod.save_token("tok", "e@x")
    cli_mod.Client = FakeClient
    try:
        result = runner.invoke(app, ["wait", "888", "--outdir", str(home / "res")])
    finally:
        cli_mod.Client = original
    assert result.exit_code == 0, result.output
    assert any(folder.name.endswith("_888") for folder in (home / "res").iterdir())
