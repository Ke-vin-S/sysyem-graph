"""Tests for `extract_forms_services`."""

from __future__ import annotations

from datetime import datetime, timezone

from core.facts import Fact, FactKind, FactTree
from core.resolvers.forms_service_resolver import extract_forms_services

NOW = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)


def _tree_with_forms(repo_id: str, *names: str) -> FactTree:
    tree = FactTree(repo_id=repo_id)
    for n in names:
        tree.extend([
            Fact(
                kind=FactKind.SYMBOL,
                file=f"forms/{n}.fmb",
                line=1,
                repo_id=repo_id,
                data={
                    "sym_kind": "form_app",
                    "name": n,
                    "enclosing_class": "",
                    "is_async": False,
                    "modifiers": [],
                },
            )
        ])
    return tree


def test_emits_one_service_per_form_fact() -> None:
    tree = _tree_with_forms("billing", "dashboard", "reports")
    services = extract_forms_services(tree, repo_id="billing", now=NOW)
    by_name = {s.name: s for s in services}
    assert set(by_name) == {"dashboard", "reports"}
    assert all(s.language == "oracle_forms" for s in services)
    assert all(s.framework == "oracle_forms" for s in services)


def test_id_is_namespaced_by_repo_id() -> None:
    tree = _tree_with_forms("billing", "dashboard")
    services = extract_forms_services(tree, repo_id="billing", now=NOW)
    assert services[0].id == "forms:billing:dashboard"


def test_extras_add_config_driven_services() -> None:
    tree = _tree_with_forms("billing")  # no file-derived forms
    services = extract_forms_services(
        tree, repo_id="billing", extras=("legacy_orders", "legacy_returns"), now=NOW
    )
    assert {s.name for s in services} == {"legacy_orders", "legacy_returns"}
    assert all(s.id.startswith("forms:config:") for s in services)


def test_file_derived_overrides_collide_safely() -> None:
    """A form with the same stem in two different repos must not collide
    at id-level (they have different repo_ids, so they're different
    services). Within one repo, duplicates of the same form name are deduped."""
    tree = _tree_with_forms("billing", "dashboard", "dashboard")
    services = extract_forms_services(tree, repo_id="billing", now=NOW)
    assert len(services) == 1


def test_extras_dedupe_against_file_forms_via_namespace() -> None:
    """File-derived `dashboard` becomes `forms:billing:dashboard`, while a
    config-listed `dashboard` becomes `forms:config:dashboard` — distinct
    ids by design. Both surface."""
    tree = _tree_with_forms("billing", "dashboard")
    services = extract_forms_services(
        tree, repo_id="billing", extras=("dashboard",), now=NOW
    )
    ids = {s.id for s in services}
    assert ids == {"forms:billing:dashboard", "forms:config:dashboard"}


def test_empty_tree_no_extras_returns_empty() -> None:
    tree = FactTree(repo_id="empty")
    assert extract_forms_services(tree, repo_id="empty", now=NOW) == []


def test_ignores_non_form_app_symbols() -> None:
    tree = FactTree(repo_id="r")
    tree.extend([
        Fact(
            kind=FactKind.SYMBOL,
            file="x.py",
            line=1,
            repo_id="r",
            data={"sym_kind": "function", "name": "f"},
        )
    ])
    assert extract_forms_services(tree, repo_id="r", now=NOW) == []
