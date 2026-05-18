"""Charge endpoints. Mounted at /payments by src/main.py."""

from fastapi import APIRouter

from src.billing.client import fetch_charge, post_charge

router = APIRouter()


@router.get("/charges/{id}")
async def get_charge(id: str):
    return fetch_charge(id)


@router.post("/charges")
async def create_charge(amount: int):
    return post_charge(amount)


@router.delete("/charges/{id}")
async def cancel_charge(id: str):
    return {"id": id, "status": "cancelled"}
