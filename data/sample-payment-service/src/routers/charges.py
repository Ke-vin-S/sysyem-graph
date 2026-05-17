"""Charge endpoints. Mounted at /payments by src/main.py."""

import httpx
from fastapi import APIRouter

router = APIRouter()


@router.get("/charges/{id}")
async def get_charge(id: str):
    response = httpx.get(f"http://billing-service/charges/{id}")
    return response.json()


@router.post("/charges")
async def create_charge(amount: int):
    return {"id": "abc", "amount": amount}


@router.delete("/charges/{id}")
async def cancel_charge(id: str):
    return {"id": id, "status": "cancelled"}
