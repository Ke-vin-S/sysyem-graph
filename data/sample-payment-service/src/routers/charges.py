"""Charge endpoints. Mounted at /payments by src/main.py.

Demonstrates the FastAPI Depends pattern: the service layer is injected as
a typed parameter, and the resolver picks up the controller -> service
edges through that type annotation.
"""

from fastapi import APIRouter, Depends

from src.services.charge_service import ChargeService

router = APIRouter()


@router.get("/charges/{id}")
async def get_charge(id: str, service: ChargeService = Depends(ChargeService)):
    return service.get(id)


@router.post("/charges")
async def create_charge(
    amount: int, service: ChargeService = Depends(ChargeService)
):
    return service.create(amount)


@router.delete("/charges/{id}")
async def cancel_charge(
    id: str, service: ChargeService = Depends(ChargeService)
):
    service.cancel(id)
    return {"id": id, "status": "cancelled"}
