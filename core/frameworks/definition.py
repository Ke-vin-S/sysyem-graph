"""Schema for `frameworks/*.yaml` — the single source of truth for framework knowledge.

Each section is optional. A "language" framework (`python.yaml`, `java.yaml`)
typically only fills `tests`, `mocks`, and `http_clients`. A "web framework"
(`fastapi.yaml`, `spring.yaml`) fills `routes` instead. The same schema covers
both because resolvers query by section, not by framework category.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _Frozen(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        populate_by_name=True,
    )


class DetectorRule(_Frozen):
    """One rule for deciding whether a repo uses this framework.

    Multiple rules in a list are OR'd: any matching rule activates the
    framework for that repo. Keeping rules small and orthogonal makes the
    detection logic auditable.
    """

    any_import_starts_with: tuple[str, ...] = ()
    any_config_key: tuple[str, ...] = ()
    """Matches if any CONFIG_VALUE fact has a key starting with one of these
    prefixes (e.g. `spring.` for application.yml)."""

    any_file_glob: tuple[str, ...] = ()
    """Path globs for "this framework is here if files like X exist"."""


class RouteMountCall(_Frozen):
    """How a sub-router/blueprint gets mounted onto a parent app.

    Example: `app.include_router(router, prefix="/api/v1")` in FastAPI.
    Example: `app.register_blueprint(bp, url_prefix="/api")` in Flask.
    """

    method: str
    """Method name on the parent app/router (`include_router`, `register_blueprint`)."""

    prefix_kwarg: str | None = None
    """Keyword argument that holds the prefix string (`prefix` for FastAPI)."""

    prefix_arg: int | None = None
    """Or positional argument index (rare)."""

    router_arg: int | None = 0
    """Positional index of the sub-router being mounted. Defaults to 0."""


class RouteBasePathSource(_Frozen):
    """Where the base URL prefix for an app comes from."""

    callee: str | None = None
    """Constructor call whose kwarg holds the base. e.g. `FastAPI(root_path=...)`."""

    kwarg: str | None = None
    """The kwarg name. Used with `callee`."""

    config_key: str | None = None
    """Or a dotted config key, e.g. `server.servlet.context-path`."""


class RoutePatterns(_Frozen):
    """How routes are declared in this framework."""

    decorator_callee_patterns: tuple[str, ...] = ()
    """Decorator callee patterns. `{any}` matches any receiver name; literal
    strings match exactly. e.g. `{any}.get`, `{any}.post` for FastAPI/Flask;
    `app.route` for Flask's app-level routes."""

    decorator_methods: tuple[str, ...] = ()
    """Allowed method names if decorator callees encode method (e.g. `get`,
    `post`)."""

    annotation_method_names: tuple[str, ...] = ()
    """For Java-style: annotation simple names indicating a method handler
    (e.g. `GetMapping`, `PostMapping`)."""

    annotation_method_map: dict[str, str] = Field(default_factory=dict)
    """Map annotation simple name → HTTP method string. Lets framework YAML
    declare its own mapping (Spring: GetMapping→get, etc.) instead of a
    hardcoded dict in the resolver. Annotations not in the map but listed
    in `annotation_method_names` fall back to the literal kwarg/method
    value when the strategy can extract one."""

    annotation_class_prefix: tuple[str, ...] = ()
    """Class-level annotation simple names that contribute a path prefix
    (e.g. `RequestMapping` in Spring)."""

    mount_calls: tuple[RouteMountCall, ...] = ()
    base_path_sources: tuple[RouteBasePathSource, ...] = ()


class TestPatterns(_Frozen):
    """How tests are identified."""

    function_name_prefixes: tuple[str, ...] = ()
    decorator_callees: tuple[str, ...] = ()
    """Decorator/annotation callees that mark a function as a test.
    For Python pytest: function_name_prefixes=['test_'] is enough; for JUnit
    we need decorator_callees=['Test', 'ParameterizedTest', ...]."""

    integration_markers: tuple[str, ...] = ()
    e2e_markers: tuple[str, ...] = ()
    test_path_globs: tuple[str, ...] = ()
    """Path patterns indicating test files (glob-style relative to repo root)."""

    test_path_prefixes: tuple[str, ...] = ()
    """Path segment pairs like 'tests/integration' that classify by location."""


