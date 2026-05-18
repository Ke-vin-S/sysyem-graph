"""Phase-2 graph node types: Endpoint, DataModel, Query, Kafka{Topic,Producer,Consumer}, Mock.

Each type is its own Pydantic model (rather than overloading `CodeArtifact`)
so property shapes stay clean and Neo4j labels stay distinct. They all share
the same `_Frozen` config as the Phase-1 types (immutable, strict, alias-friendly).

Edge writing lives in `core/graph/loader.py`; these types only describe nodes.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from core.types.service import LineRange, _Frozen


# ---- enums --------------------------------------------------------------


class DataModelKind(StrEnum):
    PYDANTIC = "pydantic"
    PYDANTIC_SETTINGS = "pydantic_settings"
    DATACLASS = "dataclass"
    SQLALCHEMY_ORM = "sqlalchemy_orm"
    JPA_ENTITY = "jpa_entity"
    LOMBOK = "lombok"
    UNKNOWN = "unknown"


class QueryKind(StrEnum):
    RAW_SQL = "raw_sql"
    ORM_CALL = "orm_call"
    JPQL = "jpql"
    NAMED_QUERY = "named_query"


class MockKind(StrEnum):
    PATCH_STR = "patch_str"
    """The patch target is a literal dotted string: @patch("httpx.get")."""

    PATCH_OBJECT = "patch_object"
    """patch.object(SomeClass, "method") — target is a class reference + attr."""


class EdgeSource(StrEnum):
    """Provenance for any edge written to Neo4j."""

    RESOLVER = "resolver"
    """Deterministic, derived from facts. Default for everything the resolvers emit."""

    LLM = "llm"
    """Best-effort, AI-suggested. Tagged so queries can filter to hard edges."""


# ---- nodes --------------------------------------------------------------


class Endpoint(_Frozen):
    """An HTTP/RPC endpoint exposed by a service.

    Promoted out of `CodeArtifact(type="endpoint")` so the edge fan-out
    (HANDLED_BY, EXPOSES, BOUND_TO) lives on a distinct label.
    """

    id: str = Field(min_length=1)
    repo_id: str = Field(alias="repoId", min_length=1)
    method: str = Field(min_length=1)
    """HTTP method or RPC verb: GET, POST, PUT, DELETE, PATCH, RPC."""

    path: str = Field(min_length=1)
    """URL path or RPC route, including any framework-defined prefix."""

    framework: str = Field(default="unknown")
    """The framework that registered the route: fastapi, flask, spring, etc."""

    handler_artifact_id: str | None = Field(default=None, alias="handlerArtifactId")
    """The CodeArtifact that handles this endpoint, when resolvable."""

    handler_file: str = Field(default="", alias="handlerFile")
    handler_symbol: str = Field(default="", alias="handlerSymbol")
    is_public: bool = Field(default=True, alias="isPublic")


class DataModel(_Frozen):
    """A structured data class — Pydantic model, dataclass, ORM entity, etc.

    Lets the impact graph answer "if this model changes, what reads/writes it?"
    Fields are stored as a tuple of (name, type_hint) pairs; we don't try to
    parse the type AST beyond what the grammar already extracted.
    """

    id: str = Field(min_length=1)
    repo_id: str = Field(alias="repoId", min_length=1)
    name: str = Field(min_length=1)
    file: str = Field(min_length=1)
    line_range: LineRange = Field(alias="lineRange")
    kind: DataModelKind = DataModelKind.UNKNOWN

    fields: tuple[tuple[str, str], ...] = Field(default_factory=tuple)
    """Tuple of (field_name, type_hint_text). Type hints are raw strings."""

    table_name: str = Field(default="", alias="tableName")
    """For ORM models: the table they map to. Empty otherwise."""

    is_public: bool = Field(default=True, alias="isPublic")


class Query(_Frozen):
    """A SQL/JPQL query string or ORM call site.

    Sourced from CALL facts (`session.execute(text("..."))`, `@Query(...)`,
    `entityManager.createQuery(...)`). Tables-touched is best-effort: we
    regex for FROM/JOIN/INSERT INTO/UPDATE without a full SQL parser.
    """

    id: str = Field(min_length=1)
    repo_id: str = Field(alias="repoId", min_length=1)
    kind: QueryKind = QueryKind.RAW_SQL
    file: str = Field(min_length=1)
    line: int = Field(ge=1)
    expression: str = Field(default="")
    """The raw SQL/JPQL or a stringified ORM call expression."""

    tables: tuple[str, ...] = Field(default_factory=tuple)
    """Best-effort list of referenced table/entity names."""

    enclosing_artifact_id: str | None = Field(default=None, alias="enclosingArtifactId")
    """CodeArtifact (function/method) that executes this query, when known."""


class KafkaTopic(_Frozen):
    """A Kafka topic referenced by at least one producer or consumer.

    Topic names are the cross-repo join key: a producer in repo A and a
    consumer in repo B both naming "user.events" stitch into a single edge
    path through the same KafkaTopic node.
    """

    id: str = Field(min_length=1)
    """Stable id of the form `topic:<name>` — global, not per-repo."""

    name: str = Field(min_length=1)
    """Wire-level topic name (e.g. 'user.events')."""


class KafkaProducer(_Frozen):
    """A code site that publishes to a Kafka topic."""

    id: str = Field(min_length=1)
    repo_id: str = Field(alias="repoId", min_length=1)
    function_artifact_id: str = Field(alias="functionArtifactId", min_length=1)
    topic_name: str = Field(alias="topicName", min_length=1)
    file: str = Field(min_length=1)
    line: int = Field(ge=1)
    framework: str = Field(default="unknown")
    """kafka-python, confluent-kafka, spring-kafka, faust, etc."""


class KafkaConsumer(_Frozen):
    """A code site that subscribes to a Kafka topic."""

    id: str = Field(min_length=1)
    repo_id: str = Field(alias="repoId", min_length=1)
    function_artifact_id: str = Field(alias="functionArtifactId", min_length=1)
    topic_name: str = Field(alias="topicName", min_length=1)
    file: str = Field(min_length=1)
    line: int = Field(ge=1)
    framework: str = Field(default="unknown")
    consumer_group: str = Field(default="", alias="consumerGroup")


class Suggestion(_Frozen):
    """A candidate edge proposed by the LLM enhance pass.

    Loader writes these MERGE'd into Neo4j with `source="llm"` and the
    provided `confidence`, so queries can filter LLM-suggested edges from
    deterministic resolver-emitted ones.
    """

    id: str = Field(min_length=1)
    """Stable id from (src, rel, dst) so repeated runs dedupe."""

    src_id: str = Field(alias="srcId", min_length=1)
    src_label: str = Field(alias="srcLabel", min_length=1)
    """Neo4j label of the source node — `CodeArtifact`, `Endpoint`, etc."""

    rel: str = Field(min_length=1)
    """Edge type: `CALLS`, `READS`, `WRITES`, etc."""

    dst_id: str = Field(alias="dstId", min_length=1)
    dst_label: str = Field(alias="dstLabel", min_length=1)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reason: str = Field(default="")
    """Free-form: why the LLM thinks this edge holds. Audit-only."""

    prompt_version: str = Field(default="", alias="promptVersion")
    model: str = Field(default="")


class Mock(_Frozen):
    """A mocked target inside a test — @patch("..."), patch.object(X, "y"), etc.

    `target_artifact_id` is populated when the resolver could bind the patch
    string to a known CodeArtifact (local source); for third-party patches
    (e.g. @patch("httpx.get")) it stays None and `patch_target` carries the
    raw string.
    """

    id: str = Field(min_length=1)
    repo_id: str = Field(alias="repoId", min_length=1)
    test_id: str = Field(alias="testId", min_length=1)
    kind: MockKind
    patch_target: str = Field(alias="patchTarget", min_length=1)
    """Raw target as written in source: 'httpx.get' or 'mymod.MyClass.method'."""

    target_artifact_id: str | None = Field(default=None, alias="targetArtifactId")
    """Resolved CodeArtifact ID when the target is a known local symbol."""

    file: str = Field(min_length=1)
    line: int = Field(ge=1)
