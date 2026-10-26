"""Client HTTP per l'API REST di CHARMM-GUI.

Comportamenti derivati da verifiche reali (vedi SOURCES.md), non da assunzioni:

* il login con credenziali sbagliate risponde **404** con body testuale
  ``No user or incorrect password!`` (non 401, non JSON);
* una richiesta senza token risponde **401** con ``{"error":"No token provided"}``
  ma ``Content-Type: text/html``: non ci si può fidare del MIME;
* il token JWT scade in circa un'ora: il client non lo rinnova da solo (servono
  le credenziali), ma ``cgui login`` lo rifà in due secondi;
* l'archivio scaricato può avere **byte ASCII di lunghezza accodati** dopo lo
  stream gzip: ``archive.normalize`` li gestisce.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from . import archive, paths
from .archive import sha256_file
from .models import JobStatus, Spec

BASE_URL = "https://charmm-gui.org/api"
RETRY_STATUS = {500, 502, 503, 504}

__all__ = [
    "BASE_URL",
    "ApiError",
    "AuthError",
    "CguiError",
    "Client",
    "JobError",
    "fetch_job",
    "safe_extract",
    "sha256_file",
]


class CguiError(RuntimeError):
    """Errore generico di cgui-studio."""


class ApiError(CguiError):
    """Risposta HTTP inattesa."""

    def __init__(self, message: str, status_code: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class AuthError(ApiError):
    """Login fallito o token assente/scaduto."""

    def __init__(self, message: str, status_code: int | None = None, body: str = "") -> None:
        super().__init__(message, status_code, body)


class JobError(CguiError):
    """Il job non è pronto, è finito male o l'archivio non c'è."""


def safe_extract(archive_path: Path, target: Path) -> list[str]:
    """Estrae un archivio scaricato rifiutando path traversal e link sospetti."""
    try:
        return archive.safe_extract_file(archive_path, target)
    except archive.ArchiveError as exc:
        raise CguiError(str(exc)) from exc


class Client:
    """Client sincrono, con retry solo sugli errori transitori."""

    def __init__(
        self,
        base_url: str = BASE_URL,
        *,
        timeout: float = 120.0,
        max_retries: int = 3,
        http: httpx.Client | None = None,
        token: str | None = None,
        email: str | None = None,
        password: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self._http = http or httpx.Client(timeout=timeout, follow_redirects=False)
        self._owns_http = http is None
        self._token = token
        self.email = email
        self.password = password

    # --------------------------------------------------------------- lifecycle
    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------- token
    def token(self) -> str:
        """Token corrente (argomento o cache). Non rinnova: serve ``login()``."""
        if self._token:
            return self._token
        cached = paths.load_token()
        if cached and cached.get("token"):
            self._token = str(cached["token"])
            return self._token
        raise AuthError("Nessun token in cache: esegui prima `cgui login`.")

    def login(self, email: str | None = None, password: str | None = None) -> str:
        """Autentica e mette il token in cache (0600). Ritorna il JWT."""
        email = email or self.email
        password = password or self.password
        if not email or not password:
            raise AuthError("servono email e password per il login")

        resp = self._request(
            "POST", "/login", json={"email": email, "password": password}, auth=False
        )
        if resp.status_code != 200:
            body = resp.text.strip()[:300]
            raise AuthError(
                f"login rifiutato (HTTP {resp.status_code}): {body}", resp.status_code, body
            )
        try:
            payload = resp.json()
        except json.JSONDecodeError as exc:
            raise ApiError(f"risposta di login non JSON: {resp.text[:200]}") from exc
        token = payload.get("token") if isinstance(payload, dict) else None
        if not token:
            raise ApiError(f"nessun campo token nella risposta di login: {payload}")

        self._token = str(token)
        self.email = email
        paths.save_token(self._token, email)
        return self._token

    # -------------------------------------------------------------------- HTTP
    def _request(
        self, method: str, path: str, *, auth: bool = True, **kwargs: Any
    ) -> httpx.Response:
        url = f"{self.base_url}{path}"
        headers = dict(kwargs.pop("headers", {}) or {})
        if auth:
            headers["Authorization"] = f"Bearer {self.token()}"

        last_error: str = "errore sconosciuto"
        for attempt in range(max(1, self.max_retries)):
            try:
                resp = self._http.request(method, url, headers=headers, **kwargs)
            except httpx.HTTPError as exc:  # rete
                last_error = f"rete non raggiungibile ({url}): {exc}"
            else:
                if resp.status_code in RETRY_STATUS:
                    last_error = f"HTTP {resp.status_code} da {url}: {resp.text[:200]}"
                elif resp.status_code == 401:
                    body = resp.text[:300]
                    raise ApiError(f"token rifiutato (HTTP 401): {body}", 401, body)
                else:
                    return resp
            if attempt < self.max_retries - 1:
                time.sleep(0.5 * (2**attempt))
        raise ApiError(last_error)

    # ------------------------------------------------------------------- azioni
    def submit(self, spec: Spec) -> dict[str, Any]:
        """Invia un Quick Bilayer e restituisce la risposta grezza del server."""
        resp = self._request(
            "POST",
            "/quick_bilayer",
            params=spec.to_query(),
            data=spec.to_form(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code != 200:
            raise ApiError(
                f"submit fallito (HTTP {resp.status_code}): {resp.text[:400]}",
                resp.status_code,
                resp.text,
            )
        try:
            payload = resp.json()
        except json.JSONDecodeError as exc:
            raise ApiError(f"submit: risposta non JSON: {resp.text[:200]}") from exc
        if str(payload.get("submitted", "")).lower() not in {"true", "1"}:
            raise ApiError(f"job rifiutato dal server: {payload}")
        return payload

    def status(self, jobid: str) -> JobStatus:
        resp = self._request("GET", "/check_status", params={"jobid": jobid})
        if resp.status_code != 200:
            raise ApiError(
                f"status fallito (HTTP {resp.status_code}): {resp.text[:300]}",
                resp.status_code,
                resp.text,
            )
        try:
            payload = resp.json()
        except json.JSONDecodeError as exc:
            raise ApiError(f"status: risposta non JSON: {resp.text[:200]}") from exc
        return JobStatus.from_json(payload, jobid=jobid)

    def download(self, jobid: str, dest: Path) -> Path:
        """Scarica l'archivio del job in ``dest`` (grezzo, coda ASCII inclusa)."""
        dest = Path(dest)
        resp = self._request("GET", "/download", params={"jobid": jobid}, timeout=600.0)
        if resp.status_code != 200:
            raise JobError(
                f"download fallito (HTTP {resp.status_code}) per il job {jobid}: {resp.text[:200]}"
            )
        content = resp.content
        if not content.startswith(b"\x1f\x8b"):
            raise JobError(
                f"il download non è un gzip (probabile pagina di errore HTML): {content[:120]!r}"
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        return dest

    def wait(
        self,
        jobid: str,
        *,
        timeout_s: float = 3600,
        interval_s: float = 20,
        on_update: Callable[[JobStatus], None] | None = None,
    ) -> JobStatus:
        """Attende la fine del job con backoff leggero (20s → 30s → ... → 60s)."""
        start = time.monotonic()
        delay = interval_s
        last: JobStatus | None = None
        while True:
            st = self.status(jobid)
            changed = last is None or st.status != last.status or st.rank != last.rank
            if on_update and changed:
                on_update(st)
            last = st
            if st.status == "done":
                return st
            if st.status == "error":
                raise JobError(
                    f"job {jobid} terminato in errore ({st.last_out_file or '?'}): "
                    f"{(st.last_out_line or st.error or '')}"
                )
            if time.monotonic() - start > timeout_s:
                raise JobError(f"Timeout dopo {timeout_s:g}s (ultimo stato: {st.status or '?'})")
            time.sleep(delay)
            delay = min(delay * 1.5, 60)

    # -------------------------------------------------------------------- probe
    @staticmethod
    def ping(timeout: float = 10.0) -> int | None:
        """HTTP code di ``check_status`` senza token (401 = API viva). None se giù."""
        try:
            with httpx.Client(timeout=timeout, follow_redirects=False) as http:
                resp = http.get(f"{BASE_URL}/check_status", params={"jobid": "0"})
                return resp.status_code
        except httpx.HTTPError:
            return None


def fetch_job(
    client: Client,
    jobid: str,
    outdir: Path,
    *,
    label: str | None = None,
    spec_json: str | None = None,
) -> dict[str, Any]:
    """Scarica, verifica ed estrae il job; scrive ``manifest.json`` di provenance."""
    outdir = Path(outdir)
    st = client.status(jobid)
    if st.status != "done":
        raise JobError(
            f"Job {jobid} non è pronto (stato '{st.status or '?'}'): niente da scaricare."
        )
    if not st.has_tar:
        raise JobError(
            f"Job {jobid} è 'done' ma il server non espone l'archivio "
            "(hasTarFile=False): riprova più tardi."
        )

    stamp = date.today().isoformat()
    folder = outdir / f"{stamp}_{label or 'job'}_{jobid}"
    system_dir = folder / "system"
    tar_path = folder / f"job_{jobid}.tar.gz"

    client.download(jobid, tar_path)
    digest = sha256_file(tar_path)
    files = safe_extract(tar_path, system_dir)

    spec: Any = None
    if spec_json:
        try:
            spec = json.loads(spec_json)
        except json.JSONDecodeError:
            spec = spec_json

    manifest = {
        "tool": "cgui-studio",
        "created": f"{stamp}T00:00:00Z",
        "jobid": jobid,
        "label": label,
        "spec": spec,
        "status": st.model_dump(mode="json"),
        "archive": {
            "path": tar_path.name,
            "sha256": digest,
            "bytes": tar_path.stat().st_size,
        },
        "files": sorted(files),
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return {
        "dir": folder,
        "system_dir": system_dir,
        "tar": tar_path,
        "sha256": digest,
        "status": st,
        "files": sorted(files),
    }
