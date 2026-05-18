"""ConfigBindingResolver tests."""

from __future__ import annotations

from datetime import datetime, timezone

from core.facts import Fact, FactKind, FactTree
from core.resolvers import ConfigBindingResolver

NOW = datetime(2026, 5, 18, tzinfo=timezone.utc)


def _cfg(key: str, value: str, file: str = "config/app.yaml") -> Fact:
    return Fact(
        kind=FactKind.CONFIG_VALUE, file=file, line=1, repo_id="r",
        data={"key": key, "value": value, "format": "yaml"},
    )


def test_http_url_with_url_key_emits_connection() -> None:
    tree = FactTree.from_facts(
        "r", [_cfg("billing.base_url", "http://billing-service")]
    )
    out = ConfigBindingResolver().resolve(
        tree=tree, repo_id="r", source_service_id="payments", now=NOW,
    )
    assert len(out.connections) == 1
    c = out.connections[0]
    assert c.target_name == "billing-service"
    assert c.protocol == "http"
    assert c.source_service_id == "payments"
    assert c.data_flow["source"] == "config"
    assert c.data_flow["config_key"] == "billing.base_url"


def test_host_port_form_emits_connection() -> None:
    tree = FactTree.from_facts(
        "r", [_cfg("kafka.bootstrap.servers", "kafka-broker.prod:9092")]
    )
    out = ConfigBindingResolver().resolve(
        tree=tree, repo_id="r", source_service_id="payments", now=NOW,
    )
    assert len(out.connections) == 1
    c = out.connections[0]
    assert c.target_name == "kafka-broker.prod"
    assert c.endpoint == "kafka-broker.prod:9092"


def test_uninteresting_key_skipped_even_for_url_value() -> None:
    """A `description` field that happens to contain a URL shouldn't
    register as a service dependency."""
    tree = FactTree.from_facts(
        "r", [_cfg("app.description", "see http://docs.internal for help")]
    )
    out = ConfigBindingResolver().resolve(
        tree=tree, repo_id="r", source_service_id="payments", now=NOW,
    )
    assert out.connections == []


def test_non_url_value_under_interesting_key_skipped() -> None:
    tree = FactTree.from_facts(
        "r", [_cfg("billing.timeout_seconds", "5")]
    )
    out = ConfigBindingResolver().resolve(
        tree=tree, repo_id="r", source_service_id="payments", now=NOW,
    )
    assert out.connections == []


def test_dedup_across_duplicate_facts() -> None:
    tree = FactTree.from_facts(
        "r",
        [
            _cfg("billing.url", "http://billing-service"),
            _cfg("billing.url", "http://billing-service"),
        ],
    )
    out = ConfigBindingResolver().resolve(
        tree=tree, repo_id="r", source_service_id="payments", now=NOW,
    )
    assert len(out.connections) == 1


def test_jdbc_style_url_resolves_hostname() -> None:
    tree = FactTree.from_facts(
        "r", [_cfg("datasource.url", "jdbc:postgresql://db-prod:5432/charges")]
    )
    out = ConfigBindingResolver().resolve(
        tree=tree, repo_id="r", source_service_id="payments", now=NOW,
    )
    # jdbc:postgresql:// — urlparse should pick up `db-prod` as host
    assert len(out.connections) == 1
    assert out.connections[0].target_name == "db-prod"
