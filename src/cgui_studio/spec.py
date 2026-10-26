"""Caricamento spec TOML: singolo blocco [build] oppure più blocchi [[build]].

Errori con messaggio utile: chiave sconosciuta con suggerimento (difflib), TOML
non valido, file assente, sezione build mancante.
"""

from __future__ import annotations

import difflib
import tomllib
from pathlib import Path

from .client import CguiError
from .models import Spec

__all__ = ["SpecFileError", "load_spec_file"]


class SpecFileError(CguiError):
    """Errore nel file spec TOML."""


def _friendly_validation_error(index: int, exc: Exception) -> str:
    """Converte l'errore pydantic in un messaggio leggibile (con suggerimenti)."""
    from pydantic import ValidationError  # import locale: evita rumore laddove non serve

    if not isinstance(exc, ValidationError):
        return f"build[{index}]: {exc}"
    lines: list[str] = []
    fields = list(Spec.model_fields)
    for err in exc.errors():
        name = err["loc"][-1] if err.get("loc") else "?"
        name_str = str(name)
        if err.get("type") == "extra_forbidden":
            matches = difflib.get_close_matches(name_str, fields, n=1)
            hint = f" — intendevi '{matches[0]}'?" if matches else ""
            lines.append(f"build[{index}]: campo sconosciuto '{name_str}'{hint}")
        else:
            loc = ".".join(str(p) for p in err.get("loc", ()))
            lines.append(
                f"build[{index}]: campo '{loc or name_str}': {err.get('msg', err['type'])}"
            )
    return "; ".join(lines)


def load_spec_file(path: Path) -> list[Spec]:
    """Parsa un file TOML e ritorna la lista di Spec (uno o più blocchi build)."""
    path = Path(path)
    if not path.is_file():
        raise SpecFileError(f"File spec non trovato: {path}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise SpecFileError(f"TOML non valido in {path}: {exc}") from exc

    unknown_top = [k for k in data if k != "build"]
    if unknown_top:
        raise SpecFileError(
            f"Chiavi di primo livello sconosciute: {', '.join(unknown_top)} "
            "(usare la sezione [build] o [[build]])"
        )
    if "build" not in data:
        raise SpecFileError("Il file spec non contiene la sezione [build] (o [[build]]).")

    raw = data["build"]
    blocks = raw if isinstance(raw, list) else [raw]
    specs: list[Spec] = []
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            raise SpecFileError(
                f"build[{index}]: il blocco deve essere una tabella TOML (chiave = ...)"
            )
        try:
            specs.append(Spec(**block))
        except Exception as exc:  # noqa: BLE001 - riconvertito in messaggio leggibile
            raise SpecFileError(_friendly_validation_error(index, exc)) from exc
    return specs
