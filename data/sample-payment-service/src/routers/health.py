"""Health endpoint. No prefix on this router so the path stays simple."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz")
def healthz():
    return {"status": "ok"}
