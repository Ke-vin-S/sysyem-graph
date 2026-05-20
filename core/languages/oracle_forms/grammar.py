"""Oracle Forms grammar — a stub for binary `.fmb`/`.fmx` files.

We never parse the binary. The grammar exists only so the walker emits
ONE SYMBOL fact per Forms file, which the `FormsServiceResolver` picks up
to materialize a `Service(language='oracle_forms')` node.

Caveat: the walker reads file content as UTF-8 with `errors='replace'`
before calling `extract`, and files larger than `WalkerConfig.max_file_bytes`
(1 MiB default) are skipped entirely. Large Forms binaries that exceed
that limit can still be surfaced as services via the `ORACLE_FORMS_APPS`
env override.
"""

from __future__ import annotations

from pathlib import Path

from core.facts import Fact, FactKind
from ingestion.grammars.grammar import Grammar


class FormsGrammar(Grammar):
    suffixes = (".fmb", ".fmx")

    def extract(self, file: Path, content: str, *, repo_id: str) -> list[Fact]:
        # `content` is intentionally ignored — the file is binary.
        del content
        name = file.stem
        return [
            Fact(
                kind=FactKind.SYMBOL,
                file=str(file),
                line=1,
                repo_id=repo_id,
                data={
                    "sym_kind": "form_app",
                    "name": name,
                    "enclosing_class": "",
                    "is_async": False,
                    "modifiers": [],
                },
            )
        ]
