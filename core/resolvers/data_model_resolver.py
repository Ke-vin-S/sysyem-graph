"""DataModelResolver: emit DataModel records for structured-data classes.

Driven by `data_models` patterns declared per framework. The resolver
itself knows nothing about Pydantic, SQLAlchemy, dataclasses, or JPA —
it walks CLASS_DEF + DECORATOR facts and matches them against the
patterns each framework's YAML supplies. New ORM? New `data_models`
block, no resolver edit.

A class matches a framework when ANY of:
  * one of its bases' final segment is in `base_class_suffixes`, or
  * one of its decorator callees is in `decorator_callees`.

If multiple frameworks match the same class (e.g. a SQLAlchemy entity
also tagged `@dataclass`), the first framework wins in iteration order
— framework registration order is deterministic via FrameworkLibrary,
so the result is stable across runs.

v1 captures `kind`, `name`, `file`, `line_range`, `table_name=""`,
`fields=()`. Field-level extraction needs grammar work (class-level
annotations aren't emitted as facts today) and lands in step 6.5.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

from core.facts import Fact, FactKind, FactTree
from core.frameworks import EffectiveFramework
from core.frameworks.definition import DataModelPatterns
from core.types import DataModel, DataModelKind, LineRange


@dataclass
class DataModelResolution:
    data_models: list[DataModel]


class DataModelResolver:
    def resolve(
        self,
        *,
        tree: FactTree,
        frameworks: tuple[EffectiveFramework, ...],
        repo_id: str,
        repo_root: str | None = None,
    ) -> DataModelResolution:
        patterns = [
            (fw, fw.data_models)
            for fw in frameworks
            if fw.data_models is not None
            and (
                fw.data_models.base_class_suffixes
                or fw.data_models.decorator_callees
                or fw.data_models.annotation_callees
            )
        ]
        if not patterns:
            return DataModelResolution(data_models=[])

        # Index class decorators/annotations by (file, class_name) for O(1)
        # lookup. We index BOTH FactKind.DECORATOR (Python) and
        # FactKind.ANNOTATION with target_kind="class" (Java) so the same
        # match logic applies regardless of source language.
        class_decorators: dict[tuple[str, str], list[str]] = {}
        for dec in tree.where(kind=FactKind.DECORATOR):
            target = str(dec.data.get("target_symbol", ""))
            if not target:
                continue
            class_decorators.setdefault((dec.file, target), []).append(
                str(dec.data.get("callee", ""))
            )
        class_annotations: dict[tuple[str, str], list[str]] = {}
        for ann in tree.where(kind=FactKind.ANNOTATION):
            if ann.data.get("target_kind") != "class":
                continue
            target = str(ann.data.get("target_symbol", ""))
            if not target:
                continue
            class_annotations.setdefault((ann.file, target), []).append(
                str(ann.data.get("callee", ""))
            )

        seen_ids: set[str] = set()
        models: list[DataModel] = []
        for class_fact in tree.where(kind=FactKind.CLASS_DEF):
            name = str(class_fact.data.get("name", ""))
            if not name:
                continue
            bases = tuple(str(b) for b in class_fact.data.get("bases") or ())
            decorators = tuple(class_decorators.get((class_fact.file, name), ()))
            annotations = tuple(class_annotations.get((class_fact.file, name), ()))

            for fw, dm in patterns:
                if not _matches(bases, decorators, annotations, dm):
                    continue
                model = _build_data_model(
                    repo_id=repo_id,
                    class_fact=class_fact,
                    name=name,
                    kind=dm.kind,
                    repo_root=repo_root,
                )
                if model.id in seen_ids:
                    continue
                seen_ids.add(model.id)
                models.append(model)
                break  # first matching framework wins
        return DataModelResolution(data_models=models)


def _matches(
    bases: tuple[str, ...],
    decorators: tuple[str, ...],
    annotations: tuple[str, ...],
    dm: DataModelPatterns,
) -> bool:
    if dm.base_class_suffixes:
        suffixes = set(dm.base_class_suffixes)
        for base in bases:
            # Compare final segment so `pydantic.BaseModel` matches `BaseModel`.
            last = base.rsplit(".", 1)[-1]
            if last in suffixes:
                return True
    if dm.decorator_callees:
        callees = set(dm.decorator_callees)
        for dec_callee in decorators:
            if dec_callee in callees:
                return True
            # Match by final segment too: `@dataclass` and `@dataclasses.dataclass`
            # are equivalent and both should land for the same YAML entry.
            if dec_callee.rsplit(".", 1)[-1] in callees:
                return True
    if dm.annotation_callees:
        ann_set = set(dm.annotation_callees)
        for ann_callee in annotations:
            # Java grammar stores the simple name in `callee` already, so
            # exact-match is enough; we also accept dotted forms by suffix
            # match for forward-compatibility.
            if ann_callee in ann_set:
                return True
            if ann_callee.rsplit(".", 1)[-1] in ann_set:
                return True
    return False


def _build_data_model(
    *, repo_id: str, class_fact: Fact, name: str, kind: str, repo_root: str | None = None
) -> DataModel:
    start = class_fact.line
    end = class_fact.line_end or start
    file = _rel_to(class_fact.file, repo_root) if repo_root else class_fact.file
    return DataModel(
        id=f"dm:{repo_id}:{file}:{name}",
        repoId=repo_id,
        name=name,
        file=file,
        lineRange=LineRange(start=start, end=end),
        kind=_normalize_kind(kind),
    )


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


def _normalize_kind(kind: str) -> DataModelKind:
    try:
        return DataModelKind(kind)
    except ValueError:
        return DataModelKind.UNKNOWN


def _iter_classes_with_decorators(
    tree: FactTree,
) -> Iterable[tuple[Fact, tuple[str, ...]]]:
    """Helper retained for future use; unused after the index optimization."""
    decorators_by_target: dict[tuple[str, str], list[str]] = {}
    for dec in tree.where(kind=FactKind.DECORATOR):
        target = str(dec.data.get("target_symbol", ""))
        if not target:
            continue
        decorators_by_target.setdefault((dec.file, target), []).append(
            str(dec.data.get("callee", ""))
        )
    for class_fact in tree.where(kind=FactKind.CLASS_DEF):
        name = str(class_fact.data.get("name", ""))
        decorators = tuple(decorators_by_target.get((class_fact.file, name), ()))
        yield class_fact, decorators
