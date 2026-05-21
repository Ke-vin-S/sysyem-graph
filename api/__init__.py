"""system-graph HTTP API.

A thin FastAPI service that exposes the Neo4j-backed impact graph and the
adapter run-state (from the SQLite stores) over JSON. The React UI in
`ui/` is the only consumer today; the API is local-first and has no auth.

The package is intentionally read-mostly: the only writes are report
generation (synthesised in memory, never persisted) — ingestion still
happens via the `sg-ingest` CLI.
"""

from api.main import create_app

__all__ = ["create_app"]
