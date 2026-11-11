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

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, ValidationError

from . import updates
from .client import ApiError, AuthError, CguiError, Client, JobError, fetch_job
from .models import JobRecord, JobStatus, Spec
from .paths import default_results_dir, load_token
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


class AuthInput(BaseModel):
    """Credenziali CHARMM-GUI: il token risultante resta SOLO in RAM (niente
    token.json, niente log): non archiviamo mai credenziali altrui su disco."""
    model_config = ConfigDict(extra="forbid")

    email: str
    password: str


class MakeBilayerInput(BaseModel):
    """Body di POST /api/make-bilayer: spec lipidica per build locale.

    I controlli fisici del launcher (box/centratura/layout/solvente) sono
    accettati e conservati per provenance; lo script li ignora finché non
    espone gli argomenti corrispondenti (make_bilayer.py supporta già ioni).
    """

    model_config = ConfigDict(extra="forbid")

    spec: str | None = None
    upper: str | None = None
    lower: str | None = None
    run_gmx: bool = False
    box_x: float | None = None
    box_y: float | None = None
    box_z: float | None = None
    center: bool | None = None
    layout: str | None = None
    solvent: str | None = None
    ions: str | None = None  # "KCl" | "NaCl" | "none"
    build_type: str | None = None  # "lipid" | "protein" | "dna" | "rna" | "small-mol"
    protein_pdb_id: str | None = None
    protein_pdb_file_name: str | None = None
    dna_sequence: str | None = None
    rna_sequence: str | None = None
    small_mol_file_name: str | None = None


class EmbedProteinInput(BaseModel):
    """Body di POST /api/embed-protein: embed di un recettore in una membrana."""
    model_config = ConfigDict(extra="forbid")

    pdb_id: str | None = None          # PDB ID es. "7CFN" (opzionale se c'è file)
    pdb_file_name: str | None = None   # nome file PDB del recettore (se caricato)
    chain: str | None = "R"            # chain da usare
    membrane_pdb_file_name: str | None = None  # nome file PDB della membrana esistente
    out_dir: str | None = "results"    # directory di output


class UploadPDBInput(BaseModel):
    """Richiesta/risposta di POST /api/upload-pdb: nome file e contenuto testo."""
    model_config = ConfigDict(extra="forbid")

    file_name: str
    content: str = ""
    saved_path: str | None = None


class EmbedInMembraneInput(BaseModel):
    """Body di POST /api/embed-in-membrane: inserisce recettore in membrana esistente."""
    model_config = ConfigDict(extra="forbid")

    membrane_pdb_file_name: str
    receptor_pdb_file_name: str
    chain: str | None = "R"
    out_dir: str | None = "results"


def _validation_detail(exc: ValidationError) -> str:
    """Messaggi leggibili del validator (i testi di Spec sono già in italiano)."""
    parts: list[str] = []
    for err in exc.errors():
        msg = str(err.get("msg", "valore non valido"))
        loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts) or "spec non valida"


def _python_cmd(extra: list[str]) -> list[str]:
    """Comando per eseguire uno script Python in modo portabile.

    Usa ``conda run -n cgui-builder python`` se ``conda`` si trova nel PATH
    (ambiente di build con GROMACS), altrimenti l'interprete corrente
    (``sys.executable``). Nessun percorso home hardcoded: funziona su
    Windows, Linux e macOS.
    """
    conda = shutil.which("conda")
    if conda:
        return [conda, "run", "-n", "cgui-builder", "python"] + extra
    return [sys.executable] + extra


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
    # Auth CHARMM-GUI: SOLO in RAM. Volutamente non persistito: chiudendo il
    # processo la sessione muore, come richiesto (zero credenziali su disco).
    app.state.auth = {"token": None, "email": None, "ts": None, "expires": None}
    _routes(app, app.state.client, app.state.store)
    _mount_static(app)
    return app


