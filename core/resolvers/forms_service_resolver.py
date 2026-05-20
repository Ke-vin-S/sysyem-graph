"""Emit Service nodes for Oracle Forms applications.

Two sources:

1. **File scan** — `.fmb` / `.fmx` files in the walked tree. The
   `FormsGrammar` already emits one `SYMBOL` fact (`sym_kind="form_app"`)
   per file; this resolver materializes a `Service` record for each.

2. **Config override** — names listed in `ORACLE_FORMS_APPS` (passed in
   as the `extras` argument). Surfaces forms that aren't checked into the
   source tree (e.g. legacy binaries kept in a separate registry).

Service IDs are namespaced to avoid collisions:
  * File-derived: `forms:<repo_id>:<stem>`
  * Config-derived: `forms:config:<name>`

If a name appears in both sources, the file-derived record wins (it has a
real file path attached). The resolver dedupes on `id`.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone

from core.facts import FactKind, FactTree
from core.types import Service


def extract_forms_services(
    tree: FactTree,
    *,
    repo_id: str,
    extras: Iterable[str] = (),
    now: datetime | None = None,
) -> list[Service]:
    """Return one Service per Forms app (file-scanned + config-listed).

    Pass `now` for time-frozen tests; defaults to `datetime.now(UTC)`."""
    timestamp = now or datetime.now(timezone.utc)
    out: list[Service] = []
    seen_ids: set[str] = set()

    for fact in tree.where(kind=FactKind.SYMBOL):
        if fact.data.get("sym_kind") != "form_app":
            continue
        name = str(fact.data.get("name") or "")
        if not name:
            continue
        svc_id = f"forms:{repo_id}:{name}"
        if svc_id in seen_ids:
            continue
        seen_ids.add(svc_id)
        out.append(
            Service(
                id=svc_id,
                name=name,
                repoUrl=fact.file,
                language="oracle_forms",
                framework="oracle_forms",
                owner="unknown",
                createdAt=timestamp,
                lastUpdatedAt=timestamp,
                isActive=True,
            )
        )

    for raw in extras:
        name = raw.strip()
        if not name:
            continue
        svc_id = f"forms:config:{name}"
        if svc_id in seen_ids:
            continue
        seen_ids.add(svc_id)
        out.append(
            Service(
                id=svc_id,
                name=name,
                repoUrl=f"oracle-forms:{name}",
                language="oracle_forms",
                framework="oracle_forms",
                owner="unknown",
                createdAt=timestamp,
                lastUpdatedAt=timestamp,
                isActive=True,
            )
        )
    return out
