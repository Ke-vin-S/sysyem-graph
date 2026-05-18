"""KafkaResolver tests."""

from __future__ import annotations

from core.facts import Fact, FactKind, FactTree
from core.frameworks import EffectiveFramework
from core.frameworks.definition import KafkaPatterns
from core.resolvers import KafkaResolver
from core.types import CodeArtifact, LineRange


def _fn(name: str, *, file: str, start: int, end: int) -> CodeArtifact:
    return CodeArtifact(
        id=f"fn:r:{file}:{name}", repoId="r", type="function", name=name,
        file=file, lineRange=LineRange(start=start, end=end), isPublic=True,
    )


def _call(file: str, line: int, *, callee: str, args: list) -> Fact:
    receiver, _, method = callee.rpartition(".")
    return Fact(
        kind=FactKind.CALL, file=file, line=line, repo_id="r",
        data={"callee": callee, "receiver": receiver, "method": method, "args": args, "kwargs": {}},
    )


def _fw(kp: KafkaPatterns, name: str = "kafka_python") -> EffectiveFramework:
    return EffectiveFramework(
        name=name, language="python", routes=None, tests=None, mocks=None,
        http_clients=None, data_models=None, queries=None, kafka=kp,
    )


def test_send_call_produces_producer_and_topic() -> None:
    fw = _fw(KafkaPatterns(produce_methods=("send",), topic_arg=0))
    caller = _fn("publish", file="src/p.py", start=1, end=20)
    tree = FactTree.from_facts(
        "r", [_call("src/p.py", 10, callee="producer.send", args=["user.events", "<expr>"])]
    )
    out = KafkaResolver().resolve(
        tree=tree, artifacts=[caller], frameworks=(fw,), repo_id="r",
    )
    assert len(out.producers) == 1
    p = out.producers[0]
    assert p.topic_name == "user.events"
    assert p.function_artifact_id == caller.id
    assert len(out.topics) == 1
    assert out.topics[0].id == "topic:user.events"


def test_consumer_constructor_call() -> None:
    fw = _fw(KafkaPatterns(consume_callees=("KafkaConsumer",), topic_arg=0))
    caller = _fn("listen", file="src/c.py", start=1, end=20)
    tree = FactTree.from_facts(
        "r", [_call("src/c.py", 5, callee="KafkaConsumer", args=["orders.created"])]
    )
    out = KafkaResolver().resolve(
        tree=tree, artifacts=[caller], frameworks=(fw,), repo_id="r",
    )
    assert len(out.consumers) == 1
    assert out.consumers[0].topic_name == "orders.created"
    assert {t.name for t in out.topics} == {"orders.created"}


def test_non_literal_topic_is_skipped() -> None:
    fw = _fw(KafkaPatterns(produce_methods=("send",), topic_arg=0))
    caller = _fn("publish", file="src/p.py", start=1, end=20)
    tree = FactTree.from_facts(
        "r", [_call("src/p.py", 10, callee="producer.send", args=["<name:topic_var>"])]
    )
    out = KafkaResolver().resolve(
        tree=tree, artifacts=[caller], frameworks=(fw,), repo_id="r",
    )
    assert out.producers == [] and out.topics == []


def test_module_level_call_has_no_enclosing_and_is_skipped() -> None:
    """Without an enclosing function, no producer record is emitted —
    PRODUCES edges require a CodeArtifact source."""
    fw = _fw(KafkaPatterns(produce_methods=("send",), topic_arg=0))
    tree = FactTree.from_facts(
        "r", [_call("src/p.py", 5, callee="producer.send", args=["t"])]
    )
    out = KafkaResolver().resolve(tree=tree, artifacts=[], frameworks=(fw,), repo_id="r")
    assert out.producers == []


def test_kafka_listener_annotation_consumer() -> None:
    """`@KafkaListener(topics = "user.events")` on a method registers a consumer."""
    fw = _fw(
        KafkaPatterns(consume_annotations=("KafkaListener",), topic_kwarg="topics"),
        name="spring_kafka",
    )
    handler = _fn("onUser", file="src/UserListener.java", start=1, end=10)
    ann = Fact(
        kind=FactKind.ANNOTATION, file="src/UserListener.java", line=5, repo_id="r",
        data={"callee": "KafkaListener", "args": [], "kwargs": {"topics": "user.events"},
              "target_symbol": "onUser", "target_kind": "method",
              "qualified": "org.springframework.kafka.annotation.KafkaListener"},
    )
    tree = FactTree.from_facts("r", [ann])
    out = KafkaResolver().resolve(
        tree=tree, artifacts=[handler], frameworks=(fw,), repo_id="r",
    )
    assert len(out.consumers) == 1
    assert out.consumers[0].topic_name == "user.events"
    assert out.consumers[0].framework == "spring_kafka"
    assert len(out.topics) == 1


def test_topic_dedup_across_calls() -> None:
    fw = _fw(KafkaPatterns(produce_methods=("send",), topic_arg=0))
    caller = _fn("publish", file="src/p.py", start=1, end=20)
    tree = FactTree.from_facts(
        "r",
        [
            _call("src/p.py", 5, callee="producer.send", args=["events"]),
            _call("src/p.py", 6, callee="producer.send", args=["events"]),
        ],
    )
    out = KafkaResolver().resolve(
        tree=tree, artifacts=[caller], frameworks=(fw,), repo_id="r",
    )
    # Two distinct producer call sites, one shared topic.
    assert len(out.producers) == 2
    assert len(out.topics) == 1
