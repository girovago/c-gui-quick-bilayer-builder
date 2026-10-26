"""CLI `cgui` (typer). Tutta la logica sta nei moduli core: qui solo orchestrazione."""

from __future__ import annotations

import functools
import getpass
import os
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Annotated, Any

import typer

from . import paths
from . import spec as spec_mod
from .client import ApiError, AuthError, CguiError, Client, JobError, fetch_job
from .models import JobStatus, Spec
from .store import JobStore

app = typer.Typer(
    help="cgui-studio: build Quick Bilayer di CHARMM-GUI in batch, con provenance.",
    no_args_is_help=True,
    add_completion=False,
)

_NON_TERMINAL = ("submitted", "pending", "running")


# ---------------------------------------------------------------------- helpers


def _error(msg: str) -> None:
    typer.secho(f"errore: {msg}", fg=typer.colors.RED, err=True)


def _ok(msg: str) -> None:
    typer.secho(f"ok: {msg}", fg=typer.colors.GREEN)


def _hint(exc: CguiError) -> str | None:
    if isinstance(exc, AuthError):
        return "controlla email/password (l'account si registra su charmm-gui.org)"
    if isinstance(exc, ApiError) and exc.status_code in (401, 403):
        return "token scaduto: esegui `cgui login`"
    if isinstance(exc, JobError):
        return "riprova dopo `cgui status <jobid>`"
    return None


def _die(msg: str, hint: str | None = None) -> None:
    _error(msg)
    if hint:
        typer.secho(f"suggerimento: {hint}", fg=typer.colors.YELLOW, err=True)
    raise typer.Exit(1)


