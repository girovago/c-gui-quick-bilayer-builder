"""Test dell'archivio: coda ASCII dopo il gzip (il bug reale del server)."""

from __future__ import annotations

import io
import os
import tarfile

import pytest

from cgui_studio.archive import ArchiveError, extract, normalize, safe_extract_file, sanitize_file


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


# ------------------------------------------------------------- sanitize_file
# Streaming da disco (BUG 3): stesso esito di normalize, zero RAM dell'intero.

def test_sanitize_clean_gzip_untouched(tmp_path):
    raw = make_gzip()
    p = tmp_path / "a.tar.gz"
    p.write_bytes(raw)
    assert sanitize_file(p) == len(raw)
    assert p.read_bytes() == raw


def test_sanitize_ascii_tail_truncated_in_place(tmp_path):
    raw = make_gzip()
    p = tmp_path / "a.tar.gz"
    p.write_bytes(raw + b"324697405")
    assert sanitize_file(p) == len(raw)
    assert p.read_bytes() == raw  # la coda è andata, il gzip è intatto


def test_sanitize_binary_tail_refused(tmp_path):
    p = tmp_path / "a.tar.gz"
    p.write_bytes(make_gzip() + b"\x00\x01\x02evil")
    with pytest.raises(ArchiveError, match="coda inattesa"):
        sanitize_file(p)


def test_sanitize_zero_tail_refused_like_normalize(tmp_path):
    """Coda di soli \\x00: normalize la rifiuta (isdigit di b'' è Falso) → idem."""
    raw = make_gzip()
    with pytest.raises(ArchiveError, match="coda inattesa"):
        normalize(raw + b"\x00")
    p = tmp_path / "a.tar.gz"
    p.write_bytes(raw + b"\x00")
    with pytest.raises(ArchiveError, match="coda inattesa"):
        sanitize_file(p)


def test_sanitize_non_gzip_and_truncated(tmp_path):
    p = tmp_path / "a.tar.gz"
    p.write_bytes(b"not a gzip at all")
    with pytest.raises(ArchiveError, match="magic number"):
        sanitize_file(p)

    full = make_gzip()
    p2 = tmp_path / "b.tar.gz"
    p2.write_bytes(full[: len(full) // 2])
    with pytest.raises(ArchiveError, match="incompleto"):
        sanitize_file(p2)


def test_safe_extract_large_streaming_matches_normalize(tmp_path):
    """tar.gz ~20MB → sanitize+extract in stream ≡ extract(normalize(bytes))."""
    blob = os.urandom(20 * (1 << 20))  # incompressibile: 20MB anche compresso
    entries = {"system/big/random.bin": blob, "system/README.txt": b"ok"}

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in entries.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    gz = buf.getvalue()
    assert len(gz) > 19 * (1 << 20)

    raw = gz + b"123456789"  # la coda ASCII del server

    # percorso classico (in RAM) come riferimento
    ref = tmp_path / "ref"
    ref.mkdir()
    n = normalize(raw)  # non tocca il disco: lavora sui bytes
    names_ref = set(extract(n, ref))
    assert names_ref == set(entries)
    assert (ref / "system/big/random.bin").read_bytes() == blob

    # percorso streaming
    p = tmp_path / "job.tar.gz"
    p.write_bytes(raw)
    out = tmp_path / "out"
    names = safe_extract_file(p, out)
    assert set(names) == set(entries)
    assert (out / "system/big/random.bin").read_bytes() == blob
    assert (out / "system/README.txt").read_bytes() == b"ok"
    assert p.read_bytes() == gz  # coda ritagliata in place
