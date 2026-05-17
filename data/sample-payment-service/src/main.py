"""FastAPI app entrypoint.

The base API prefix lives here (`root_path="/v1"`). Individual routers are
mounted with their own prefixes via `app.include_router(..., prefix=...)`.
Endpoints are *not* in this file — they're in src/routers/. This is the
cross-file pattern that motivates EndpointResolver.
"""

from fastapi import FastAPI

from .routers import charges, health

app = FastAPI(root_path="/v1")

app.include_router(charges.router, prefix="/payments")
app.include_router(health.router)
