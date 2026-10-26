"""Fixture condivise: home temporanea (CGUI_STUDIO_HOME) e cosstruzione client mock."""

from __future__ import annotations

import httpx
import pytest

from cgui_studio.client import Client
from cgui_studio.models import Spec

MOCK_BASE = "https://mock.test/api"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CGUI_STUDIO_HOME", str(tmp_path / "cguihome"))
    return tmp_path / "cguihome"


@pytest.fixture
def sleep_noop(monkeypatch):
    monkeypatch.setattr("cgui_studio.client.time.sleep", lambda _s: None)


def mock_client(handler, **kw) -> Client:
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url=MOCK_BASE)
    return Client(base_url=MOCK_BASE, http=http, **kw)


def leaflet_spec(**kw) -> Spec:
    kw.setdefault("upper", "POPC=64")
    kw.setdefault("lower", "POPC=64")
    kw.setdefault("label", "POPC64")
    return Spec(**kw)
