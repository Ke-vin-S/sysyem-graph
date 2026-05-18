"""KafkaResolver: detect Kafka producer/consumer call sites.

Driven by per-framework `kafka` patterns. The resolver is language- and
client-library-agnostic; kafka-python, confluent-kafka, spring-kafka,
sarama, kafkajs all plug in by dropping a `kafka:` block in their
framework YAML.

Three outputs per match:
  1. A KafkaTopic node — global id `topic:<name>` so producers in repo A
     and consumers in repo B join through the same node.
  2. A KafkaProducer or KafkaConsumer record, attributed to its
     enclosing function via line range.
  3. The PRODUCES / CONSUMES edge is materialized by GraphLoader from
     the records' `topic_name` field.

v1 handles call-shaped patterns only: `producer.send("topic", ...)`,
`KafkaConsumer("topic", ...)`. Decorator-shaped patterns (Faust
`@app.agent("topic")`) need a small extension — same idea, different
fact kind.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

from core.facts import Fact, FactKind, FactTree
from core.frameworks import EffectiveFramework
from core.frameworks.definition import KafkaPatterns
from core.types import (
    CodeArtifact,
    KafkaConsumer,
    KafkaProducer,
    KafkaTopic,
)


@dataclass
class KafkaResolution:
    topics: list[KafkaTopic]
    producers: list[KafkaProducer]
    consumers: list[KafkaConsumer]


class KafkaResolver:
    def resolve(
        self,
        *,
        tree: FactTree,
        artifacts: Iterable[CodeArtifact],
        frameworks: tuple[EffectiveFramework, ...],
        repo_id: str,
        repo_root: str | None = None,
    ) -> KafkaResolution:
        framework_patterns: list[tuple[str, KafkaPatterns]] = [
            (fw.name, fw.kafka) for fw in frameworks if fw.kafka is not None
        ]
        if not framework_patterns:
            return KafkaResolution(topics=[], producers=[], consumers=[])

        by_file_ranges: dict[str, list[tuple[int, int, CodeArtifact]]] = {}
        for art in artifacts:
            by_file_ranges.setdefault(art.file, []).append(
                (art.line_range.start, art.line_range.end, art)
            )

        topic_names: set[str] = set()
        producers: list[KafkaProducer] = []
        consumers: list[KafkaConsumer] = []
        seen_producer_ids: set[str] = set()
        seen_consumer_ids: set[str] = set()

        # Pass 1: CALL facts (Python producer.send, KafkaConsumer ctor).
        for call in tree.where(kind=FactKind.CALL):
            callee = str(call.data.get("callee", ""))
            method = str(call.data.get("method", ""))
            for fw_name, kp in framework_patterns:
                role = _classify_call(callee, method, kp)
                if role is None:
                    continue
                topic = _extract_topic(call, kp.topic_arg, kp.topic_kwarg)
                if not topic:
                    continue
                call_file = _rel_to(call.file, repo_root) if repo_root else call.file
                enclosing = _enclosing_artifact(by_file_ranges, call_file, call.line)
                if enclosing is None:
                    continue
                topic_names.add(topic)
                if role == "produce":
                    pid = f"kp:{repo_id}:{call_file}:{call.line}"
                    if pid in seen_producer_ids:
                        continue
                    seen_producer_ids.add(pid)
                    producers.append(
                        KafkaProducer(
                            id=pid, repoId=repo_id,
                            functionArtifactId=enclosing.id, topicName=topic,
                            file=call_file, line=call.line, framework=fw_name,
                        )
                    )
                else:  # consume
                    cid = f"kc:{repo_id}:{call_file}:{call.line}"
                    if cid in seen_consumer_ids:
                        continue
                    seen_consumer_ids.add(cid)
                    consumers.append(
                        KafkaConsumer(
                            id=cid, repoId=repo_id,
                            functionArtifactId=enclosing.id, topicName=topic,
                            file=call_file, line=call.line, framework=fw_name,
                        )
                    )
                break  # first matching framework wins

        # Pass 2: ANNOTATION facts (Java `@KafkaListener(topics="user.events")`).
        # Consumer-only — producers in Spring are calls (KafkaTemplate.send).
        for ann in tree.where(kind=FactKind.ANNOTATION):
            callee = str(ann.data.get("callee", ""))
            if ann.data.get("target_kind") != "method":
                continue
            for fw_name, kp in framework_patterns:
                if not kp.consume_annotations:
                    continue
                if callee not in kp.consume_annotations and callee.rsplit(".", 1)[-1] not in set(kp.consume_annotations):
                    continue
                topic = _extract_topic(ann, kp.topic_arg, kp.topic_kwarg)
                if not topic:
                    continue
                ann_file = _rel_to(ann.file, repo_root) if repo_root else ann.file
                enclosing = _enclosing_artifact(by_file_ranges, ann_file, ann.line)
                if enclosing is None:
                    continue
                topic_names.add(topic)
                cid = f"kc:{repo_id}:{ann_file}:{ann.line}"
                if cid in seen_consumer_ids:
                    continue
                seen_consumer_ids.add(cid)
                consumers.append(
                    KafkaConsumer(
                        id=cid, repoId=repo_id,
                        functionArtifactId=enclosing.id, topicName=topic,
                        file=ann_file, line=ann.line, framework=fw_name,
                    )
                )
                break

        topics = [KafkaTopic(id=f"topic:{name}", name=name) for name in sorted(topic_names)]
        return KafkaResolution(topics=topics, producers=producers, consumers=consumers)


def _classify_call(callee: str, method: str, kp: KafkaPatterns) -> str | None:
    """'produce' | 'consume' | None."""
    if kp.produce_callees and callee in kp.produce_callees:
        return "produce"
    if kp.produce_methods and method in kp.produce_methods:
        return "produce"
    if kp.consume_callees:
        # Consumer constructors typically appear as `KafkaConsumer(...)` — the
        # callee is the bare class name (or a dotted path ending in it).
        last = callee.rsplit(".", 1)[-1]
        if callee in kp.consume_callees or last in kp.consume_callees:
            return "consume"
    return None


def _extract_topic(fact: Fact, topic_arg: int, topic_kwarg: str) -> str:
    """Pull the topic name out of a CALL or ANNOTATION fact.

    Try the kwarg path first (`@KafkaListener(topics="x")`), then fall
    back to positional. Returns "" if the value is non-literal or
    missing.
    """
    if topic_kwarg:
        kwargs = fact.data.get("kwargs") or {}
        value = kwargs.get(topic_kwarg)
        if isinstance(value, str) and not (value.startswith("<") and value.endswith(">")):
            return value
    args = fact.data.get("args") or []
    if topic_arg >= len(args):
        return ""
    value = args[topic_arg]
    if not isinstance(value, str):
        return ""
    if value.startswith("<") and value.endswith(">"):
        return ""
    return value


def _enclosing_artifact(
    by_file_ranges: dict[str, list[tuple[int, int, CodeArtifact]]],
    file: str,
    line: int,
) -> CodeArtifact | None:
    ranges = by_file_ranges.get(file, ())
    best: CodeArtifact | None = None
    best_span = float("inf")
    for start, end, art in ranges:
        if start <= line <= end:
            span = end - start
            if span < best_span:
                best_span = span
                best = art
    return best


def _rel_to(file: str, root: str) -> str:
    if not root:
        return file
    fp = PurePosixPath(file.replace("\\", "/"))
    rp = PurePosixPath(root.replace("\\", "/"))
    try:
        return str(fp.relative_to(rp))
    except ValueError:
        parts = fp.parts
        root_name = rp.name
        if root_name in parts:
            idx = parts.index(root_name)
            return str(PurePosixPath(*parts[idx + 1 :]))
        return file
