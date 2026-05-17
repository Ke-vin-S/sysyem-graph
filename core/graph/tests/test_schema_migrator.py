"""Unit tests for schema definitions and the Migrator's bookkeeping."""

from __future__ import annotations

from core.graph.schema import (
    LOOKUP_INDEXES,
    NODE_LABELS,
    RELATIONSHIPS,
    UNIQUENESS_CONSTRAINTS,
)


def test_node_labels_match_domain_types() -> None:
    """Every domain type we emit should have a Neo4j label."""
    assert "Service" in NODE_LABELS
    assert "CodeArtifact" in NODE_LABELS
    assert "TestCase" in NODE_LABELS
    assert "ExternalConnection" in NODE_LABELS


def test_relationships_cover_all_edges_in_loader() -> None:
    """The schema's relationship list must include every edge type
    GraphLoader produces. New edge types must add a RELATIONSHIPS entry."""
    expected = {"CONTAINS", "DEFINES", "COVERS", "INITIATES", "TARGETS", "EXPOSES"}
    assert expected <= set(RELATIONSHIPS)


def test_every_data_node_has_id_uniqueness_constraint() -> None:
    constrained_labels = {c.label for c in UNIQUENESS_CONSTRAINTS}
    for label in ("Service", "CodeArtifact", "TestCase", "ExternalConnection"):
        assert label in constrained_labels, f"missing uniqueness constraint for {label}"


def test_constraint_cypher_uses_if_not_exists() -> None:
    """Constraints must be idempotent — IF NOT EXISTS is required so
    re-running migrations doesn't fail on already-created constraints."""
    for c in UNIQUENESS_CONSTRAINTS:
        assert "IF NOT EXISTS" in c.cypher


def test_index_cypher_uses_if_not_exists() -> None:
    for idx in LOOKUP_INDEXES:
        assert "IF NOT EXISTS" in idx.cypher
