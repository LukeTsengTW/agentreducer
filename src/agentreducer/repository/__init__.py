"""Repository workspace file-presence reduction; no import-time filesystem work."""
from .models import (
    RepositoryFile, RepositoryManifest, RepositoryCandidate, RepositoryReductionResult,
    FixedTextOverlay, ExternalReadIsolation, RepositoryVisibilityEvidence,
)
from .manifest import build_repository_manifest
from .projection import RepositoryProjectionMaterializer
from .reducer import RepositoryReducer
from .reduction import reduce_repository_agent, reduce_repository_stochastic

__all__ = [
    "RepositoryFile", "RepositoryManifest", "RepositoryCandidate", "RepositoryReductionResult",
    "FixedTextOverlay", "ExternalReadIsolation", "RepositoryVisibilityEvidence",
    "build_repository_manifest",
    "RepositoryProjectionMaterializer", "RepositoryReducer",
    "reduce_repository_agent", "reduce_repository_stochastic",
]
