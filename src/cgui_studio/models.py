"""Modelli: specifica di build, stato di un job, record d'archivio.

La mappatura dei parametri verso l'API non è inventata: deriva dalla doc
ufficiale incrociata con due verifiche indipendenti su archivi reali
(vedi SOURCES.md). In particolare:

* ``ion_conc``/``ion_type`` viaggiano in **minuscolo**. I nomi maiuscoli della
  documentazione (``Ion_conc``/``Ion_type``) vengono accettati dal server ma
  **ignorati**: il sistema esce sempre con NaCl 0.15 M. Bug confermato
  ispezionando ``early-step2.2_ions_count.str`` di un job reale.
* ``margin``/``wdist``/``clone_job``/``membrane_only``/``run_ffconverter`` sono
  parametri di **query string**; composizione, ioni e flag vanno nel **form**.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

#: stati in cui il job è chiuso e non ha senso ri-interrogarlo
TERMINAL_STATUS = frozenset({"done", "error"})

#: le biomembrane predefinite accettate da ``membtype``
MEMBRANE_PRESETS = (
    "PMm", "PMf", "PMp", "aPM",
    "ENDm", "ENDf", "ENDp", "aEND",
    "BTm", "BTf", "BTp", "aBT",
    "GMm", "GMf", "GMp", "aGM",
    "YMm", "YMf", "YMp", "aYM",
    "ERm", "ERf", "ERp", "aER",
)  # fmt: skip


def slugify(label: str | None) -> str:
    """Label → nome sicuro per la cartella risultati (nessuna sub-cartella)."""
    if not label:
        return "job"
    slug = re.sub(r"[^\w.\-]+", "_", label.strip()).strip("._")
    return slug or "job"


def format_number(value: float) -> str:
    """Numero senza zeri inutili: 20.0 -> '20', 22.5 -> '22.5', 0.15 -> '0.15'."""
    return f"{value:g}"


def _flag(value: bool) -> str:
    return "true" if value else "false"


class Spec(BaseModel):
    """Cosa costruire. Una volta inviata, sul server diventa un job immutabile."""

    model_config = ConfigDict(extra="forbid", frozen=False)

    label: str | None = Field(default=None, description="nome leggibile del sistema")

    # composizione: o upper+lower, o membtype (mutuamente esclusivi)
    upper: str | None = None
    lower: str | None = None
    membtype: str | None = None

    margin: float = 20.0
    wdist: float | None = None

    ion_conc: float = 0.15
    ion_type: str = "NaCl"

    membrane_only: bool = True
    prot_projection_upper: bool = False
    prot_projection_lower: bool = False
    ppm: bool = False
    topology_in: bool = True
    heteroatoms: bool = False
    run_ffconverter: bool = True

    # riparte dalla struttura di un job precedente (PDB Reader già fatto)
    clone_job_from: str | None = None

    # parametri presenti nel backend ma NON documentati: spediti solo se valorizzati
    temperature: float | None = None
    align_option: int | None = None
    hetero_xy_option: str | None = None
    charmmff_wyf_checked: bool = False
    charmmff_hmr_checked: bool = False
    charmm_mini: bool = False

    @model_validator(mode="after")
    def _check(self) -> Spec:
        has_custom = self.upper is not None and self.lower is not None
        has_preset = self.membtype is not None
        if (self.upper is None) != (self.lower is None):
            raise ValueError("upper e lower vanno indicati entrambi")
        if not has_custom and not has_preset:
            raise ValueError("Nessuna composizione: serve upper+lower oppure membtype")
        if has_custom and has_preset:
            raise ValueError("UNA sola modalità: upper/lower e membtype sono mutuamente esclusivi")
        if self.margin <= 0:
            raise ValueError("margin deve essere > 0")
        if self.ion_conc < 0:
            raise ValueError("ion_conc non può essere negativa")
        if not self.membrane_only and not self.clone_job_from:
            raise ValueError(
                "membrane_only=False richiede clone_job_from: il PDB Reader non ha un "
                "endpoint pubblico, quindi il jobid della proteina va creato prima dal sito"
            )
        if self.membrane_only and self.clone_job_from:
            raise ValueError("membrane_only=True e clone_job_from sono incompatibili")
        return self

    # ------------------------------------------------------------------ mapping
    def to_query(self) -> dict[str, str]:
        """Parametri di query string (posizione verificata sul server)."""
        q: dict[str, str] = {
            "margin": format_number(self.margin),
            "membrane_only": _flag(self.membrane_only),
            "run_ffconverter": _flag(self.run_ffconverter),
        }
        if self.clone_job_from:
            q["clone_job"] = "true"
        if self.wdist is not None:
            q["wdist"] = format_number(self.wdist)
        if self.temperature is not None:
            q["temperature"] = format_number(self.temperature)
        if self.align_option is not None:
            q["align_option"] = str(self.align_option)
        if self.hetero_xy_option:
            q["hetero_xy_option"] = self.hetero_xy_option
        return q

    def to_form(self) -> dict[str, str]:
        """Parametri del body urlencoded. I flag falsi si omettono."""
        f: dict[str, str] = {}
        if self.membtype:
            f["membtype"] = self.membtype
        else:
            f["upper"] = str(self.upper)
            f["lower"] = str(self.lower)
        if self.clone_job_from:
            f["jobid"] = self.clone_job_from
        # minuscolo: i maiuscoli della doc sono ignorati dal server (bug verificato)
        f["ion_conc"] = format_number(self.ion_conc)
        f["ion_type"] = self.ion_type
        for name, value in (
            ("prot_projection_upper", self.prot_projection_upper),
            ("prot_projection_lower", self.prot_projection_lower),
            ("ppm", self.ppm),
            ("topology_in", self.topology_in),
            ("heteroatoms", self.heteroatoms),
            ("charmmff_wyf_checked", self.charmmff_wyf_checked),
            ("charmmff_hmr_checked", self.charmmff_hmr_checked),
            ("charmm_mini", self.charmm_mini),
        ):
            if value:
                f[name] = "true"
        return f

    def digest(self) -> dict[str, Any]:
        """Rappresentazione stabile per il manifest."""
        return self.model_dump(mode="json")


class JobStatus(BaseModel):
    """Risposta di ``/check_status``, tollerante a campi mancanti o nuovi."""

    model_config = ConfigDict(extra="allow")

    jobid: str | None = None
    status: str | None = None
    rank: str | None = None
    rqinfo: str | None = None
    pidfile: str | None = None
    has_tar: bool | None = None
    last_out_file: str | None = None
    last_out_line: int | None = None
    last_out_time: str | None = None
    last_inp_step: int | None = None
    error: str | None = None

    @classmethod
    def from_json(cls, payload: Any, *, jobid: str | None = None) -> JobStatus:
        """Parsa la risposta del server senza mai sollevare."""
        if not isinstance(payload, dict):
            return cls(
                jobid=jobid,
                status=None,
                error=f"risposta non interpretabile dal server: {payload!r:.200}",
            )
        try:
            return cls(
                jobid=payload.get("jobid") or jobid,
                status=payload.get("status"),
                rank=payload.get("rank"),
                rqinfo=payload.get("rqinfo"),
                pidfile=payload.get("pidfile"),
                has_tar=_as_bool(payload.get("hasTarFile", payload.get("has_tar"))),
                last_out_file=payload.get("lastOutFile") or payload.get("last_out_file"),
                last_out_line=_as_int(payload.get("lastOutLine", payload.get("last_out_line"))),
                last_out_time=payload.get("lastOutTime") or payload.get("last_out_time"),
                last_inp_step=_as_int(payload.get("lastInpStep", payload.get("last_inp_step"))),
                error=payload.get("error"),
            )
        except ValidationError:
            # pydantic lax non coerces int/dict → str: una risposta col tipo
            # sbagliato NON deve uccidere un wait/poll (contratto: mai sollevare)
            return cls(
                jobid=jobid,
                status=None,
                error=f"campi non interpretabili dal server: {payload!r:.200}",
            )

    @property
    def is_terminal(self) -> bool:
        return (self.status or "") in TERMINAL_STATUS

    @property
    def rank_position(self) -> int | None:
        """Quanti job ci sono davanti, se il server lo dice."""
        if not self.rank:
            return None
        m = re.search(r"(\d+)\s+job", self.rank)
        return int(m.group(1)) if m else None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _as_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return None


class JobRecord(BaseModel):
    """Riga della tabella ``jobs`` (provenance locale)."""

    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    jobid: str
    label: str | None = None
    spec_json: str
    state: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    outdir: str | None = None
    archive_sha256: str | None = None
    last_step: int | None = None
    rank: str | None = None
    error: str | None = None

    @property
    def spec_dict(self) -> dict[str, Any]:
        """La spec come dizionario (per la GUI e per i manifest)."""
        try:
            data = json.loads(self.spec_json)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}
