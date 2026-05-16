"""Java AST-based test extractor using javalang.

Identifies JUnit (4/5) and TestNG test methods, collects imports, detects
Mockito-style mocking, and flags use of HTTP/DB/queue client libraries that
would make the test an integration test.

javalang is a pure-Python Java parser — no JVM required. It's stricter than
the Python `ast` module about syntax (it predates several modern Java
features), so we catch all parse errors and return [] for files we can't
read, the same way the Python parser does.
"""

from __future__ import annotations

from pathlib import Path

from ingestion.parsers.parser import ParsedTest, Parser

#: Annotations that mark a method as a test, across JUnit 4, JUnit 5 (Jupiter),
#: and TestNG. We match on the simple name only (after the last dot), since
#: javalang stores annotations un-qualified by default.
_TEST_ANNOTATIONS: frozenset[str] = frozenset(
    {
        "Test",
        "ParameterizedTest",
        "RepeatedTest",
        "TestFactory",
        "TestTemplate",
    }
)

#: Annotation simple-names whose presence on a field/parameter makes that
#: declaration count as "mocked" for classification purposes.
_MOCK_ANNOTATIONS: frozenset[str] = frozenset({"Mock", "MockBean", "Spy", "InjectMocks"})

#: Import package prefixes that mean "this file talks to a real external
#: system." If a test imports one and we don't see a corresponding mock,
#: we classify it as an integration test.
_EXTERNAL_IMPORT_PREFIXES: tuple[str, ...] = (
    "java.net.http",
    "java.sql",
    "javax.sql",
    "javax.persistence",
    "jakarta.persistence",
    "okhttp3",
    "retrofit2",
    "com.squareup.okhttp",
    "org.apache.http",
    "org.apache.hc",
    "org.springframework.web.client",
    "org.springframework.web.reactive.function.client",
    "org.springframework.jdbc",
    "org.springframework.kafka",
    "org.springframework.amqp",
    "org.springframework.data.redis",
    "redis.clients",
    "org.apache.kafka",
    "software.amazon.awssdk",
    "com.amazonaws",
    "io.grpc",
    "com.mongodb",
    "org.neo4j.driver",
)


class JavaParser(Parser):
    suffixes = (".java",)

    def parse(self, file: Path, content: str) -> list[ParsedTest]:
        # Imported lazily so environments without the dep can still import
        # this module (mirrors the Python parser's stdlib-only behavior).
        try:
            import javalang
            from javalang.parser import JavaSyntaxError
        except ImportError:
            return []

        try:
            tree = javalang.parse.parse(content)
        except (JavaSyntaxError, Exception):
            return []

        imports = tuple(imp.path for imp in (tree.imports or []))
        external_imports = {
            imp for imp in imports if imp.startswith(_EXTERNAL_IMPORT_PREFIXES)
        }
        external_simple_names = {imp.rsplit(".", 1)[-1] for imp in external_imports}

        # Class-level @Mock fields apply to every test method in the class.
        class_mocked = _collect_field_mocks(tree, javalang)

        tests: list[ParsedTest] = []
        for _, method_node in tree.filter(javalang.tree.MethodDeclaration):
            annotations = tuple(
                _annotation_name(a) for a in (method_node.annotations or [])
            )
            if not _is_test_method(annotations):
                continue
            line = _line_of(method_node)
            method_mocked = _collect_method_mocks(method_node, javalang) | class_mocked
            unmocked_externals = external_simple_names - method_mocked
            calls_external = bool(unmocked_externals) and _references_any(
                method_node, unmocked_externals, javalang
            )
            tests.append(
                ParsedTest(
                    name=method_node.name,
                    file=str(file),
                    line_start=line,
                    line_end=line,
                    decorators=annotations,
                    imports=imports,
                    mocked_modules=tuple(sorted(method_mocked)),
                    calls_external=calls_external,
                )
            )
        return tests


def _annotation_name(node: object) -> str:
    name = getattr(node, "name", "") or ""
    # javalang represents fully-qualified annotations like @org.junit.Test as
    # "org.junit.Test"; we only ever match on the simple name.
    return name.rsplit(".", 1)[-1]


def _is_test_method(annotations: tuple[str, ...]) -> bool:
    return any(a in _TEST_ANNOTATIONS for a in annotations)


def _line_of(node: object) -> int:
    pos = getattr(node, "position", None)
    line = getattr(pos, "line", None) if pos is not None else None
    return int(line) if line else 1


def _collect_field_mocks(tree: object, javalang_mod) -> set[str]:  # type: ignore[no-untyped-def]
    """Simple names of fields annotated with `@Mock`/`@MockBean` etc."""
    mocked: set[str] = set()
    for _, field in tree.filter(javalang_mod.tree.FieldDeclaration):  # type: ignore[attr-defined]
        annotations = {_annotation_name(a) for a in (field.annotations or [])}
        if not annotations & _MOCK_ANNOTATIONS:
            continue
        field_type = getattr(field, "type", None)
        type_name = getattr(field_type, "name", None)
        if type_name:
            mocked.add(type_name)
    return mocked


def _collect_method_mocks(method_node: object, javalang_mod) -> set[str]:  # type: ignore[no-untyped-def]
    """Inline `Mockito.mock(Foo.class)` calls inside the method body."""
    mocked: set[str] = set()
    for _, call in method_node.filter(javalang_mod.tree.MethodInvocation):  # type: ignore[attr-defined]
        if call.member != "mock" or not call.arguments:
            continue
        # Mockito.mock(Foo.class) -> arguments[0] is a ClassReference with .type.name
        first = call.arguments[0]
        ref_type = getattr(first, "type", None)
        type_name = getattr(ref_type, "name", None)
        if type_name:
            mocked.add(type_name)
    return mocked


def _references_any(method_node: object, names: set[str], javalang_mod) -> bool:  # type: ignore[no-untyped-def]
    """True if the method body references any of `names` as a type or invocation target."""
    if not names:
        return False
    Node = javalang_mod.tree.Node  # type: ignore[attr-defined]
    for _, node in method_node.filter(Node):
        for attr in ("type", "qualifier", "member"):
            value = getattr(node, attr, None)
            if isinstance(value, str) and value in names:
                return True
            inner_name = getattr(value, "name", None)
            if isinstance(inner_name, str) and inner_name in names:
                return True
    return False
