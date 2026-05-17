"""Integration test — hits a real (in production) billing-service.

Classified INTEGRATION two ways: (1) path is under tests/integration/, and
(2) httpx is imported and called without a mock.
"""

import httpx

from src.routers.charges import cancel_charge
from src.routers.health import healthz


def test_real_payment_call():
    response = httpx.get("http://billing-service/charges/abc")
    assert response.status_code == 200
    assert cancel_charge is not None


def test_real_payment_create():
    response = httpx.post("http://billing-service/charges", json={"amount": 100})
    assert response.status_code == 201


def test_health_endpoint_handler_exists():
    assert healthz is not None
