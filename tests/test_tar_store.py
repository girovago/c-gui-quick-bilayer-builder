"""Test estrazione sicura del tar (rifiuto path traversal) e store SQLite."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from cgui_studio.client import CguiError, safe_extract
from cgui_studio.models import JobRecord
from cgui_studio.store import JobStore


def write_tar(path: Path, entries: dict[str, bytes], *, symlinks: dict[str, str] | None = None):
    with tarfile.open(path, "w:gz") as tar:
        for name, blob in entries.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(blob)
            tar.addfile(info, io.BytesIO(blob))
        for name, target in (symlinks or {}).items():
            info = tarfile.TarInfo(name=name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            tar.addfile(info)


def test_rejects_path_traversal(tmp_path):
    hostile = tmp_path / "evil.tar.gz"
    write_tar(hostile, {"../evil": b"x"})
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(CguiError, match="rifiutato"):
        safe_extract(hostile, out)
    # nessun file fuori dalla destinazione
    assert not (tmp_path / "evil").exists()
    assert not (tmp_path.parent / "evil").exists()


def test_rejects_absolute_path(tmp_path):
    hostile = tmp_path / "evil2.tar.gz"
    with tarfile.open(hostile, "w:gz") as tar:
        info = tarfile.TarInfo(name="/tmp/abs_evil")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    out = tmp_path / "out2"
    out.mkdir()
    with pytest.raises(CguiError):
        safe_extract(hostile, out)
    assert not Path("/tmp/abs_evil").exists()


def test_rejects_symlink_escape(tmp_path):
    hostile = tmp_path / "evil3.tar.gz"
    write_tar(hostile, {}, symlinks={"link": "../../outside"})
    out = tmp_path / "out3"
    out.mkdir()
    with pytest.raises(CguiError):
        safe_extract(hostile, out)


def test_extracts_valid_tar(tmp_path):
    good = tmp_path / "good.tar.gz"
    write_tar(good, {"sub/input_file_packaged.tar.gz": b"payload", "README": b"ok"})
    out = tmp_path / "out"
    out.mkdir()
    safe_extract(good, out)
    assert (out / "sub" / "input_file_packaged.tar.gz").read_bytes() == b"payload"
    assert (out / "README").read_bytes() == b"ok"


def test_invalid_archive_raises(tmp_path):
    bad = tmp_path / "bad.tar.gz"
    bad.write_bytes(b"not a tar at all")
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(CguiError):
        safe_extract(bad, out)


# --------------------------------------------------------------------- store


def _spec_json(**kw) -> str:
    from tests.conftest import leaflet_spec

    return leaflet_spec(**kw).model_dump_json()


def test_store_crud_roundtrip(home):
    with JobStore() as store:
        rec = store.add("9285768142", _spec_json(), label="POPC64")
        assert rec.id is not None
        assert rec.jobid == "9285768142"
        assert rec.state == "submitted"
        assert rec.created_at and rec.updated_at

        got = store.get_by_jobid("9285768142")
        assert got is not None
        assert got.label == "POPC64"
        assert got.spec_dict["upper"] == "POPC=64"

        store.set_state("9285768142", "done")
        updated = store.get_by_jobid("9285768142")
        assert updated.state == "done"
        assert updated.updated_at >= updated.created_at

        store.update("9285768142", archive_sha256="abc", last_step=8, rank="r", outdir="/x")
        full = store.get_by_jobid("9285768142")
        assert full.archive_sha256 == "abc" and full.last_step == 8 and full.rank == "r"

        assert store.get_by_jobid("sconosciuto") is None
        ids = [j.jobid for j in store.list_jobs(limit=10)]
        assert ids == ["9285768142"]
        assert isinstance(full, JobRecord)


def test_store_unique_jobid(home):
    from cgui_studio.client import CguiError

    with JobStore() as store:
        store.add("111", _spec_json())
        with pytest.raises(CguiError, match="già presente"):
            store.add("111", _spec_json())


def test_store_list_limit(home):
    with JobStore() as store:
        for i in range(5):
            store.add(f"j{i:02d}", _spec_json())
        rows = store.list_jobs(limit=3)
        assert [r.jobid for r in rows] == ["j04", "j03", "j02"]


def test_store_wal_mode(home):
    with JobStore() as store:
        mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
