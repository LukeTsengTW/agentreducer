"""Counted composition with the unchanged M0 sequence reducer; no hidden trials."""
from .._counting import CountingOracle
from ..reducers import ddmin
from .models import (
    ExternalReadIsolation, RepositoryCandidate, RepositoryReductionResult,
    RepositoryVisibilityEvidence,
)
from .projection import RepositoryProjectionMaterializer


class RepositoryReducer:
    def reduce(self, candidate, oracle, *, projection=None):
        if not isinstance(candidate, RepositoryCandidate):
            raise TypeError("Repository reduction requires RepositoryCandidate.")
        if projection is not None:
            if not isinstance(projection, RepositoryProjectionMaterializer):
                raise TypeError("Projection observation requires the configured materializer.")
            projection._candidate(candidate)
        all_verified = projection is not None

        def evaluate(paths):
            nonlocal all_verified
            trial_candidate = RepositoryCandidate(candidate.manifest, tuple(paths))
            if projection is None:
                return oracle.evaluate(trial_candidate)
            with projection._observe(trial_candidate) as scope:
                outcome = oracle.evaluate(trial_candidate)
            all_verified = all_verified and scope.verified
            return outcome

        counted = CountingOracle(evaluate)
        reduced = ddmin(candidate.files, counted)
        visibility = RepositoryVisibilityEvidence(
            all_verified,
            ExternalReadIsolation.UNKNOWN if projection is None else ExternalReadIsolation.NOT_GUARANTEED,
        )
        return RepositoryReductionResult(candidate, candidate.manifest.candidate(reduced),
                                         counted.evaluations, visibility)

