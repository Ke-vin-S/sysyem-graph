"""Decide which frameworks apply to a given repo's FactTree.

Detection is best-effort and OR'd: any matching DetectorRule activates the
framework. A repo can match multiple frameworks (e.g. fastapi + pytest); the
resolver consumes all of them together.
"""

from __future__ import annotations

import fnmatch

from core.facts import FactKind, FactTree
from core.frameworks.definition import DetectorRule, FrameworkDefinition
from core.frameworks.library import FrameworkLibrary


def detect_frameworks(tree: FactTree, library: FrameworkLibrary) -> list[FrameworkDefinition]:
    """Return every framework whose detectors match facts in `tree`."""
    matched: list[FrameworkDefinition] = []
    for definition in library.all():
        for rule in definition.detectors:
            if _rule_matches(rule, tree):
                matched.append(definition)
                break
    return matched


def _rule_matches(rule: DetectorRule, tree: FactTree) -> bool:
    if rule.any_import_starts_with:
        for fact in tree.where(kind=FactKind.IMPORT):
            module = str(fact.data.get("module", ""))
            if any(module.startswith(prefix) for prefix in rule.any_import_starts_with):
                return True
    if rule.any_config_key:
        for fact in tree.where(kind=FactKind.CONFIG_VALUE):
            key = str(fact.data.get("key", ""))
            if any(key.startswith(prefix) for prefix in rule.any_config_key):
                return True
    if rule.any_file_glob:
        for file in tree.files():
            if any(fnmatch.fnmatch(file, glob) for glob in rule.any_file_glob):
                return True
    return False