class MockPatterns(_Frozen):
    decorator_callees: tuple[str, ...] = ()
    """Decorators/annotations that mark a parameter/field as mocked
    (`unittest.mock.patch`, `Mock`)."""

    with_callees: tuple[str, ...] = ()
    """For Python `with patch(...):` style."""

    field_annotations: tuple[str, ...] = ()
    """For Java `@Mock` / `@MockBean` on fields."""

    mock_call_signatures: tuple[str, ...] = ()
    """Inline calls that produce a mock (`Mockito.mock`, `mock`)."""


class HttpClientPatterns(_Frozen):
    external_modules: tuple[str, ...] = ()
    """Import modules whose use indicates a real HTTP/DB/queue call."""


class DataModelPatterns(_Frozen):
    """How this framework declares structured data classes.

    A class matches when ANY of:
      * its bases include a name whose final segment is in `base_class_suffixes`,
      * one of its decorators/annotations' callees is in `decorator_callees`,
      * one of its class-level annotations matches `annotation_callees` (Java).

    `decorator_callees` and `annotation_callees` are conceptually the same
    (Python @decorator vs Java @Annotation); the split is because language
    grammars emit different FactKinds (DECORATOR vs ANNOTATION).
    """

    kind: str = "unknown"
    """Tag applied to the emitted DataModel.kind: pydantic, dataclass,
    sqlalchemy_orm, jpa_entity, etc."""

    base_class_suffixes: tuple[str, ...] = ()
    """Match when the class extends one of these — compared by the final
    segment so `pydantic.BaseModel` and `BaseModel` both match `BaseModel`."""

    decorator_callees: tuple[str, ...] = ()
    """Python-side: match when class decorated with one of these (e.g. `dataclass`)."""

    annotation_callees: tuple[str, ...] = ()
    """Java-side: match when class carries one of these annotations
    (e.g. `Entity`, `Document`)."""

    table_arg_kwarg: str = ""
    """For ORM kinds: name of the kwarg holding the table name when set
    inline (e.g. `__tablename__` won't show up — this is for builder-style
    cases). Empty when not applicable."""


class QueryPatterns(_Frozen):
    """How queries appear as call sites or annotations.

    A CALL matches when its `method` is in `call_methods` OR its full
    callee is in `call_callees`. An ANNOTATION matches when its `callee`
    is in `annotation_callees` (Java `@Query("...")`). In all cases the
    argument at `expression_arg` is the SQL/JPQL text.
    """

    kind: str = "raw_sql"
    """Tag applied to emitted Query.kind."""

    call_methods: tuple[str, ...] = ()
    """`execute`, `query`, `createQuery`, etc."""

    call_callees: tuple[str, ...] = ()
    """Full dotted callees like `text` or `sqlalchemy.text`."""

    annotation_callees: tuple[str, ...] = ()
    """Java-side: annotation names declaring a query (`Query`, `NamedQuery`)."""

    expression_arg: int = 0
    """Positional index of the SQL/expression argument."""


class KafkaPatterns(_Frozen):
    """Kafka producer/consumer call shapes for this framework."""

    produce_methods: tuple[str, ...] = ()
    """Method names that publish to a topic: `send`, `produce`."""

    produce_callees: tuple[str, ...] = ()
    """Full dotted callees: `KafkaTemplate.send`, `confluent_kafka.Producer.produce`."""

    consume_callees: tuple[str, ...] = ()
    """Constructor/decorator callees that subscribe: `KafkaConsumer`,
    `@app.agent` (Faust)."""

    consume_annotations: tuple[str, ...] = ()
    """Java-side: annotations on methods that subscribe (`KafkaListener`).
    The annotation's first arg / `topics` kwarg holds the topic."""

    topic_arg: int = 0
    """Position of the topic argument in produce/consume calls."""

    topic_kwarg: str = ""
    """Kwarg name holding the topic when not positional (Java
    `@KafkaListener(topics = "user.events")`)."""


class FrameworkDefinition(_Frozen):
    name: str
    language: str
    """Canonical language: `python`, `java`, `kotlin`, `go`, etc. Frameworks
    that span multiple languages list one primary here and override with
    detectors."""

    detectors: tuple[DetectorRule, ...] = ()
    routes: RoutePatterns | None = None
    tests: TestPatterns | None = None
    mocks: MockPatterns | None = None
    http_clients: HttpClientPatterns | None = None
    data_models: DataModelPatterns | None = None
    queries: QueryPatterns | None = None
    kafka: KafkaPatterns | None = None
    notes: str = Field(default="")
