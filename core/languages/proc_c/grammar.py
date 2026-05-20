"""Pro*C grammar — C with embedded `EXEC SQL …` blocks.

Pro*C is preprocessed by Oracle's `proc` tool into plain C. We do NOT
invoke `proc` (it requires the Oracle Client install). Instead, we:

  1. Split the source into C regions and `EXEC SQL …` regions.
  2. Run the shared C extractor (`extract_c_facts`) over the C regions
     with the SQL blanked out — line numbers preserved.
  3. Run a small SQL extractor over the EXEC SQL regions to emit
     `SQL_STATEMENT` facts.

Recognized SQL forms (case-insensitive):
  EXEC SQL SELECT … FROM … [WHERE …];                    → operation=select
  EXEC SQL INSERT INTO <tbl> …;                          → operation=insert
  EXEC SQL UPDATE <tbl> SET …;                           → operation=update
  EXEC SQL DELETE FROM <tbl> …;                          → operation=delete
  EXEC SQL MERGE INTO <tbl> …;                           → operation=merge
  EXEC SQL CALL <pkg.proc>(:bind);                       → operation=call,
                                                            target_proc set
  EXEC SQL EXECUTE PROCEDURE <pkg.proc>(:bind);          → operation=execute,
                                                            target_proc set
  EXEC SQL EXECUTE … BEGIN <pkg.proc>(…); END;           → operation=execute,
                                                            target_proc set
"""

from __future__ import annotations

import re
from pathlib import Path

from core.facts import Fact, FactKind
from core.languages.c.grammar import extract_c_facts, strip_c_noise
from ingestion.grammars.grammar import Grammar

_IDENT = r"[A-Za-z][A-Za-z0-9_$#]*"
_QUAL = rf"(?:{_IDENT}\.){{0,2}}{_IDENT}"

# `EXEC SQL <body> ;` — Pro*C also accepts `END-EXEC;` but most code uses `;`.
# We match laz­ily and explicitly stop at the first `;` so multi-statement
# blocks split correctly.
_RE_EXEC_SQL = re.compile(
    r"EXEC\s+SQL\b(.+?);", re.IGNORECASE | re.DOTALL
)

# Table-touching ops inside an EXEC SQL block.
_RE_SELECT = re.compile(
    r"\bSELECT\b.+?\bFROM\s+(?P<tables>[^;]+?)(?:\bWHERE\b|;|\bORDER\b|\bGROUP\b|$)",
    re.IGNORECASE | re.DOTALL,
)
_RE_INSERT = re.compile(rf"\bINSERT\s+INTO\s+({_QUAL})\b", re.IGNORECASE)
_RE_UPDATE = re.compile(rf"\bUPDATE\s+({_QUAL})\b", re.IGNORECASE)
_RE_DELETE = re.compile(rf"\bDELETE\s+FROM\s+({_QUAL})\b", re.IGNORECASE)
_RE_MERGE = re.compile(rf"\bMERGE\s+INTO\s+({_QUAL})\b", re.IGNORECASE)
_RE_CALL = re.compile(rf"\bCALL\s+({_QUAL})\s*\(", re.IGNORECASE)
_RE_EXEC_PROC = re.compile(
    rf"\bEXECUTE(?:\s+PROCEDURE)?\s+({_QUAL})\s*\(?", re.IGNORECASE
)
# `EXEC SQL EXECUTE BEGIN pkg.proc(:v); END;` is the standard Pro*C form
# for calling stored procedures.
_RE_BEGIN_BLOCK = re.compile(
    rf"\bBEGIN\s+({_QUAL})\s*\(", re.IGNORECASE
)

_BLOCK_KEYWORDS = frozenset({"begin", "end", "declare"})


class ProCGrammar(Grammar):
    suffixes = (".pc", ".pcc")

    def extract(self, file: Path, content: str, *, repo_id: str) -> list[Fact]:
        try:
            return _extract(file, content, repo_id=repo_id)
        except Exception:
            return []


def _extract(file: Path, content: str, *, repo_id: str) -> list[Fact]:
    file_str = str(file)
    facts: list[Fact] = []

    # 1. Find the EXEC SQL blocks and emit SQL_STATEMENT facts for them.
    sql_spans: list[tuple[int, int]] = []
    for m in _RE_EXEC_SQL.finditer(content):
        sql_spans.append((m.start(), m.end()))
        body = m.group(1)
        line = _line_of(content, m.start())
        facts.extend(_sql_statements(body, file_str, repo_id, base_line=line))

    # 2. Replace EXEC SQL regions with blanks (preserving line numbers) so
    # the C extractor doesn't try to parse the SQL as C.
    blanked = _blank_spans(content, sql_spans)
    # Run the shared C extractor over the blanked source.
    facts.extend(extract_c_facts(file, blanked, repo_id=repo_id))

    return facts


