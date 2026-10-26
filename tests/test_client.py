"""Test client: login, submit (posizioni query/form), status, retry, wait, fetch."""

from __future__ import annotations

import datetime as _dt
import io
import json
import tarfile
from pathlib import Path
from urllib.parse import parse_qsl

import httpx
import pytest

from cgui_studio.client import ApiError, AuthError, JobError, fetch_job, sha256_file
from tests.conftest import leaflet_spec, mock_client


def _json(data, status=200):
    return httpx.Response(
        status, headers={"content-type": "application/json"}, content=json.dumps(data)
    )


# --------------------------------------------------------------------- login


def test_login_ok_saves_token(home):
    seen = {}

    def handler(req):
        seen["path"] = req.url.path
        seen["body"] = json.loads(req.content)
        return _json({"token": "jwt-123"})

    token = mock_client(handler).login("me@x.org", "pw")
    assert token == "jwt-123"
    assert seen["path"] == "/api/login"
    assert seen["body"] == {"email": "me@x.org", "password": "pw"}

    token_file = home / "cache" / "token.json"
    cached = json.loads(token_file.read_text())
    assert cached["token"] == "jwt-123" and cached["email"] == "me@x.org"
    assert (token_file.stat().st_mode & 0o777) == 0o600


def test_login_404_text_body_raises_autherror():
    def handler(req):
        return httpx.Response(404, text="No user or incorrect password!")

    with pytest.raises(AuthError, match="No user or incorrect password!"):
        mock_client(handler).login("x@y.z", "bad")


def test_login_missing_token_field():
    def handler(req):
        return _json({"ok": True})

    with pytest.raises(ApiError, match="token"):
        mock_client(handler).login("x@y.z", "bad")


# -------------------------------------------------------------------- submit


def test_submit_posts_query_and_form_correctly():
    seen = {}

    def handler(req):
        seen["method"] = req.method
        seen["path"] = req.url.path
        seen["query"] = dict(req.url.params)
        seen["form"] = dict(parse_qsl(req.content.decode()))
        seen["auth"] = req.headers.get("Authorization")
        return _json(
            {
                "jobid": "111",
                "modules": "membuilder ffconverter",
                "submitted": True,
                "use_redis": True,
            }
        )

    sp = leaflet_spec(wdist=2.5, membrane_only=False, clone_job_from="9285768142")
    data = mock_client(handler, token="tkn").submit(sp)

    assert data["jobid"] == "111"
    assert seen["method"] == "POST"
    assert seen["path"] == "/api/quick_bilayer"
    assert seen["auth"] == "Bearer tkn"
    # QUERY: margin/wdist/clone_job — MAI upper/lower/jobid
    assert seen["query"]["margin"] == "20"
    assert seen["query"]["wdist"] == "2.5"
    assert seen["query"]["clone_job"] == "true"
    assert seen["query"]["membrane_only"] == "false"
    assert seen["query"]["run_ffconverter"] == "true"
    assert "upper" not in seen["query"] and "jobid" not in seen["query"]
    # FORM: upper/lower/jobid — MAI margin
    assert seen["form"]["upper"] == "POPC=64"
    assert seen["form"]["lower"] == "POPC=64"
    assert seen["form"]["jobid"] == "9285768142"
    assert "margin" not in seen["form"] and "clone_job" not in seen["form"]


def test_submit_error_flag_raises_api_error():
    def handler(req):
        return _json({"submitted": False, "request": {}})

    with pytest.raises(ApiError, match="rifiutato"):
        mock_client(handler).submit(leaflet_spec())


# -------------------------------------------------------------------- status


PENDING_JSON = {
    "jobid": "9285768142",
    "rank": "Your job is pending, 19 job(s) ahead",
    "status": "pending",
    "rqinfo": "q1",
    "hasTarFile": False,
    "lastInpStep": 3,
}


def test_status_parses_pending():
    def handler(req):
        assert req.url.path == "/api/check_status"
        assert req.url.params["jobid"] == "9285768142"
        assert req.headers["Authorization"] == "Bearer tkn"
        return _json(PENDING_JSON)

    st = mock_client(handler, token="tkn").status("9285768142")
    assert st.status == "pending"
    assert st.rank_position == 19
    assert st.last_inp_step == 3
    assert st.has_tar is False
    assert st.is_terminal is False