@contextmanager
def _map_errors(mock: bool) -> Iterator[None]:
    """Traduzione unica e centralizzata tra eccezioni del core e codici HTTP."""
    try:
        yield
    except AuthError:
        msg = (
            "login CHARMM-GUI richiesto: usa il tasto Login CHARMM-GUI nella barra "
            "in alto (oppure `uv run cgui login` da CLI). La GUI web non salva il "
            "token su disco."
            if not mock
            else "mock: auth non richiesta"
        )
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

    @app.get("/api/pdbs")
    def list_pdbs() -> list[dict[str, str]]:
        """Elenca i file PDB disponibili in results/ (ricorsivo), per scegliere
        una struttura già generata senza doverla ricaricare."""
        base = default_results_dir()
        found: list[dict[str, str]] = []
        if base.exists():
            for p in sorted(base.rglob("*.pdb")):
                rel = p.relative_to(base)
                found.append({"name": p.name, "path": str(rel), "abs": str(p)})
        return found

    # ------------------------------------------------------------- aggiornamenti
    @app.get("/api/update")
    def update_status() -> dict[str, Any]:
        """Stato aggiornamenti (no-repo/offline = degradato, non errore)."""
        return updates.status()

    @app.post("/api/update")
    def update_apply() -> dict[str, Any]:
        """git pull --ff-only (+ build npm se web/dist manca). Mock: rifiutato."""
        if app.state.mock:
            raise HTTPException(409, "mock: il finto server non gestisce gli aggiornamenti")
        return updates.apply()

    # ------------------------------------------------------------- auth CHARMM-GUI
    def _mask(email: str) -> str:
        name, _, dom = email.partition("@")
        if not dom:
            return name[:2] + "***"
        return f"{name[:2]}***@{dom}"

    @app.post("/api/auth/login")
    def auth_login(payload: AuthInput) -> dict[str, Any]:
        """Login CHARMM-GUI: il JWT resta SOLO in RAM del processo (niente
        token.json): chi riavvia il server rifà login, zero credenziali salvate."""
        if app.state.mock:
            raise HTTPException(409, "mock: il finto server non gestisce il login")
        if not hasattr(app.state.client, "login_nocache"):
            raise HTTPException(409, "client non reale: login non disponibile")
        try:
            token = app.state.client.login_nocache(payload.email, payload.password)
        except AuthError as exc:
            # NIENTE dettaglio password nel messaggio: resta solo la risposta server.
            raise HTTPException(401, f"login CHARMM-GUI rifiutato: {exc}") from None
        except ApiError as exc:
            raise HTTPException(502, f"server CHARMM-GUI irraggiungibile: {exc}") from None
        app.state.auth.update(
            {"token": token, "email": payload.email, "ts": datetime.now(UTC).isoformat()}
        )
        app.state.client.set_token(token)
        return {"logged": True, "email": _mask(payload.email), "mode": "ram"}

    @app.get("/api/auth")
    def auth_get() -> dict[str, Any]:
        q = app.state.auth
        if q.get("token"):
            return {
                "logged": True,
                "email": _mask(str(q.get("email"))),
                "ts": q.get("ts"),
                "mode": "ram",
                "mock": app.state.mock,
            }
        # fallback trasparente: token rilasciato da `cgui login` su disco
        cached = load_token()
        if cached and cached.get("token"):
            return {
                "logged": True,
                "email": _mask(str(cached.get("email") or "dal comando cgui login")),
                "mode": "disk-cache",
                "mock": app.state.mock,
            }
        return {"logged": False, "mock": app.state.mock, "mode": "none"}

    @app.delete("/api/auth")
    def auth_logout() -> dict[str, Any]:
        """Chiude la sessione in RAM. Il token cache su disco, se esiste (CLI),
        resta com'è: qui non lo tocchiamo mai."""
        app.state.auth.update({"token": None, "email": None, "ts": None, "expires": None})
        if hasattr(app.state.client, "set_token"):
            app.state.client.set_token(None)
        return {"logged": False, "mode": "none"}

    @app.get("/api/sample/pdb")
    def sample_pdb() -> Response:
        """Struttura campione generata in locale (zero rete esterna).

        Stesso bilayer finto del mock (128 lipidi: 64+64,acqua e ioni): serve
        da demo per la vista 3D quando non c'è un sistema reale estratto.
        """
        return Response(
            content=bilayer_pdb_bytes(lipids_side=4),
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
        if jobid.startswith("local-"):
            # Job generato in locale: stato "done" simulato, niente client CHARMM-GUI.
            st = JobStatus(status="done", rank="locale", last_inp_step=0, has_tar=False, error=None)
            record = _record_or_404(store, jobid)
        else:
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
        system_root = system.resolve()
        if ".." in PurePosixPath(file_path).parts:
            raise HTTPException(404, "path rifiutato: fuori dalla cartella system del job")
        candidate = (system / file_path).resolve()
        try:
            candidate.relative_to(system_root)
        except ValueError:
            raise HTTPException(404, "path rifiutato: fuori system del job") from None
        if candidate.is_symlink():
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

    @app.post("/api/make-bilayer")
    def make_bilayer(payload: MakeBilayerInput) -> dict[str, Any]:
        """Genera una struttura bilayer all-atom in locale (GROMACS-compatibile).

        Esegue scripts/make_bilayer.py dentro l'ambiente conda `cgui-builder`
        (Python 3.12, GROMACS 2026.3, mdtraj/MDAnalysis) e registra il risultato
        come job `local-<ts>` nello store: stato "done", outdir assoluto, così
        le rotte /api/files/{jobid} e il dettaglio funzionano come per i job
        CHARMM-GUI reali.
        """
        script = Path(__file__).resolve().parents[2] / "scripts" / "make_bilayer.py"
        if not script.exists():
            raise HTTPException(500, f"script non trovato: {script}")
        base_out = default_results_dir()
        cmd = _python_cmd([str(script), "--output", str(base_out)])
        if payload.spec:
            cmd += ["--spec", payload.spec]
        if payload.upper:
            cmd += ["--upper", payload.upper]
        if payload.lower:
            cmd += ["--lower", payload.lower]
        if payload.run_gmx:
            cmd += ["--run-gmx"]
        # controlli fisici del launcher: passati allo script per generazione effettiva
        if payload.box_x is not None:
            cmd += ["--box-x", str(payload.box_x)]
        if payload.box_y is not None:
            cmd += ["--box-y", str(payload.box_y)]
        if payload.box_z is not None:
            cmd += ["--box-z", str(payload.box_z)]
        if payload.center is not None and not payload.center:
            cmd += ["--no-center"]
        if payload.layout:
            cmd += ["--layout", payload.layout]
        if payload.solvent:
            cmd += ["--solvent", payload.solvent]
        if payload.ions in ("KCl", "NaCl"):
            cmd += ["--ion-type", payload.ions, "--ion-conc", "0.15"]
        # Nuovi campi per proteine, acidi nucleici, small molecules
        if payload.build_type:
            cmd += ["--build-type", payload.build_type]
        if payload.protein_pdb_id:
            cmd += ["--pdb", payload.protein_pdb_id]
        if payload.dna_sequence:
            cmd += ["--dna", payload.dna_sequence]
        if payload.rna_sequence:
            cmd += ["--rna", payload.rna_sequence]
        if payload.small_mol_file_name:
            cmd += ["--small-mol", payload.small_mol_file_name]

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(504, f"make_bilayer timeout: {exc}") from exc

        if res.returncode != 0:
            raise HTTPException(502, f"make_bilayer fallito: {res.stderr[-800:]}")

        outdir = None
        for line in res.stdout.splitlines():
            if line.startswith("Bilayer generato in: "):
                outdir = line.split("Bilayer generato in: ", 1)[1].strip()
                break
        if not outdir:
            raise HTTPException(502, f"outdir non trovato nell'output: {res.stdout[-500:]}")

        outdir_path = Path(outdir)
        system_dir = outdir_path / "system"

        # Record nello store come job locale (stato "done", outdir assoluto).
        # Le rotte /api/files/{jobid} e il dettaglio funzionano come per i job reali.
        jobid = f"local-{int(datetime.now(UTC).timestamp())}"
        spec_in = {
            "spec": payload.spec,
            "upper": payload.upper,
            "lower": payload.lower,
            "run_gmx": payload.run_gmx,
            "source": "local",
        }
        # provenance dei controlli fisici del launcher (box/layout/solvente)
        launcher_params = {
            "box_x": payload.box_x,
            "box_y": payload.box_y,
            "box_z": payload.box_z,
            "center": payload.center,
            "layout": payload.layout,
            "solvent": payload.solvent,
        }
        if any(v is not None for v in launcher_params.values()) or payload.ions:
            launcher_params["ions"] = payload.ions
            spec_in["launcher"] = launcher_params
        label = payload.spec or payload.upper or "locale"
        try:
            store.add(jobid, spec_in, label=label, state="done", outdir=str(outdir_path))
        except CguiError:
            jobid = f"local-{int(datetime.now(UTC).timestamp() * 1000)}"
            store.add(jobid, spec_in, label=label, state="done", outdir=str(outdir_path))

        files = []
        if system_dir.is_dir():
            for entry in sorted(system_dir.iterdir()):
                if entry.is_file():
                    files.append({"name": entry.name, "size": entry.stat().st_size})

        manifest_path = outdir_path / "manifest.json"
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

        # Metriche fisiche post-build (se calcolate)
        metrics_path = system_dir / "metrics.json"
        metrics = json.loads(metrics_path.read_text()) if metrics_path.exists() else None

        return {
            "jobid": jobid,
            "outdir": str(outdir_path),
            "system_dir": str(system_dir),
            "files": files,
            "manifest": manifest,
            "log": (res.stdout + "\n" + res.stderr).strip() or "nessun output",
            "metrics": metrics,
        }

    @app.post("/api/embed-protein")
    def embed_protein_endpoint(payload: EmbedProteinInput) -> dict[str, Any]:
        """Embed di un recettore (PDB ID o file) in una membrana esistente.

        Esegue scripts/embed_protein.py dentro l'ambiente conda `cgui-builder`.
        Se pdb_id è fornito, lo scarica da RCSB/OPM e lo orienta; se pdb_file_name
        è fornito, usa il file caricato. Se membrane_pdb_file_name è fornito,
        cerca di allineare il recettore alla membrana esistente.
        Restituisce il manifest e l'outdir (registrato come job locale).
        """
        script = Path(__file__).resolve().parents[2] / "scripts" / "embed_protein.py"
        if not script.exists():
            raise HTTPException(500, f"script non trovato: {script}")
        base_out = default_results_dir()
        cmd = _python_cmd([str(script), "--output", str(base_out)])
        if payload.pdb_id:
            cmd += ["--pdb", payload.pdb_id]
        if payload.chain:
            cmd += ["--chain", payload.chain]
        # pdb_file_name e membrane_pdb_file_name richiedono che i file siano già
        # salvati nella working dir del server; per ora li passiamo come note.
        # L'embed vero e proprio in membrana esistente richiede uno script
        # dedicato (embed_in_membrane.py) da scrivere: qui si limita a orientare
        # il recettore e preparare la topologia CHARMM36.

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(504, f"embed_protein timeout: {exc}") from exc

        if res.returncode != 0:
            raise HTTPException(502, f"embed_protein fallito: {res.stderr[-800:]}")

        # Cerca l'outdir nell'output
        outdir = None
        for line in res.stdout.splitlines():
            if "Output: " in line:
                outdir = line.split("Output: ", 1)[1].strip()
                break

        if not outdir:
            # Fallback: usa l'ultima directory in results/
            base_out_path = Path(base_out)
            dirs = sorted([d for d in base_out_path.iterdir() if d.is_dir()],
                          key=lambda d: d.stat().st_mtime, reverse=True)
            if dirs:
                outdir = str(dirs[0])

        jobid = f"local-embed-{int(datetime.now(UTC).timestamp())}"
        return {
            "jobid": jobid,
            "outdir": outdir or str(base_out),
            "log": (res.stdout + "\n" + res.stderr).strip() or "nessun output",
        }

    @app.post("/api/upload-pdb")
    def upload_pdb(payload: UploadPDBInput) -> UploadPDBInput:
        """Salva un file PDB caricato dal browser (inviato come testo JSON) in results/uploads/.
        Limite di sicurezza: 10 MB max, nome base senza slash (no path traversal)."""
        if len(payload.content.encode("utf-8")) > 10 * 1024 * 1024:
            raise HTTPException(413, "file troppo grande: max 10 MB")
        uploads_dir = default_results_dir() / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        nome = Path(payload.file_name).name
        if nome != payload.file_name:
            raise HTTPException(400, "nome file non valido: solo nome base, niente cartelle")
        dest = uploads_dir / nome
        dest.write_text(payload.content, encoding="utf-8")
        return UploadPDBInput(file_name=nome, saved_path=str(dest))


    @app.post("/api/embed-in-membrane")
    def embed_in_membrane(payload: EmbedInMembraneInput) -> dict[str, Any]:
        script = Path(__file__).resolve().parents[2] / "scripts" / "embed_in_membrane.py"
        if not script.exists():
            raise HTTPException(500, f"script non trovato: {script}")
        uploads_dir = default_results_dir() / "uploads"

        def _find_pdb(name: str) -> Path:
            # Prima prova in uploads/ (file appena caricati)
            cand = uploads_dir / name
            if cand.exists():
                return cand
            # Poi cerca ricorsivamente in tutta results/ (strutture vecchie)
            base = default_results_dir()
            if base.exists():
                for p in base.rglob(name):
                    if p.is_file():
                        return p
            raise FileNotFoundError(name)

        try:
            membrane_path = _find_pdb(payload.membrane_pdb_file_name)
            receptor_path = _find_pdb(payload.receptor_pdb_file_name)
        except FileNotFoundError as exc:
            raise HTTPException(404, f"file PDB non trovato: {exc}") from None

        out_dir = default_results_dir() / (payload.out_dir or "results")
        cmd = _python_cmd(
            [
                str(script),
                "--membrane-pdb", str(membrane_path),
                "--receptor-pdb", str(receptor_path),
                "--chain", payload.chain or "R",
                "--output", str(out_dir),
            ]
        )

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(504, f"embed_in_membrane timeout: {exc}") from exc

        if res.returncode != 0:
            raise HTTPException(502, f"embed_in_membrane fallito: {res.stderr[-800:]}")

        jobid = f"local-embed-mem-{int(datetime.now(UTC).timestamp())}"
        return {
            "jobid": jobid,
            "outdir": str(out_dir / "system"),
            "log": (res.stdout + "\n" + res.stderr).strip() or "nessun output",
        }

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
