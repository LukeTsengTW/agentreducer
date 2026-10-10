"""Owned finite trial-call budget with atomic, irreversible reservation."""

from threading import Lock

from .models import BudgetSnapshot


class TrialBudget:
    def __init__(self, limit: int):
        if type(limit) is not int:
            raise TypeError("max_total_trials must be a strict integer.")
        if limit <= 0:
            raise ValueError("max_total_trials must be positive.")
        self._limit = limit
        self._used = 0
        self._lock = Lock()

    def _reserve(self) -> bool:
        with self._lock:
            if self._used == self._limit:
                return False
            self._used += 1
            return True

    def snapshot(self) -> BudgetSnapshot:
        with self._lock:
            return BudgetSnapshot(self._limit, self._used)
