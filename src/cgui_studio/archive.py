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
    """Estrae un archivio da disco in stream (senza caricarlo tutto in RAM).

    Prima ``sanitize_file`` verifica/ritaglia la coda ASCII a chunk,
    poi ``tarfile.open("r:gz")`` legge dal disco: solo i singoli membri
    (piccoli) vengono tenuti in memoria durante l'estrazione.
    """
    archive_path = Path(archive_path)
    if not archive_path.is_file():
        raise ArchiveError(f"archivio non trovato: {archive_path}")
    sanitize_file(archive_path)
    try:
        with tarfile.open(archive_path, "r:gz") as tar:
            members = tar.getmembers()
            for member in members:
                _check_member(member)
            tar.extractall(target, members=members, filter="data")
            return [m.name for m in members if m.isfile()]
    except ArchiveError:
        raise
    except (tarfile.TarError, OSError) as exc:
        raise ArchiveError(f"tar non estraibile: {exc}") from exc


#: la coda reale del server sono poche cifre ASCII: oltre è garbage
_TAIL_LIMIT = 4096


def sanitize_file(path: Path) -> int:
    """Verifica da disco lo stream gzip e ritaglia la coda ASCII in place.

    Equivalente in risultato a ``normalize`` ma a chunk (1 MiB): l'archivio
    non passa mai intero per la RAM. Restituisce la lunghezza del payload
    gzip valido. Semantica identica: coda assente ok; coda di sole cifre
    (eventuali ``\\x00`` ai lati) accettata e rimossa; qualunque altra coda
    (o assenza di fine stream) → ``ArchiveError``. Unica differenza difensiva:
    code oltre ``_TAIL_LIMIT`` byte (irrealistiche) vengono rifiutate.
    """
    path = Path(path)
    dec = zlib.decompressobj(wbits=31)
    eof = False
    end_offset = 0
    trailing: list[bytes] = []
    with path.open("rb") as fh:
        # peek (non consume): il decompressore vuole ricevere il magic in input
        if fh.peek(2)[:2] != b"\x1f\x8b":
            raise ArchiveError("non è un gzip: manca il magic number 1f 8b")
        consumed = 0
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            if eof:
                trailing.append(chunk)
                if sum(len(t) for t in trailing) > _TAIL_LIMIT:
                    raise ArchiveError(
                        f"coda inattesa dopo il gzip (> {_TAIL_LIMIT} byte): archivio sospetto"
                    )
                continue
            consumed += len(chunk)
            try:
                dec.decompress(chunk)
            except zlib.error as exc:
                raise ArchiveError(f"gzip corrotto o troncato: {exc}") from exc
            if dec.eof:
                eof = True
                end_offset = consumed - len(dec.unused_data)
                if dec.unused_data:
                    trailing.append(dec.unused_data)
    if not eof:
        raise ArchiveError("gzip incompleto: stream terminato prima della fine")
    tail = b"".join(trailing)
    # condizione identica a normalize (una coda di soli \x00 è rifiutata):
    if tail and not tail.strip(b"\x00").isdigit():
        raise ArchiveError(
            f"coda inattesa dopo il gzip ({len(tail)} byte, inizio {tail[:32]!r}): "
            "archivio sospetto"
        )
    if trailing:
        with path.open("r+b") as fh:
            fh.truncate(end_offset)
    return end_offset


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