def _blank_spans(text: str, spans: list[tuple[int, int]]) -> str:
    if not spans:
        return text
    out: list[str] = []
    last = 0
    for start, end in sorted(spans):
        out.append(text[last:start])
        # Preserve newlines so reported lines stay accurate.
        out.append("".join("\n" if c == "\n" else " " for c in text[start:end]))
        last = end
    out.append(text[last:])
    return "".join(out)


def _sql_statements(body: str, file: str, repo_id: str, *, base_line: int) -> list[Fact]:
    """Parse the body of one EXEC SQL block. The block may contain one or
    more statements separated by `;`, but in practice Pro*C blocks are
    single-statement — we emit one fact per recognized operation found."""
    out: list[Fact] = []
    cleaned = strip_c_noise(body)  # strip C-style string literals inside SQL

    # CALL / EXECUTE forms — emit as `call` operation with `target_proc`.
    # `EXECUTE BEGIN <pkg.proc>(...); END;` is the standard Pro*C wrapper
    # and would match `_RE_EXEC_PROC` (capturing "BEGIN") AND
    # `_RE_BEGIN_BLOCK` (capturing the real proc). Skip the "BEGIN" capture.
    for m in _RE_CALL.finditer(cleaned):
        target = m.group(1).lower()
        if target in _BLOCK_KEYWORDS:
            continue
        out.append(_sql_fact(file, repo_id, m, operation="call",
                             target_proc=target, base_line=base_line))
    for m in _RE_EXEC_PROC.finditer(cleaned):
        target = m.group(1).lower()
        if target in _BLOCK_KEYWORDS:
            continue
        out.append(_sql_fact(file, repo_id, m, operation="execute",
                             target_proc=target, base_line=base_line))
    for m in _RE_BEGIN_BLOCK.finditer(cleaned):
        target = m.group(1).lower()
        if target in _BLOCK_KEYWORDS:
            continue
        out.append(_sql_fact(file, repo_id, m, operation="execute",
                             target_proc=target, base_line=base_line))

    # Table-touching statements.
    for m in _RE_SELECT.finditer(cleaned):
        tables = _parse_from_clause(m.group("tables") or "")
        out.append(_sql_fact(file, repo_id, m, operation="select",
                             tables=tables, base_line=base_line))
    for op, regex in (
        ("insert", _RE_INSERT),
        ("update", _RE_UPDATE),
        ("delete", _RE_DELETE),
        ("merge", _RE_MERGE),
    ):
        for m in regex.finditer(cleaned):
            out.append(
                _sql_fact(
                    file, repo_id, m, operation=op,
                    tables=[m.group(1).lower()], base_line=base_line,
                )
            )
    return out


def _sql_fact(
    file: str,
    repo_id: str,
    m: re.Match,
    *,
    operation: str,
    tables: list[str] | None = None,
    target_proc: str = "",
    base_line: int,
) -> Fact:
    # base_line is the line where the enclosing EXEC SQL started; we add
    # the in-body offset so multi-line SQL inside one block still produces
    # accurate-ish line numbers.
    in_block_line = m.string.count("\n", 0, m.start())
    line = base_line + in_block_line
    raw = m.group(0).strip()
    if len(raw) > 200:
        raw = raw[:197] + "..."
    return Fact(
        kind=FactKind.SQL_STATEMENT,
        file=file,
        line=line,
        repo_id=repo_id,
        data={
            "operation": operation,
            "tables": tables or [],
            "target_proc": target_proc,
            "enclosing_symbol": "",
            "raw": raw,
        },
    )


def _parse_from_clause(s: str) -> list[str]:
    tokens = re.split(r"[\s,]+", s.strip())
    out: list[str] = []
    skip_next = False
    join_kw = {"join", "left", "right", "inner", "outer", "full", "cross", "on", "using"}
    for tok in tokens:
        if not tok:
            continue
        low = tok.lower()
        if skip_next:
            skip_next = False
            continue
        if low in join_kw:
            continue
        if low == "as":
            skip_next = True
            continue
        if re.match(rf"^{_QUAL}$", tok):
            out.append(low)
    seen: set[str] = set()
    deduped: list[str] = []
    for t in out:
        if t not in seen:
            seen.add(t)
            deduped.append(t)
    return deduped


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1
