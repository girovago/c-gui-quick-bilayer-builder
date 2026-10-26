"""Test dell'archivio: coda ASCII dopo il gzip (il bug reale del server)."""

from __future__ import annotations

import io
import tarfile

import pytest

from cgui_studio.archive import ArchiveError, normalize


def make_gzip(entries: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, blob in (entries or {"README": b"ok"}).items():
            info = tarfile.TarInfo(name=name)
            info.size = len(blob)
            tar.addfile(info, io.BytesIO(blob))
    return buf.getvalue()


def test_clean_gzip_no_trailing():
    n = normalize(make_gzip())
    assert n.payload.startswith(b"README") or b"README" in str(n.payload)
    assert not n.had_trailing


def test_ascii_trailing_length_accepted():
    """Il server accoda la lunghezza compressa come cifre ASCII: va accettata."""
    n = normalize(make_gzip() + b"324697405")
    assert n.had_trailing
    assert n.compressed_size == len(make_gzip())
    assert b"README" in n.payload


def test_binary_trailing_refused():
    """Una coda NON numerica è un archivio sospetto: rifiuto esplicito."""
    with pytest.raises(ArchiveError, match="coda inattesa"):
        normalize(make_gzip() + b"\x00\x01\x02evil")


def test_non_gzip_refused():
    with pytest.raises(ArchiveError, match="magic number"):
        normalize(b"this is not a gzip at all")


def test_truncated_gzip_refused():
    full = make_gzip()
    with pytest.raises(ArchiveError, match="incompleto"):
        normalize(full[: len(full) // 2])
