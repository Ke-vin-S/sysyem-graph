"""Smoke tests for the Phase-2 node types."""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from core.types import (
    DataModel,
    DataModelKind,
    EdgeSource,
    Endpoint,
    KafkaConsumer,
    KafkaProducer,
    KafkaTopic,
    LineRange,
    Mock,
    MockKind,
    Query,
    QueryKind,
)


def _line_range() -> LineRange:
    return LineRange(start=1, end=10)


def test_endpoint_minimal_construction() -> None:
    e = Endpoint(
        id="endpoint:repo:GET:/v1/x",
        repoId="repo",
        method="GET",
        path="/v1/x",
        framework="fastapi",
    )
    assert e.id.startswith("endpoint:")
    assert e.handler_artifact_id is None
    assert e.is_public is True


def test_endpoint_alias_round_trip() -> None:
    e = Endpoint(
        id="e1",
        repoId="r",
        method="POST",
        path="/x",
        handlerArtifactId="fn:r:src/x.py:create",
        handlerFile="src/x.py",
        handlerSymbol="create",
    )
    assert e.handler_artifact_id == "fn:r:src/x.py:create"
    assert e.model_dump(by_alias=True)["handlerArtifactId"] == "fn:r:src/x.py:create"


def test_data_model_fields_are_tuple_of_tuples() -> None:
    d = DataModel(
        id="dm:r:src/m.py:Charge",
        repoId="r",
        name="Charge",
        file="src/m.py",
        lineRange=_line_range(),
        kind=DataModelKind.PYDANTIC,
        fields=(("id", "str"), ("amount", "int")),
    )
    assert d.kind is DataModelKind.PYDANTIC
    assert d.fields == (("id", "str"), ("amount", "int"))


def test_query_kind_defaults_to_raw_sql() -> None:
    q = Query(id="q1", repoId="r", file="src/x.py", line=42, expression="SELECT 1")
    assert q.kind is QueryKind.RAW_SQL
    assert q.tables == ()


def test_kafka_topic_cross_repo_id() -> None:
    # Topic ID is global so producer/consumer in different repos join.
    t = KafkaTopic(id="topic:user.events", name="user.events")
    assert t.id == "topic:user.events"


def test_kafka_producer_consumer_topic_reference() -> None:
    p = KafkaProducer(
        id="kp:r:src/p.py:42",
        repoId="r",
        functionArtifactId="fn:r:src/p.py:publish",
        topicName="user.events",
        file="src/p.py",
        line=42,
        framework="kafka-python",
    )
    c = KafkaConsumer(
        id="kc:r:src/c.py:17",
        repoId="r",
        functionArtifactId="fn:r:src/c.py:handle",
        topicName="user.events",
        file="src/c.py",
        line=17,
        framework="kafka-python",
        consumerGroup="billing-group",
    )
    assert p.topic_name == c.topic_name == "user.events"


def test_mock_patch_str_vs_patch_object() -> None:
    m1 = Mock(
        id="mock:r:t1:httpx.get",
        repoId="r",
        testId="t1",
        kind=MockKind.PATCH_STR,
        patchTarget="httpx.get",
        file="tests/a.py",
        line=10,
    )
    m2 = Mock(
        id="mock:r:t1:M.method",
        repoId="r",
        testId="t1",
        kind=MockKind.PATCH_OBJECT,
        patchTarget="src.module.MyClass.method",
        targetArtifactId="method:r:src/module.py:MyClass.method",
        file="tests/a.py",
        line=11,
    )
    assert m1.target_artifact_id is None
    assert m2.target_artifact_id is not None


def test_edge_source_values() -> None:
    assert {s.value for s in EdgeSource} == {"resolver", "llm"}


def test_required_fields_validate() -> None:
    with pytest.raises(PydanticValidationError):
        Endpoint(id="", repoId="r", method="GET", path="/x")  # type: ignore[call-arg]
    with pytest.raises(PydanticValidationError):
        Query(id="q1", repoId="r", file="src/x.py", line=0)  # type: ignore[call-arg]
