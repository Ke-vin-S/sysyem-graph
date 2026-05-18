"""Module-name ↔ file-path translation, driven by `LanguageProfile`."""

from __future__ import annotations

import fnmatch

from core.languages.profile import LanguageProfile


def resolve_candidate_files(module: str, profile: LanguageProfile) -> list[str]:
    """Expand a dotted module name to candidate file paths using the profile's
    templates.

    Python: `core.resolvers.x` -> ['core/resolvers/x.py', 'core/resolvers/x/__init__.py']
    Java:   `com.x.Foo` -> ['com/x/Foo.java']
    PL/SQL: `payments.charges` -> ['payments/charges.pks', '.pkb', '.sql']
    """
    if not module:
        return []
    sep = profile.module_resolution.separator or "."
    cleaned = module.lstrip(sep)
    if not cleaned:
        return []
    base = cleaned.replace(sep, "/")
    return [
        tpl.format(module=base) for tpl in profile.module_resolution.candidate_path_templates
    ]


def is_aggregator_file(file: str, profile: LanguageProfile) -> bool:
    """True if `file` matches any of the language's package-aggregator patterns."""
    name = file.rsplit("/", 1)[-1]
    for pattern in profile.package_aggregator.files:
        if "/" in pattern:
            if fnmatch.fnmatch(file, pattern):
                return True
        elif fnmatch.fnmatch(name, pattern):
            return True
    return False


def init_file_to_module(file: str, profile: LanguageProfile) -> str:
    """Inverse of `resolve_candidate_files`: given an aggregator file, return
    the module name it represents.

    Python: `core/resolvers/__init__.py` -> `core.resolvers`
    PL/SQL: `payments/charges.pks` -> `payments.charges`
    Java:   `com/x/Foo.java` -> `com.x.Foo`

    Empty string when `file` doesn't match any of the language's templates.

    When multiple templates match (e.g. Python's `{module}.py` AND
    `{module}/__init__.py` both match `core/x/__init__.py`), the longest
    suffix wins — that's the more-specific template.
    """
    sep = profile.module_resolution.separator or "."
    best_suffix = -1
    best_base = ""
    for template in profile.module_resolution.candidate_path_templates:
        if "{module}" not in template:
            continue
        prefix, _, suffix = template.partition("{module}")
        if prefix:
            continue
        if not file.endswith(suffix):
            continue
        if len(suffix) <= best_suffix:
            continue
        best_suffix = len(suffix)
        best_base = file[: -len(suffix)] if suffix else file
    if best_suffix < 0:
        return ""
    return best_base.replace("/", sep)
