"""Atomic Fact records — what grammars emit, what resolvers consume."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field


class FactKind(StrEnum):
    """The kind of source-code observation a Fact represents.

    Kinds are intentionally generic across languages. Python `def f` and Java
    `public void f` are both `SYMBOL` with `sym_kind="function"`. Python
    `@decorator` and Java `@Annotation` are `DECORATOR` and `ANNOTATION`
    respectively (we keep them distinct because Python decorators wrap; Java
    annotations describe).
    """

    SYMBOL = "symbol"
    """A definition: function, method, class, variable. `data.sym_kind`
    distinguishes between them."""

    CLASS_DEF = "class_def"
    """A class/interface/struct definition. Separate from SYMBOL because
    resolvers query for enclosing class often."""

    DECORATOR = "decorator"
    """A Python decorator. `data.callee` (e.g. 'router.get'), `data.args`,
    `data.kwargs`. `data.target_symbol` points at the SYMBOL it decorates."""

    ANNOTATION = "annotation"
    """A Java/Kotlin/C# annotation. Same shape as DECORATOR but typed differently
    so a single resolver can match by kind."""

    IMPORT = "import"
    """An import statement. `data.module` (dotted), `data.names` (imported
    symbols), `data.alias`."""

    CALL = "call"
    """A function/method invocation. `data.receiver`, `data.method`,
    `data.args` (positional, simplified), `data.kwargs` (string-keyed)."""

    CONFIG_VALUE = "config_value"
    """A key/value from a config file (yaml/toml/properties/env/json). `data.key`
    is a dotted path, `data.value` is the leaf value as a string."""

    STRING_LITERAL = "string_literal"
    """A standalone string literal worth retaining (e.g. a SQL query, a URL).
    Most string literals aren't worth keeping; grammars decide."""

    TYPE_REFERENCE = "type_reference"
    """A reference to a type by name. Used by resolvers when a class declaration
    lives in one file but a controller annotation refers to it."""

    ASSIGNMENT = "assignment"
    """A name-binding statement: `x = expr`, `self.x = expr`, `x: T = expr`.

    Scope is one of {module, function, method, class}. Grammars emit
    assignments selectively — typically module-level and self.X = inside
    methods, since those are the ones the resolver uses for type
    inference. In-function local assignments are ignored to keep the fact
    count bounded.

    `data` shape:
      target: str               # short name (`x`, or last segment of `self.x`)
      target_chain: list[str]   # `self.x` -> ["self", "x"]
      source_kind: str          # call | name | literal | attr | expr
      source: str               # callee/name/value, raw string form
      type_hint: str            # `db: Database = ...` -> "Database"
      scope: str                # module | function | method | class
    """


class Fact(BaseModel):
    """An atomic, uninterpreted observation from a source file.

    Facts are intentionally dumb: a `DECORATOR` fact says "this callee was used
    at this line with these args"; it does NOT say "this is a route decorator".
    The interpretation happens later, in resolvers, driven by framework YAML.

    `data` is a free-form payload because each FactKind has its own schema.
    Resolvers know what keys to look for given the kind. We trade a bit of
    type-safety for the flexibility to add new fact kinds without touching
    a giant union type.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_assignment=True,
        populate_by_name=True,
    )

    kind: FactKind
    file: str
    line: int = Field(ge=1)
    line_end: int | None = None
    repo_id: str
    data: dict[str, Any] = Field(default_factory=dict)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def id(self) -> str:
        """Stable hash of (kind, file, line, data). Used in derivation receipts
        so a resolver output can be traced back to the exact Facts that
        produced it."""
        payload = json.dumps(
            {"kind": str(self.kind), "file": self.file, "line": self.line, "data": self.data},
            sort_keys=True,
            default=str,
        ).encode()
        return "fact:" + hashlib.sha1(payload).hexdigest()[:16]
