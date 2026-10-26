"""cgui-studio: core CLI sopra l'API Quick Bilayer di CHARMM-GUI (batch + provenance)."""

from .client import ApiError, AuthError, CguiError, Client, JobError, fetch_job, sha256_file
from .models import TERMINAL_STATUS, JobRecord, JobStatus, Spec
from .store import JobStore

__version__ = "0.1.0"

__all__ = [
    "ApiError",
    "AuthError",
    "Client",
    "CguiError",
    "JobError",
    "JobRecord",
    "JobStatus",
    "JobStore",
    "Spec",
    "TERMINAL_STATUS",
    "__version__",
    "fetch_job",
    "sha256_file",
]