def test_status_401_no_retry_and_message():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return _json({"error": "No token provided"}, status=401)

    with pytest.raises(ApiError, match="No token provided") as exc:
        mock_client(handler, token="expired").status("1")
    assert exc.value.status_code == 401
    assert calls["n"] == 1  # mai retry su 401


# --------------------------------------------------------------------- retry


def test_retry_500_then_success():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500, text="boom")
        return _json({"token": "ok"})

    token = mock_client(handler, max_retries=3).login("e@x", "p")
    assert token == "ok"
    assert calls["n"] == 3


def test_no_retry_on_404():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(404, text="No user or incorrect password!")

    with pytest.raises(AuthError):
        mock_client(handler).login("e@x", "p")
    assert calls["n"] == 1


def test_network_error_retried_then_success():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] < 2:
            raise httpx.ConnectError("connection refused")
        return _json({"token": "ok"})

    assert mock_client(handler).login("e@x", "p") == "ok"
    assert calls["n"] == 2


# ---------------------------------------------------------------------- wait


def test_wait_polls_until_done():
    states = iter(
        [
            {"jobid": "9", "status": "pending", "rank": "Your job is pending, 2 job(s) ahead"},
            {"jobid": "9", "status": "running"},
            {"jobid": "9", "status": "done", "hasTarFile": True},
        ]
    )
    updates: list[str] = []

    def handler(req):
        return _json(next(states))

    st = mock_client(handler, token="t").wait(
        "9", timeout_s=10, interval_s=0.001, on_update=lambda s: updates.append(s.status)
    )
    assert st.status == "done"
    assert updates == ["pending", "running", "done"]


def test_wait_timeout_raises():
    def handler(req):
        return _json({"jobid": "9", "status": "running"})

    with pytest.raises(JobError, match="Timeout"):
        mock_client(handler, token="t").wait("9", timeout_s=0.2, interval_s=0.001)


def test_status_without_token_raises_autherror_locally(home):
    with pytest.raises(AuthError, match="login"):
        mock_client(lambda req: _json({})).status("1")


# -------------------------------------------------------------------- fetch


def make_tar_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, blob in entries.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(blob)
            tar.addfile(info, io.BytesIO(blob))
    return buf.getvalue()


def test_fetch_job_extracts_and_manifest(home):
    payload = make_tar_bytes({"charmm-gui.tgz": b"inner", "README": b"hi"})
    downloads = {"n": 0}

    def handler(req):
        if req.url.path.endswith("/check_status"):
            assert req.headers["Authorization"] == "Bearer t"
            return _json({"jobid": "42", "status": "done", "hasTarFile": True, "lastInpStep": 8})
        if req.url.path.endswith("/download"):
            downloads["n"] += 1
            return httpx.Response(200, content=payload)
        raise AssertionError(f"endpoint inatteso {req.url}")

    result = fetch_job(
        mock_client(handler, token="t"),
        "42",
        home / "results",
        label="POPC64",
        spec_json=leaflet_spec().model_dump_json(),
    )

    assert (result["system_dir"] / "charmm-gui.tgz").read_bytes() == b"inner"
    assert result["sha256"] == sha256_file(result["tar"])
    name = result["dir"].name
    assert name.startswith(_dt.date.today().isoformat())
    assert "_POPC64_42" in name
    assert downloads["n"] == 1

    manifest = json.loads((result["dir"] / "manifest.json").read_text())
    assert manifest["jobid"] == "42"
    assert manifest["archive"]["sha256"] == result["sha256"]
    assert manifest["status"]["status"] == "done"
    assert manifest["spec"]["upper"] == "POPC=64"
    assert set(manifest["files"]) == {"charmm-gui.tgz", "README"}


def test_fetch_refuses_not_done():
    def handler(req):
        return _json({"jobid": "5", "status": "running"})

    with pytest.raises(JobError, match="running"):
        fetch_job(mock_client(handler, token="t"), "5", Path("/tmp/x"))


def test_fetch_done_without_tar_refuses():
    def handler(req):
        return _json({"jobid": "5", "status": "done", "hasTarFile": False})

    with pytest.raises(JobError, match="hasTarFile"):
        fetch_job(mock_client(handler, token="t"), "5", Path("/tmp/x"))


def test_download_404_raises_job_error(tmp_path):
    def handler(req):
        return httpx.Response(404, text="Forbidden: Run the workflow first")

    with pytest.raises(JobError, match="404"):
        mock_client(handler, token="t").download("5", tmp_path / "nope.tgz")
