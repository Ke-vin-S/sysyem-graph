"""Service layer between the FastAPI router and the repository.

Demonstrates the resolver's new patterns:
  * `self.repo.fetch(...)` — `self.<attr>.<method>()` resolves through
    __init__'s `repo: ChargeRepository` annotation.
  * `self._record_cancellation()` — same-class self-dispatch.
"""

from src.repos.charge_repo import ChargeRepository


class ChargeService:
    def __init__(self, repo: ChargeRepository):
        self.repo = repo

    def get(self, charge_id: str) -> dict:
        # `self.repo.fetch` -> ChargeRepository.fetch
        return self.repo.fetch(charge_id)

    def create(self, amount: int) -> dict:
        return self.repo.create(amount)

    def cancel(self, charge_id: str) -> None:
        self._record_cancellation(charge_id)

    def _record_cancellation(self, charge_id: str) -> None:
        # No-op stand-in.
        return None
