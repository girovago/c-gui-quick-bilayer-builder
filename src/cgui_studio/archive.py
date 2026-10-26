"""Normalizzazione ed estrazione sicura degli archivi CHARMM-GUI.

Il server aggiunge, dopo lo stream gzip completo, una coda ASCII con la
lunghezza compressa (es. ``b'324697405'``). Un lettore ingenuo la tratta come
parte del file e fallisce, oppure la ignora accettando un archivio troncato.
Qui: si decomprime il primo membro gzip, si verifica che la coda sia vuota o
composta solo da cifre, si controllano i membri (path traversal, path assoluti,
symlink che escono) e solo allora si estrae con il filtro ``data``.
"""

from __future__ import annotations

import hashlib
import io
import tarfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class ArchiveError(RuntimeError):
    """Archivio non valido, troncato, corrotto o sospetto."""


@dataclass
class Normalized:
    """Risultato della normalizzazione."""

    payload: bytes  # tar decompresso, pronto per tarfile
    compressed_size: int
    trailing: bytes
    sha256: str  # del file gzip originale, coda inclusa

    @property
    def had_trailing(self) -> bool:
        return bool(self.trailing)


def normalize(raw: bytes) -> Normalized:
    """Decomprime il primo membro gzip e valida la coda."""
    if not raw.startswith(b"\x1f\x8b"):
        raise ArchiveError("non è un gzip: manca il magic number 1f 8b")

    dec = zlib.decompressobj(wbits=31)
    try:
        payload = dec.decompress(raw)
        payload += dec.flush()
    except zlib.error as exc:
        raise ArchiveError(f"gzip corrotto o troncato: {exc}") from exc

    if not dec.eof:
        raise ArchiveError("gzip incompleto: stream terminato prima della fine")

    trailing = dec.unused_data
    if trailing and not trailing.strip(b"\x00").isdigit():
        raise ArchiveError(
            f"coda inattesa dopo il gzip ({len(trailing)} byte, inizio {trailing[:32]!r}): "
            "archivio sospetto"
        )

    return Normalized(
        payload=payload,
        compressed_size=len(raw) - len(trailing),
        trailing=trailing,
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def _check_member(member: tarfile.TarInfo) -> None:
    """Rifiuta membri che scriverebbero fuori dalla destinazione."""
    name = member.name
    if name.startswith("/") or PurePosixPath(name).is_absolute():
        raise ArchiveError(f"membro con path assoluto rifiutato: {name}")
    parts = PurePosixPath(name).parts
    if ".." in parts:
        raise ArchiveError(f"membro con path traversal rifiutato: {name}")
    if member.issym() or member.islnk():
        target = PurePosixPath(member.linkname)
        if target.is_absolute() or ".." in target.parts:
            raise ArchiveError(
                f"link che esce dalla destinazione rifiutato: {name} -> {member.linkname}"
            )


def extract(normalized: Normalized, target: Path) -> list[str]:
    """Estrae il tar in ``target`` e restituisce i path relativi scritti."""
    target.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(fileobj=io.BytesIO(normalized.payload)) as tar:
            members = tar.getmembers()
            for member in members:
                _check_member(member)
            tar.extractall(target, members=members, filter="data")
            return [m.name for m in members if m.isfile()]
    except ArchiveError:
        raise
    except (tarfile.TarError, OSError) as exc:
        raise ArchiveError(f"tar non estraibile: {exc}") from exc


def safe_extract_file(archive_path: Path, target: Path) -> list[str]:
    """Legge un archivio da disco, lo normalizza e lo estrae in sicurezza."""
    archive_path = Path(archive_path)
    if not archive_path.is_file():
        raise ArchiveError(f"archivio non trovato: {archive_path}")
    return extract(normalize(archive_path.read_bytes()), target)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
