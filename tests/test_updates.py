"""GET /api/update ePOST /api/update: degradazione onesta, mai eccezioni."""

from __future__ import annotations

import subprocess

import cgui_studio.updates as updates


def test_status_fuori_da_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "ROOT", tmp_path)
    out = updates.status()
    assert out == {"available": False, "behind": 0, "mode": "no-repo", "detail": out["detail"]}
    assert "git" in out["detail"]


def test_status_repo_senza_remote(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    monkeypatch.setattr(updates, "ROOT", repo)
    out = updates.status()
    assert out["available"] is False
    assert out["mode"] == "no-remote"


def test_apply_npm_assente_non_errore(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "ROOT", tmp_path)  # senza repo né frontend
    out = updates.apply()
    assert out["mode"] == "error"  # pull fuori da repo → fallito pulito
    assert out["build"]["done"] is False


def test_root_reale():
    """Il ROOT reale del repo: stato in un dei modi degradati/ok noti (mai crash)."""
    out = updates.status()
    assert out["mode"] in {"ok", "no-remote", "offline", "no-remote-branch", "diverged"}
