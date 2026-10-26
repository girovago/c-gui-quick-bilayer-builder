"""Web locale (FastAPI) sopra il core CLI — non sostituisce il core, lo riusa.

Rotte (/api/*), tutte sincrone: Metro FastAPI in threadpool, il core è sync
(SQLite con lock, client httpx sincrono):

- ``GET  /api/ping``                    -> ``{"ok": true, "mock": <bool>}``
- ``POST /api/jobs``                    -> submit Quick Bilayer (mock ok)
- ``GET  /api/jobs``                    -> record d'archivio dal JobStore
- ``GET  /api/jobs/{jobid}``            -> ``{record, status}`` con 1 status() vero
- ``POST /api/jobs/{jobid}/fetch``      -> ``{"wait": true}``? poi fetch_job + manifest
- ``GET  /api/files/{jobid}``           -> albero dei file estratti
- ``GET  /api/files/{jobid}/{path..}``  -> file (text/binary; rifiuta ``..``/symlink)
- ``POST /api/form-preview``            -> debug payload ``query/form`` della `Spec`
- ``GET  /api/sample/pdb``              -> struttura campione locale (header ``X-Sample``)

Errori coerenti: 404 job inesistente / non estratto, 409 token assente o job non
pronto, 502 errori API a monte (messaggio del client), 422 errori del validator di
Spec (pydantic).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, ValidationError

from .client import ApiError, AuthError, Client, JobError, fetch_job
from .models import JobRecord, JobStatus, Spec
from .paths import default_results_dir
from .store import JobStore
from .webmock import FakeClient, bilayer_pdb_bytes

#: estensioni servite da /api/files come text/plain (il resto: octet-stream)
_TEXT_SUFFIXES = frozenset(
    {
        ".txt",
        ".pdb",
        ".str",
        ".top",
        ".mdp",
        ".itp",
        ".json",
        ".log",
        ".csv",
        ".rst",
        ".xml",
        ".conf",
    }
)


class JobInput(BaseModel):
    """Body di POST /api/jobs: solo i campi documentati, i campi in più sono 422."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    upper: str | None = None
    lower: str | None = None
    membtype: str | None = None
    margin: float = 20.0
    wdist: float | None = None
    ion_conc: float = 0.15
    ion_type: str = "NaCl"
    membrane_only: bool = True
    run_ffconverter: bool = True
    # nome del campo dell'API reale; in `Spec` si chiama clone_job_from
    clone_job: str | None = None

    def to_spec(self) -> Spec:
        data: dict[str, Any] = {
            "margin": self.margin,
            "ion_conc": self.ion_conc,
            "ion_type": self.ion_type,
            "membrane_only": self.membrane_only,
            "run_ffconverter": self.run_ffconverter,
        }
        for name in ("label", "upper", "lower", "membtype", "wdist"):
            if getattr(self, name) is not None:
                data[name] = getattr(self, name)
        if self.clone_job:
            data["clone_job_from"] = self.clone_job
        try:
            return Spec(**data)
        except ValidationError as exc:
            raise HTTPException(422, _validation_detail(exc)) from exc


class FetchInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    wait: bool = False
    timeout: float = 3600.0


def _validation_detail(exc: ValidationError) -> str:
    """Messaggi leggibili del validator (i testi di Spec sono già in italiano)."""
    parts: list[str] = []
    for err in exc.errors():
        msg = str(err.get("msg", "valore non valido"))
        loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts) or "spec non valida"


def create_app(
    *,
    mock: bool | None = None,
    client_factory: Any = None,  # () -> Client | FakeClient; per iniettare il mock nei test
) -> FastAPI:
    """App factory. Un worker: client e store restano per vita processo."""
    mock = bool(os.environ.get("CGUI_MOCK")) if mock is None else mock
    app = FastAPI(title="cgui-studio", version="0.1.0", docs_url=None, redoc_url=None)
    app.state.mock = mock
    app.state.client = client_factory() if client_factory else (FakeClient() if mock else Client())
    app.state.store = JobStore()
    _routes(app, app.state.client, app.state.store)
    _mount_static(app)
    return app


