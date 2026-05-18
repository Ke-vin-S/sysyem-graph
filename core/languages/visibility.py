"""Named visibility rules referenced by `languages/<lang>.yaml:visibility.rule`.

Each rule is a small predicate over a SYMBOL fact (or CLASS_DEF). The YAML
names which rule applies for a given language. New rules register via the
`_REGISTRY` dict at module level — no resolver edits required.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from core.facts import Fact


class VisibilityRule(ABC):
    """Decide whether a symbol's name is publicly visible.

    `data` is the Fact's `data` dict so concrete rules can reach in for the
    fields they need (`name`, `modifiers`, `enclosing_class`, etc.).
    """

    @abstractmethod
    def is_public(self, data: dict[str, Any]) -> bool: ...


class LeadingUnderscoreIsPrivate(VisibilityRule):
    """Python convention: names starting with `_` are private."""

    def is_public(self, data: dict[str, Any]) -> bool:
        name = str(data.get("name", ""))
        return bool(name) and not name.startswith("_")


class JavaPublicModifier(VisibilityRule):
    """Java: anything explicitly declared `public`. Defaults to False
    when modifiers are absent — that's package-private, which we treat
    as non-public for the cross-repo graph."""

    def is_public(self, data: dict[str, Any]) -> bool:
        modifiers = data.get("modifiers") or []
        if isinstance(modifiers, (list, tuple)):
            return "public" in modifiers
        return False


class StartsUppercase(VisibilityRule):
    """Go convention: exported identifiers start with a capital letter."""

    def is_public(self, data: dict[str, Any]) -> bool:
        name = str(data.get("name", ""))
        return bool(name) and name[0].isupper()


class PlsqlDeclaredInSpec(VisibilityRule):
    """PL/SQL: symbols declared in the `.pks` (package spec) are public;
    those only in `.pkb` (body) are private. Resolvers populate `data`
    with the file the symbol came from so this rule can inspect it."""

    def is_public(self, data: dict[str, Any]) -> bool:
        file = str(data.get("file", ""))
        return file.endswith(".pks") or file.endswith(".sql")


class ExportKeyword(VisibilityRule):
    """TypeScript / JavaScript: rely on an explicit `exported` flag set
    by the grammar when it sees `export` keyword on the declaration."""

    def is_public(self, data: dict[str, Any]) -> bool:
        return bool(data.get("exported"))


class AlwaysPublic(VisibilityRule):
    """Fallback used by languages without a meaningful visibility convention."""

    def is_public(self, data: dict[str, Any]) -> bool:
        return True


_REGISTRY: dict[str, VisibilityRule] = {
    "leading_underscore_is_private": LeadingUnderscoreIsPrivate(),
    "java_public_modifier": JavaPublicModifier(),
    "starts_uppercase": StartsUppercase(),
    "plsql_declared_in_spec": PlsqlDeclaredInSpec(),
    "export_keyword": ExportKeyword(),
    "always_public": AlwaysPublic(),
}


def registered_rules() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def is_public(rule_name: str, fact: Fact) -> bool:
    """Apply the named rule to a fact's data. Unknown rules default to public."""
    rule = _REGISTRY.get(rule_name)
    if rule is None:
        return True
    # Augment data with the fact's file so PL/SQL rule can check spec vs body.
    data = dict(fact.data)
    data.setdefault("file", fact.file)
    return rule.is_public(data)
