"""Charge endpoints. Mounted at /payments by src/main.py.

Demonstrates three patterns the new resolver handles:
  * Depends-injected service: `service: ChargeService = Depends(ChargeService)`
    resolves `service.get(...)` -> `ChargeService.get`.
  * Depends-injected dependency function: `db: dict = Depends(get_db)`
    creates an `endpoint -> get_db` edge so `get_db` is no longer isolated.
  * `service.method()` inside the handler reaches into the service layer.
"""

from fastapi import APIRouter, Depends

from src.repos.charge_repo import get_db
from src.services.charge_service import ChargeService

router = APIRouter()


@router.get("/charges/{id}")
async def get_charge(
    id: str,
    service: ChargeService = Depends(ChargeService),
    db: dict = Depends(get_db),
):
    return service.get(id)


@router.post("/charges")
async def create_charge(
    amount: int,
    service: ChargeService = Depends(ChargeService),
):
    return service.create(amount)


@router.delete("/charges/{id}")
async def cancel_charge(
    id: str,
    service: ChargeService = Depends(ChargeService),
):
    service.cancel(id)
    return {"id": id, "status": "cancelled"}