@contextmanager
def _map_errors(mock: bool) -> Iterator[None]:
    """Traduzione unica e centralizzata tra eccezioni del core e codici HTTP."""
    try:
        yield
    except AuthError:
        msg = "esegui `uv run cgui login`" if not mock else "mock: auth non richiesta"
        raise HTTPException(409, msg) from None
    except ApiError as exc:
        raise HTTPException(502, str(exc)) from exc
    except JobError as exc:
        # Condivide deliberatamente il 409 con AuthError: per il frontend è lo
        # stesso stato "transitorio, riprova" (job non pronto, fetch senza
        # archivio, timeout di client.wait — incluso il FakeClient nella GUI).
        raise HTTPException(409, str(exc)) from exc


def _routes(app: FastAPI, client: Client | FakeClient, store: JobStore) -> None:
    @app.get("/api/ping")
    def ping() -> dict[str, Any]:
        return {"ok": True, "mock": app.state.mock}

    @app.get("/api/sample/pdb")
    def sample_pdb() -> Response:
        """Struttura campione generata in locale (zero rete esterna).

        Stesso bilayer finto del mock (128 lipidi: 64+64,acqua e ioni): serve
        da demo per la vista 3D quando non c'è un sistema reale estratto.
        """
        return Response(
            content=bilayer_pdb_bytes(lipids_side=8),
            media_type="text/plain",
            headers={"X-Sample": "true", "Cache-Control": "no-store"},
        )

    @app.post("/api/jobs")
    def submit(payload: JobInput) -> dict[str, Any]:
        spec = payload.to_spec()
        with _map_errors(app.state.mock):
            response = client.submit(spec)
        if not isinstance(response, dict):
            raise HTTPException(502, f"risposta submit non interpretabile: {response!r:.200}")
        jobid = str(response.get("jobid", "") or "")
        if not jobid:
            raise HTTPException(502, f"job inviato ma senza jobid: {response!r:.300}")
        try:
            store.add(jobid, spec, state="submitted")
        except Exception as exc:
            raise HTTPException(409, f"jobid {jobid} non registrabile: {exc}") from exc
        return {
            "jobid": jobid,
            "modules": response.get("modules"),
            "spec": spec.model_dump(mode="json"),
        }

    @app.get("/api/jobs")
    def jobs() -> list[dict[str, Any]]:
        return [r.model_dump(mode="json") for r in store.list_jobs()]

    @app.get("/api/jobs/{jobid}")
    def job_detail(jobid: str) -> dict[str, Any]:
        _record_or_404(store, jobid)
        with _map_errors(app.state.mock):
            st = client.status(jobid)
        record = _sync_status(store, jobid, st)
        return {
            "record": (record or _record_or_404(store, jobid)).model_dump(mode="json"),
            "status": st.model_dump(mode="json", exclude_none=True),
        }

    @app.post("/api/jobs/{jobid}/fetch")
    def job_fetch(jobid: str, body: FetchInput | None = None) -> dict[str, Any]:
        record = _record_or_404(store, jobid)
        data = body or FetchInput()
        with _map_errors(app.state.mock):
            st = client.status(jobid)
            if data.wait and not st.is_terminal:
                st = client.wait(jobid, timeout_s=data.timeout)
            if (st.status or "") != "done":
                raise JobError(
                    f"job {jobid} non pronto (stato '{st.status or '?'}'): "
                    "attendi o riprova con wait=true"
                )
            result = fetch_job(
                client, jobid, default_results_dir(), label=record.label, spec_json=record.spec_json
            )
        store.update(
            jobid,
            state="done",
            outdir=str(result["dir"]),
            archive_sha256=result["sha256"],
            last_step=st.last_inp_step,
        )
        record = store.get_by_jobid(jobid) or record
        return {
            "jobid": jobid,
            "dir": str(result["dir"]),
            "system_dir": str(result["system_dir"]),
            "sha256": result["sha256"],
            "files": result["files"],
            "record": record.model_dump(mode="json"),
        }

    @app.get("/api/files/{jobid}")
    def file_tree(jobid: str) -> dict[str, Any]:
        base = _system_dir(store, jobid)
        files: list[dict[str, Any]] = []
        stack: list[Path] = [base]
        while stack:
            d = stack.pop()
            for entry in sorted(d.iterdir(), key=lambda p: p.name):
                if entry.is_dir():
                    stack.append(entry)
                elif entry.is_file():
                    rel = entry.relative_to(base).as_posix()
                    try:
                        size = entry.stat().st_size
                    except OSError:
                        continue
                    files.append({"name": rel, "size": size, "depth": len(Path(rel).parts)})
        return {"jobid": jobid, "files": files}

    @app.get("/api/files/{jobid}/{file_path:path}")
    def file_content(jobid: str, file_path: str) -> FileResponse:
        system = _system_dir(store, jobid)
        if ".." in PurePosixPath(file_path).parts:
            raise HTTPException(404, "path rifiutato: fuori dalla cartella system del job")
        candidate = (system / file_path).resolve()
        if not candidate.is_relative_to(system.resolve()) or candidate.is_symlink():
            raise HTTPException(404, "path rifiutato: fuori dalla cartella system del job")
        if not candidate.is_file():
            raise HTTPException(404, f"file non trovato nel job {jobid}: {file_path}")
        media = (
            "text/plain"
            if candidate.suffix.lower() in _TEXT_SUFFIXES
            else "application/octet-stream"
        )
        return FileResponse(
            candidate,
            media_type=media,
            filename=candidate.name,
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/form-preview")
    def form_preview(payload: JobInput) -> dict[str, Any]:
        spec = payload.to_spec()
        return {"query": spec.to_query(), "form": spec.to_form()}

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        """Con build di produzione (web/dist) serve la GUI; altrimenti un hint."""
        dist = _dist_dir()
        if dist is None:
            raise HTTPException(
                404,
                "nessun frontend compilato: `npm i && npm run build` in web/ "
                "(per sviluppo: `npm run dev` + `cgui serve`).",
            )
        return FileResponse(dist / "index.html")


def _record_or_404(store: JobStore, jobid: str) -> JobRecord:
    record = store.get_by_jobid(jobid)
    if record is None:
        raise HTTPException(404, f"job {jobid} inesistente (mai inviato da questa installazione)")
    return record


def _sync_status(store: JobStore, jobid: str, st: JobStatus) -> JobRecord | None:
    """Aggiornamento identico al `_sync_store` del CLI: state/rank/step (+error)."""
    fields: dict[str, Any] = {"state": st.status, "rank": st.rank, "last_step": st.last_inp_step}
    if st.status == "error":
        fields["error"] = st.error or "stato d'errore senza dettagli"
    return store.update(jobid, **fields)


def _system_dir(store: JobStore, jobid: str) -> Path:
    record = _record_or_404(store, jobid)
    if not record.outdir:
        raise HTTPException(404, f"job {jobid}: nessun archivio estratto (usa 'Scarica e estrai')")
    system = Path(record.outdir) / "system"
    if not system.is_dir():
        raise HTTPException(404, f"job {jobid}: system/ non trovato in {record.outdir}")
    return system


def _dist_dir() -> Path | None:
    """web/dist accanto al pacchetto (layout repo: src/cgui_studio/*.py)."""
    dist = Path(__file__).resolve().parents[2] / "web" / "dist"
    return dist if (dist / "index.html").is_file() else None


def _mount_static(app: FastAPI) -> None:
    dist = _dist_dir()
    if dist is not None:
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")


def run(host: str = "127.0.0.1", port: int = 8756) -> None:
    """Avvio uvicorn (da `cgui serve`)."""
    import uvicorn

    uvicorn.run(create_app(), host=host, port=port, log_level="info")
