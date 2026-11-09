"""Controllo aggiornamenti del repo locale (zero dipendenze extra).

La copia distribuita è un clone/zip git: ``git fetch origin`` e confronto
``HEAD`` con ``origin/main``. Su un pacchetto installato (uvx, senza ``.git``)
o senza rete restituisce uno stato degenerato onesto, mai un errore: chi deve
semplicemente usare la GUI non deve sapere nulla di git.

``apply()`` esegue ``git pull --ff-only`` (fallisce pulito se l'utente ha
modifiche locali in conflitto) e ricostruisce la frontend con npm se
``web/dist`` manca — è gitignored, quindi un clone fresco ne è privo.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def _git(*args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _squeeze(msg: str, limit: int = 200) -> str:
    return " ".join(msg.strip().split())[:limit] or "nessun dettaglio"


def status() -> dict[str, Any]:
    """Confronto HEAD vs origin/main (o origin/master). Mai eccezioni."""
    out: dict[str, Any] = {"available": False, "behind": 0, "mode": "ok", "detail": ""}
    if _git("rev-parse", "--is-inside-work-tree").returncode != 0:
        out["mode"] = "no-repo"
        out["detail"] = "Installazione senza repository git: aggiornamento manuale."
        return out
    if _git("remote", "get-url", "origin").returncode != 0:
        out["mode"] = "no-remote"
        out["detail"] = "Nessun remote 'origin': aggiornamento manuale."
        return out
    fetched = _git("fetch", "--quiet", "origin", timeout=60)
    if fetched.returncode != 0:
        out["mode"] = "offline"
        out["detail"] = f"fetch fallito (rete?): {_squeeze(fetched.stderr)}"
        return out
    remote = _git("rev-parse", "--quiet", "--verify", "origin/main")
    if remote.returncode != 0:
        remote = _git("rev-parse", "--quiet", "--verify", "origin/master")
    if remote.returncode != 0:
        out["mode"] = "no-remote-branch"
        out["detail"] = "Remote senza branch main/master."
        return out
    remote_head = remote.stdout.strip()
    local = _git("rev-parse", "HEAD").stdout.strip()
    if local == remote_head:
        out["detail"] = "Aggiornato all'ultima versione."
        return out
    count = _git("rev-list", "--count", f"HEAD..{remote_head}")
    behind = int(count.stdout.strip() or "0")
    if count.returncode != 0 or behind == 0:
        # HEAD divergente (o nessun commit in ritardo): non aggiorniamo da qui
        out["mode"] = "diverged"
        out["detail"] = "Repo in uno stato non aggiornabile in automatico."
        return out
    out["behind"] = behind
    out["available"] = True
    out["detail"] = f"{behind} commit in ritardo rispetto a origin."
    return out


def apply() -> dict[str, Any]:
    """pull --ff-only + rebuild della frontend se web/dist manca."""
    out = apply_pull()
    out["build"] = _rebuild_frontend()
    return out


def apply_pull() -> dict[str, Any]:
    pulled = _git("pull", "--ff-only", "origin", timeout=180)
    out: dict[str, Any] = {"available": False, "behind": 0, "mode": "ok", "detail": ""}
    if pulled.returncode != 0:
        msg = _squeeze(pulled.stderr)
        out["mode"] = "conflict" if ("fast-forward" in msg or "divergent" in msg) else "error"
        out["detail"] = f"Aggiornamento fallito: {msg}"
        return out
    out["detail"] = _squeeze(pulled.stdout) or "Aggiornato."
    return out


def _rebuild_frontend() -> dict[str, Any]:
    web = ROOT / "web"
    if not (web / "package.json").exists():
        return {"done": False, "detail": "Nessuna frontend nel repo."}
    npm = shutil.which("npm")
    if npm is None:
        return {
            "done": False,
            "detail": "npm non trovato: serve Node per costruire la GUI (la vecchia resta valida).",
        }
    if (web / "dist" / "index.html").exists():
        return {
            "done": False,
            "skipped": True,
            "detail": "web/dist presente: build esistente invariata.",
        }
    run = subprocess.run(
        ["npm", "run", "build"],
        cwd=str(web),
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    ok = run.returncode == 0 and (web / "dist" / "index.html").exists()
    return {"done": ok, "detail": _squeeze(run.stdout if ok else run.stderr)}
