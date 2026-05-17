"""FactTree — indexed container for the facts produced from one repo.

Resolvers query the tree by kind, file, and proximity. To keep those queries
cheap on large repos, we index by `(kind, file)` and by `file` at construction
time. Once built, the tree is read-only — facts are immutable, indices never
need to invalidate.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from core.facts.fact import Fact, FactKind


@dataclass
class FactTree:
    """Read-only fact store with the index shapes resolvers need.

    Build by passing facts to the constructor or calling `extend`. After that,
    queries are O(1) for the indexed shapes and O(n) only for the rare
    full-scan operations.
    """

    repo_id: str
    _by_kind: dict[FactKind, list[Fact]] = field(default_factory=lambda: defaultdict(list))
    _by_file: dict[str, list[Fact]] = field(default_factory=lambda: defaultdict(list))
    _all: list[Fact] = field(default_factory=list)

    @classmethod
    def from_facts(cls, repo_id: str, facts: Iterable[Fact]) -> "FactTree":
        tree = cls(repo_id=repo_id)
        tree.extend(facts)
        return tree

    def extend(self, facts: Iterable[Fact]) -> None:
        for fact in facts:
            self._all.append(fact)
            self._by_kind[fact.kind].append(fact)
            self._by_file[fact.file].append(fact)

    def __iter__(self) -> Iterator[Fact]:
        return iter(self._all)

    def __len__(self) -> int:
        return len(self._all)

    def where(self, *, kind: FactKind | None = None, file: str | None = None) -> list[Fact]:
        """Linear filter over the indexed buckets."""
        if kind is not None and file is None:
            return list(self._by_kind.get(kind, ()))
        if file is not None and kind is None:
            return list(self._by_file.get(file, ()))
        if kind is not None and file is not None:
            return [f for f in self._by_kind.get(kind, ()) if f.file == file]
        return list(self._all)

    def by_file(self, file: str) -> list[Fact]:
        return list(self._by_file.get(file, ()))

    def files(self) -> list[str]:
        return sorted(self._by_file)

    def symbol_at(self, *, file: str, line_after: int) -> Fact | None:
        """First SYMBOL fact in `file` at or after `line_after`.

        Used to attach a DECORATOR or ANNOTATION fact to the function/class
        it decorates: decorators sit on the lines above the def, and the
        nearest following SYMBOL is the target.
        """
        best: Fact | None = None
        for fact in self._by_file.get(file, ()):
            if fact.kind not in (FactKind.SYMBOL, FactKind.CLASS_DEF):
                continue
            if fact.line < line_after:
                continue
            if best is None or fact.line < best.line:
                best = fact
        return best

    def enclosing_class(self, symbol: Fact) -> Fact | None:
        """Return the nearest enclosing CLASS_DEF in the same file, or None.

        'Enclosing' means: a CLASS_DEF whose `line <= symbol.line` and whose
        `line_end` (when known) covers `symbol.line`. We do not track scope
        formally — we approximate by line range, which is enough for the
        single-class-per-file convention common in Java and frequent in Python.
        """
        candidates = [
            f
            for f in self._by_file.get(symbol.file, ())
            if f.kind is FactKind.CLASS_DEF and f.line <= symbol.line
        ]
        if not candidates:
            return None
        # Prefer the deepest (highest line) candidate that still encloses.
        # A candidate with no line_end is treated as "extends to EOF" so it
        # always encloses any later symbol.
        candidates.sort(key=lambda f: f.line, reverse=True)
        for candidate in candidates:
            if candidate.line_end is None or candidate.line_end >= symbol.line:
                return candidate
        return None

    def imports_in(self, file: str) -> list[Fact]:
        return [f for f in self._by_file.get(file, ()) if f.kind is FactKind.IMPORT]

    def config_values(self) -> list[Fact]:
        return list(self._by_kind.get(FactKind.CONFIG_VALUE, ()))
