"""Repository layer — typical FastAPI pattern that was previously isolated.

Demonstrates three resolver fixes at once:
  * `self.client.post(...)` — `self.<attr>.<method>()` constructor-DI path
  * The whole class is reachable through ChargeService's `self.repo.<m>()`
  * A `Depends(get_db)` site links endpoint -> get_db without ceremony
"""

from src.billing.client import fetch_charge, post_charge


def get_db() -> dict:
    """Stand-in for the FastAPI `get_db` dependency provider.

    The test that this matters: `service: X = Depends(get_db)` must link
    the endpoint to `get_db` so on-call can see "if get_db changes, these
    routes are affected."
    """
    return {"connection": "fake"}


class ChargeRepository:
    def __init__(self, client_factory):
        # `self.client = client_factory()` — module-level/constructor mix.
        # The resolver picks up `self.client` via the call-source heuristic.
        self.client = client_factory()

    def fetch(self, charge_id: str) -> dict:
        # Direct module function call.
        return fetch_charge(charge_id)

    def create(self, amount: int) -> dict:
        return post_charge(amount)
