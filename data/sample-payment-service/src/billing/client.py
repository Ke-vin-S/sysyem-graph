"""Thin billing-service client. Exists so cross-file CALLS edges have
something local to point at — without it, sample-payment-service has no
function-to-function dependencies inside the repo."""

import httpx


def fetch_charge(charge_id: str) -> dict:
    response = httpx.get(f"http://billing-service/charges/{charge_id}")
    return response.json()


def post_charge(amount: int) -> dict:
    response = httpx.post(
        "http://billing-service/charges", json={"amount": amount}
    )
    return response.json()
