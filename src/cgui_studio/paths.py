"""Directory XDG, token in cache e file di stato locale.

Tutto sovrascrivibile con ``CGUI_STUDIO_HOME`` (usato dai test).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

APP = "cgui-studio"

#: oltre questa età il token è considerato sospetto (il JWT vive ~1h)
DEFAULT_TOKEN_MAX_AGE_S = 3600


def _home_override() -> Path | None:
    raw = os.environ.get("CGUI_STUDIO_HOME")
    return Path(raw).expanduser() if raw else None


def config_dir() -> Path:
    """Configurazione (XDG_CONFIG_HOME)."""
    if (ov := _home_override()) is not None:
        return ov / "config"
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / APP


def cache_dir() -> Path:
    """Token e cache volatile (XDG_CACHE_HOME)."""
    if (ov := _home_override()) is not None:
        return ov / "cache"
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base) / APP


def data_dir() -> Path:
    """Database dei job (XDG_DATA_HOME)."""
    if (ov := _home_override()) is not None:
        return ov / "data"
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / APP


def token_path() -> Path:
    """Il token sta nella cache: è volatile, non configurazione."""
    return cache_dir() / "token.json"


def db_path() -> Path:
    return data_dir() / "jobs.db"


def default_results_dir() -> Path:
    """Dove finiscono gli output: ``./results`` nella cwd."""
    if (ov := _home_override()) is not None:
        return ov / "results"
    return Path.cwd() / "results"


def ensure_dirs() -> None:
    for d in (cache_dir(), data_dir()):
        d.mkdir(parents=True, exist_ok=True)
        d.chmod(0o700)


def write_private(path: Path, text: str) -> None:
    """Scrive un file con permessi 0600 (token, segreti)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    path.chmod(0o600)


def save_token(token: str, email: str | None = None) -> Path:
    """Salva il JWT in cache con permessi 0600."""
    ensure_dirs()
    target = token_path()
    write_private(
        target,
        json.dumps(
            {
                "token": token,
                "email": email,
                "saved_at": time.time(),
            },
            indent=2,
        ),
    )
    return target


def load_token() -> dict[str, Any] | None:
    """Token in cache, o None. Mai solleva: la cache può essere corrotta."""
    target = token_path()
    if not target.is_file():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("token"):
        return None
    return data


def token_age_s(cached: dict[str, Any] | None) -> float | None:
    """Età del token in secondi, se ``saved_at`` è noto."""
    if not cached:
        return None
    saved = cached.get("saved_at")
    if isinstance(saved, (int, float)):
        return max(0.0, time.time() - float(saved))
    return None


def clear_token() -> None:
    token_path().unlink(missing_ok=True)
