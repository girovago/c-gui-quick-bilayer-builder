"""Archivio SQLite dei job inviati (provenance: spec, stato, sha256 dell'archivio)."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import paths
from .client import CguiError
from .models import JobRecord

__all__ = ["JobStore"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    jobid TEXT UNIQUE,
    label TEXT,
    spec_json TEXT NOT NULL,
    state TEXT,
    created_at TEXT,
    updated_at TEXT,
    outdir TEXT,
    archive_sha256 TEXT,
    last_step INTEGER,
    rank TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS jobs_updated_idx ON jobs (updated_at);
"""

_UPDATABLE = frozenset({"state", "label", "outdir", "archive_sha256", "last_step", "rank", "error"})
_COLUMNS = (
    "id",
    "jobid",
    "label",
    "spec_json",
    "state",
    "created_at",
    "updated_at",
    "outdir",
    "archive_sha256",
    "last_step",
    "rank",
    "error",
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _record(row: sqlite3.Row) -> JobRecord:
    return JobRecord(**{col: row[col] for col in _COLUMNS})


class JobStore:
    """SQLite singola connessione (check_same_thread=False, WAL) protetta da lock."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.path = Path(db_path) if db_path is not None else paths.db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def __enter__(self) -> JobStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    @staticmethod
    def _dumps(spec: Any) -> str:
        if isinstance(spec, str):  # già JSON
            return spec
        if hasattr(spec, "model_dump_json"):
            return str(spec.model_dump_json())
        return json.dumps(spec, ensure_ascii=False, sort_keys=True)

    def add(
        self,
        jobid: str,
        spec: Any,
        *,
        label: str | None = None,
        state: str = "submitted",
        outdir: str | None = None,
    ) -> JobRecord:
        spec_json = self._dumps(spec)
        if label is None and hasattr(spec, "label"):
            raw_label = spec.label
            label = raw_label if isinstance(raw_label, str) else None
        now = _now()
        try:
            with self._lock:
                cur = self._conn.execute(
                    "INSERT INTO jobs "
                    "(jobid, label, spec_json, state, created_at, updated_at, outdir) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (str(jobid), label, spec_json, state, now, now, outdir),
                )
                self._conn.commit()
        except sqlite3.IntegrityError as exc:
            raise CguiError(
                f"Job {jobid} già presente nell'archivio (jobid UNIQUE): usa `cgui status {jobid}`."
            ) from exc
        return JobRecord(
            id=int(cur.lastrowid),
            jobid=str(jobid),
            label=label,
            spec_json=spec_json,
            state=state,
            created_at=now,
            updated_at=now,
            outdir=outdir,
        )

    def update(self, jobid: str, **fields: Any) -> JobRecord | None:
        """Aggiorna solo colonne ammesse (+updated_at). Ritorna la riga o None se assente."""
        unknown = set(fields) - _UPDATABLE
        if unknown:
            raise CguiError(f"Campi di update non validi: {sorted(unknown)}")
        if not fields:
            return self.get_by_jobid(jobid)
        keys = list(fields)
        sql = (
            f"UPDATE jobs SET updated_at = ?, {', '.join(f'{k} = ?' for k in keys)} WHERE jobid = ?"
        )
        values = [_now()] + [fields[k] for k in keys] + [str(jobid)]
        with self._lock:
            self._conn.execute(sql, values)
            self._conn.commit()
        return self.get_by_jobid(jobid)

    def get_by_jobid(self, jobid: str) -> JobRecord | None:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM jobs WHERE jobid = ?", (str(jobid),))
            row = cur.fetchone()
        return _record(row) if row else None

    def list_jobs(self, limit: int = 50) -> list[JobRecord]:
        limit = max(0, int(limit))
        with self._lock:
            cur = self._conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))
            rows = cur.fetchall()
        return [_record(row) for row in rows]

    def set_state(self, jobid: str, state: str) -> JobRecord | None:
        return self.update(jobid, state=state)
