"""Service layer between the FastAPI router and the billing/storage modules.

Exists primarily to exercise the call graph through a Depends-injected
parameter: the router declares `service: ChargeService = Depends(ChargeService)`
and then calls `service.get(...)`. The resolver should resolve those calls
through the param's type annotation to the ChargeService methods below.
"""

from src.billing.client import fetch_charge, post_charge
from src.billing.store import get_charge_row, record_payment


class ChargeService:
    def get(self, charge_id: str) -> dict:
        # Direct same-module function call — already resolves today.
        return fetch_charge(charge_id)

    def create(self, amount: int) -> dict:
        return post_charge(amount)

    def cancel(self, charge_id: str) -> None:
        # Same-class dispatch via self — exercises the self.method() fix.
        self._record_cancellation(charge_id)

    def _record_cancellation(self, charge_id: str) -> None:
        record_payment(None, charge_id, 0)