def _guarded(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Messaggi concisi senza traceback, salvo --debug (preso da ctx.obj)."""

    @functools.wraps(fn)
    def wrapper(ctx: typer.Context, *args: Any, **kwargs: Any) -> Any:
        debug = bool((ctx.obj or {}).get("debug"))
        try:
            return fn(ctx, *args, **kwargs)
        except typer.Exit:
            raise
        except KeyboardInterrupt:
            typer.secho("interrotto", err=True)
            raise typer.Exit(130) from None
        except CguiError as exc:
            if debug:
                raise
            _die(str(exc), _hint(exc))
        except Exception as exc:  # noqa: BLE001 - fallback conciso per l'utente
            if debug:
                raise
            _die(f"errore inatteso: {exc}", "riprova con --debug per vedere il traceback")

    return wrapper


def _client(require_token: bool = False) -> Client:
    cached = paths.load_token() or {}
    token = cached.get("token")
    if require_token and not token:
        raise AuthError("Nessun token in cache: esegui prima `cgui login`.")
    return Client(token=token)


def _sync_store(jobid: str, st: JobStatus) -> None:
    with JobStore() as store:
        if store.get_by_jobid(jobid) is None:
            return
        fields: dict[str, Any] = {
            "state": st.status,
            "rank": st.rank,
            "last_step": st.last_inp_step,
        }
        if st.status == "error":
            fields["error"] = st.error or f"stato d'errore ({st.rank or 'senza dettagli'})"
        store.update(jobid, **fields)


def _wait_and_fetch(
    client: Client,
    jobid: str,
    label: str | None,
    spec_json: str | None,
    outdir: Path,
    *,
    timeout_s: float = 3600,
) -> dict[str, Any]:
    def on_update(st: JobStatus) -> None:
        parts = [f"[{st.status or '?'}]"]
        if st.rank:
            parts.append(st.rank)
        if st.last_inp_step is not None:
            parts.append(f"step {st.last_inp_step}")
        typer.echo("  " + " ".join(parts))

    st = client.wait(jobid, timeout_s=timeout_s, on_update=on_update)
    _sync_store(jobid, st)
    if st.status != "done":
        raise JobError(f"Job {jobid} finito con stato '{st.status or '?'}': niente da scaricare.")
    result = fetch_job(client, jobid, outdir, label=label, spec_json=spec_json)
    with JobStore() as store:
        store.update(
            jobid,
            state="done",
            outdir=str(result["dir"]),
            archive_sha256=result["sha256"],
            last_step=st.last_inp_step,
        )
    return result


def _print_fetch_summary(result: dict[str, Any]) -> None:
    folder = Path(result["dir"])
    typer.secho(f"Estratto in {folder / 'system'}", fg=typer.colors.GREEN)
    typer.echo(f"  manifest {folder / 'manifest.json'}")
    typer.echo(f"  sha256   {result['sha256']}")


@app.callback()
def _main(
    ctx: typer.Context,
    debug: Annotated[bool, typer.Option("--debug", help="Traceback completo a schermo")] = False,
) -> None:
    ctx.obj = {"debug": debug}


# --------------------------------------------------------------------- comandi


@app.command("login")
@_guarded
def login_cmd(
    ctx: typer.Context,
    email: Annotated[str | None, typer.Argument(help="Email dell'account CHARMM-GUI")] = None,
) -> None:
    """Login e salvataggio del token in cache (permessi 0600)."""
    email = email or typer.prompt("Email CHARMM-GUI")
    password = os.environ.get("CGUI_PASS") or getpass.getpass("Password CHARMM-GUI: ")
    with _client() as client:
        client.login(email, password)
    _ok(f"Login riuscita — token salvato in {paths.token_path()}")


@app.command("build")
@_guarded
def build_cmd(
    ctx: typer.Context,
    spec_file: Annotated[Path, typer.Argument(help="File TOML con un blocco [build]")],
    wait_flag: Annotated[
        bool, typer.Option("--wait", help="Attende il job e scarica l'archivio")
    ] = False,
    outdir: Annotated[
        Path | None, typer.Option("--outdir", help="Cartella risultati (default: data/results)")
    ] = None,
    timeout: Annotated[float, typer.Option("--timeout", help="Massimo secondi di attesa")] = 3600,
) -> None:
    """Invia UN build da file TOML; con --wait attende e scarica."""
    specs = spec_mod.load_spec_file(spec_file)
    if len(specs) != 1:
        _die(f"{spec_file} contiene {len(specs)} blocchi build: usa `cgui batch`.")
    sp = specs[0]
    dest = outdir or paths.default_results_dir()
    with _client() as client:
        response = client.submit(sp)
        jobid = str(response.get("jobid", ""))
        if not jobid:
            raise ApiError(f"job inviato ma senza jobid nella risposta: {response}")
        _ok(f"Job inviato: {jobid}  (modules={response.get('modules')})")
        with JobStore() as store:
            rec = store.add(jobid, sp, state="submitted")
        if wait_flag:
            result = _wait_and_fetch(
                client, jobid, sp.label, rec.spec_json, dest, timeout_s=timeout
            )
            _print_fetch_summary(result)


@app.command("batch")
@_guarded
def batch_cmd(
    ctx: typer.Context,
    path: Annotated[Path, typer.Argument(help="File TOML con più blocchi [[build]]")],
    wait_flag: Annotated[
        bool, typer.Option("--wait", help="Attende e scarica ogni job inviato")
    ] = False,
    max_parallel: Annotated[
        int, typer.Option("--max-parallel", min=1, max=8, help="Submit/attese in parallelo")
    ] = 2,
    outdir: Annotated[
        Path | None, typer.Option("--outdir", help="Cartella risultati (default: data/results)")
    ] = None,
    timeout: Annotated[float, typer.Option("--timeout", help="Massimo secondi di attesa")] = 3600,
) -> None:
    """Invia più build dallo stesso file TOML."""
    specs = spec_mod.load_spec_file(path)
    if not specs:
        _die(f"Nessun blocco [[build]] in {path}.")
    dest = outdir or paths.default_results_dir()
    typer.echo(f"{len(specs)} build da inviare (parall.={max_parallel}, uscita: {dest})")

    def one(sp: Spec) -> tuple[bool, str, str]:
        try:
            with _client() as client:
                response = client.submit(sp)
                jobid = str(response.get("jobid", ""))
                if not jobid:
                    raise ApiError(f"job inviato ma senza jobid nella risposta: {response}")
                with JobStore() as store:
                    rec = store.add(jobid, sp, state="submitted")
            typer.echo(f"  inviato: {jobid}  ({sp.label or 'no-label'})")
            if wait_flag and jobid:
                with _client(require_token=True) as client2:
                    _wait_and_fetch(
                        client2, jobid, sp.label, rec.spec_json, dest, timeout_s=timeout
                    )
            return True, jobid, ""
        except CguiError as exc:
            return False, "-", str(exc)

    failures = 0
    with ThreadPoolExecutor(max_workers=max_parallel) as pool:
        futures = [pool.submit(one, sp) for sp in specs]
        for fut in as_completed(futures):
            ok, jobid, err = fut.result()
            if not ok:
                failures += 1
                _error(err)
    if failures:
        _error(f"{failures}/{len(specs)} build falliti.")
        raise typer.Exit(1)
    _ok("Batch completato.")


@app.command("status")
@_guarded
def status_cmd(
    ctx: typer.Context,
    jobid: Annotated[str | None, typer.Argument(help="ID del job")] = None,
    all_flag: Annotated[
        bool, typer.Option("--all", help="Aggiorna live tutti i job non ancora conclusi")
    ] = False,
) -> None:
    """Stato live di un job (o di tutti i job in corso con --all)."""
    if not jobid and not all_flag:
        _die("Serve JOBID oppure --all.")
    if jobid:
        with _client(require_token=True) as client:
            st = client.status(jobid)
        _sync_store(jobid, st)
        parts = [f"job {st.jobid or jobid}", f"status={st.status or '?'}"]
        if st.rank:
            pos = st.rank_position
            parts.append(f"rank={st.rank}" + (f" (n°{pos})" if pos is not None else ""))
        if st.last_inp_step is not None:
            parts.append(f"step={st.last_inp_step}")
        if st.has_tar is not None:
            parts.append(f"hasTar={st.has_tar}")
        typer.echo("  ".join(parts))
        if st.error:
            typer.secho(f"  errore: {st.error}", fg=typer.colors.RED)
        return
    with JobStore() as store:
        open_jobs = [j for j in store.list_jobs(limit=100) if (j.state or "") in _NON_TERMINAL]
    if not open_jobs:
        typer.echo("Nessun job in corso (tutti chiusi: vedi `cgui ls`).")
        return
    exit_code = 0
    for rec in open_jobs:
        try:
            with _client(require_token=True) as client:
                st = client.status(rec.jobid)
                _sync_store(rec.jobid, st)
                typer.echo(f"  {rec.jobid}  status={st.status or '?'}  {st.rank or ''}")
        except CguiError as exc:
            _error(f"{rec.jobid}: {exc}")
            exit_code = 1
    if exit_code:
        raise typer.Exit(exit_code)


@app.command("wait")
@_guarded
def wait_cmd(
    ctx: typer.Context,
    jobid: Annotated[str, typer.Argument(help="ID del job da attendere e scaricare")],
    outdir: Annotated[
        Path | None, typer.Option("--outdir", help="Cartella risultati (default: data/results)")
    ] = None,
    timeout: Annotated[float, typer.Option("--timeout", help="Massimo secondi di attesa")] = 3600,
) -> None:
    """Attende la fine di un job GIÀ inviato, poi scarica ed estrae (come build --wait)."""
    dest = outdir or paths.default_results_dir()
    with JobStore() as store:
        rec = store.get_by_jobid(jobid)
    with _client(require_token=True) as client:
        result = _wait_and_fetch(
            client,
            jobid,
            rec.label if rec else None,
            rec.spec_json if rec else None,
            dest,
            timeout_s=timeout,
        )
    _print_fetch_summary(result)


@app.command("fetch")
@_guarded
def fetch_cmd(
    ctx: typer.Context,
    jobid: Annotated[str, typer.Argument(help="ID del job da scaricare")],
    outdir: Annotated[
        Path | None, typer.Option("--outdir", help="Cartella risultati (default: data/results)")
    ] = None,
) -> None:
    """Scarica + verifica + estrae il tar e scrive manifest.json di provenance."""
    dest = outdir or paths.default_results_dir()
    with JobStore() as store:
        rec = store.get_by_jobid(jobid)
    with _client(require_token=True) as client:
        result = fetch_job(
            client,
            jobid,
            dest,
            label=rec.label if rec else None,
            spec_json=rec.spec_json if rec else None,
        )
    with JobStore() as store:
        store.update(
            jobid,
            state="done",
            outdir=str(result["dir"]),
            archive_sha256=result["sha256"],
            last_step=result["status"].last_inp_step,
        )
    _print_fetch_summary(result)


@app.command("ls")
@_guarded
def ls_cmd(ctx: typer.Context) -> None:
    """Elenca i job registrati nell'archivio locale."""
    with JobStore() as store:
        jobs = store.list_jobs(limit=50)
    if not jobs:
        typer.echo("Archivio vuoto: usa `cgui build` o `cgui batch`.")
        return
    typer.echo(f"{'JOBID':<14} {'STATO':<10} {'LABEL':<24} AGGIORNATO")
    for job in jobs:
        typer.echo(
            f"{job.jobid:<14} {(job.state or '-'):<10} {(job.label or '-'):<24} "
            f"{(job.updated_at or '-'):<19}"
        )


@app.command("serve")
@_guarded
def serve_cmd(
    ctx: typer.Context,
    host: Annotated[
        str, typer.Option("--host", help="Interfaccia di ascolto (default: localhost)")
    ] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Porta della GUI web")] = 8756,
) -> None:
    """GUI web locale: wizard Quick Bilayer + vista 3D NGL + job live.

    Con CGUI_MOCK=1 gira senza token né rete (server finto locale).
    """
    from . import webui

    if os.environ.get("CGUI_MOCK"):
        typer.secho("MOCK attivo: nessuna chiamata reale a charmm-gui.org", fg=typer.colors.CYAN)
    typer.echo(f"GUI su http://{host}:{port}")
    # Sono solo una rilevazione preventiva (TOCTOU possibile ma raro):
    # se la porta si libera/occupa nel mezzo vale il secondo controllo.
    import socket as _socket

    with _socket.socket() as probe:
        probe.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            _die(f"porta {port} occupata: usa --port NNNN")
    try:
        webui.run(host=host, port=port)
    except OSError as exc:
        _die(f"porta {port} occupata: usa --port NNNN", str(exc))


@app.command("doctor")
@_guarded
def doctor_cmd(ctx: typer.Context) -> None:
    """Diagnostica ambiente: Python, dir XDG, token in cache, raggiungibilità API."""
    problems = 0

    def row(ok: bool, text: str, is_problem: bool = True, hint: str | None = None) -> None:
        nonlocal problems
        if ok:
            _ok(text)
            return
        if is_problem:
            problems += 1
        _error(text)
        if hint:
            typer.echo(f"  suggerimento: {hint}")

    ver = sys.version_info
    row(
        (ver.major, ver.minor) >= (3, 11),
        f"Python {ver.major}.{ver.minor}.{ver.micro} su {sys.executable}",
        hint="serve Python >= 3.11 (uv è configurato per 3.12)",
    )
    for name, d in (("cache", paths.cache_dir()), ("data", paths.data_dir())):
        try:
            d.mkdir(parents=True, exist_ok=True)
            probe = d / ".write-probe-cgui"
            probe.write_text("x", encoding="utf-8")
            probe.unlink(missing_ok=True)
            _ok(f"{name}: {d} scrivibile")
        except OSError as exc:
            row(False, f"{name}: {d} NON scrivibile ({exc})")

    cached = paths.load_token()
    age = paths.token_age_s(cached)
    if cached:
        fresh = age is not None and age <= paths.DEFAULT_TOKEN_MAX_AGE_S
        who = cached.get("email") or "?"
        row(
            fresh,
            f"token in cache ({who}, {_fmt_age(age)})",
            is_problem=False,
            hint="token probabilmente scaduto: esegui `cgui login`",
        )
    else:
        row(False, "Nessun token in cache", hint="esegui `cgui login`")

    code = Client.ping()
    if code is None:
        row(False, "API charmm-gui.org NON raggiungibile", hint="controlla la connessione di rete")
    else:
        _ok(
            f"API charmm-gui.org raggiungibile (HTTP {code}; "
            "401 atteso: vuol dire vivo, ma senza token)"
        )

    if problems:
        raise typer.Exit(1)
    _ok("Doctor: ambiente ok.")


def _fmt_age(seconds: float | None) -> str:
    if seconds is None:
        return "età sconosciuta"
    if seconds < 90:
        return f"{seconds:.0f}s fa"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min fa"
    return f"{seconds / 3600:.1f}h fa"


if __name__ == "__main__":  # pragma: no cover
    app()
