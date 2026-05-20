"""PL/SQL grammar — packages, procedures, functions, triggers, embedded SQL.

Regex-based (no native PL/SQL parser in the Python stdlib, and pulling
ANTLR runtime + a PL/SQL grammar would be a bigger dependency than is
warranted for the structural extraction we need here).

Identifiers in PL/SQL are case-insensitive; we lowercase every name we
emit so resolvers can index without re-normalizing.

Files claimed:
  .pks  package spec
  .pkb  package body
  .sql  generic — works for standalone procedures, triggers, views, etc.
  .plsql, .prc, .fnc, .trg, .vw  legacy site-specific conventions

Emits:
  CLASS_DEF        kind="package" for every `CREATE [OR REPLACE] PACKAGE …`
  SYMBOL           sym_kind="procedure"|"function"|"trigger" with
                   enclosing_package set when nested in a package body
  CALL             every `[<pkg>.]<name>(...)` call site inside a body
  SQL_STATEMENT    SELECT / INSERT / UPDATE / DELETE / MERGE / TRUNCATE /
                   CALL inside procedure bodies, plus `EXECUTE PROCEDURE`
                   forms. `data.tables` is populated for table-touching ops;
                   `data.target_proc` is populated for CALL forms.

The grammar never raises — malformed input returns [].
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from core.facts import Fact, FactKind
from ingestion.grammars.grammar import Grammar

logger = logging.getLogger(__name__)


# ---- regex toolkit --------------------------------------------------------

# A PL/SQL identifier — letters, digits, `_`, `$`, `#`. Must start with a letter.
_IDENT = r"[A-Za-z][A-Za-z0-9_$#]*"
# A possibly-qualified identifier: `schema.pkg.name` (we cap at three parts).
_QUAL = rf"(?:{_IDENT}\.){{0,2}}{_IDENT}"

_RE_PACKAGE = re.compile(
    rf"\bCREATE\s+(?:OR\s+REPLACE\s+)?PACKAGE(?:\s+BODY)?\s+({_QUAL})\b",
    re.IGNORECASE,
)
_RE_PROCEDURE = re.compile(
    rf"\bPROCEDURE\s+({_IDENT})\b", re.IGNORECASE
)
_RE_FUNCTION = re.compile(
    rf"\bFUNCTION\s+({_IDENT})\b", re.IGNORECASE
)
_RE_TRIGGER = re.compile(
    rf"\bCREATE\s+(?:OR\s+REPLACE\s+)?TRIGGER\s+({_QUAL})\b", re.IGNORECASE
)
# A call site: `[<qual>.]<ident>(`. We require the open-paren so naked
# identifiers (variable references) don't match.
_RE_CALL = re.compile(rf"\b({_QUAL})\s*\(", re.IGNORECASE)

# Statement-introducing keywords for SQL_STATEMENT extraction.
_RE_SELECT = re.compile(r"\bSELECT\b\s.+?\bFROM\s+(?P<tables>[^;]+?)(?:\bWHERE\b|;|\bGROUP\b|\bORDER\b|\bUNION\b)", re.IGNORECASE | re.DOTALL)
_RE_INSERT = re.compile(rf"\bINSERT\s+INTO\s+({_QUAL})\b", re.IGNORECASE)
_RE_UPDATE = re.compile(rf"\bUPDATE\s+({_QUAL})\b", re.IGNORECASE)
_RE_DELETE = re.compile(rf"\bDELETE\s+FROM\s+({_QUAL})\b", re.IGNORECASE)
_RE_MERGE = re.compile(rf"\bMERGE\s+INTO\s+({_QUAL})\b", re.IGNORECASE)
_RE_TRUNCATE = re.compile(rf"\bTRUNCATE\s+TABLE\s+({_QUAL})\b", re.IGNORECASE)
_RE_CALL_KW = re.compile(rf"\bCALL\s+({_QUAL})\s*\(", re.IGNORECASE)
_RE_EXEC_PROC = re.compile(
    rf"\bEXECUTE(?:\s+PROCEDURE)?\s+({_QUAL})\s*\(?", re.IGNORECASE
)

# Comments — single-line `--` and multi-line `/* … */`. We strip these before
# scanning so a `-- procedure foo` line doesn't generate a spurious SYMBOL.
_RE_LINE_COMMENT = re.compile(r"--[^\n]*")
_RE_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_RE_STRING = re.compile(r"'(?:''|[^'])*'")  # PL/SQL strings, with '' escape

# Keywords that look like calls but aren't worth recording. Mostly control
# flow + builtins. Resolvers can also filter, but excluding the obvious ones
# at extraction time keeps fact counts manageable.
_NOT_A_CALL = frozenset(
    {
        # control-flow
        "if", "elsif", "else", "end", "loop", "for", "while", "exit",
        "return", "raise", "exception", "when", "then", "begin", "declare",
        "case", "in", "out", "is", "as", "open", "close", "fetch", "into",
        "values", "set", "and", "or", "not", "between", "like", "null",
        "true", "false", "default", "constant",
        # cursors / DDL
        "cursor", "type", "subtype", "record", "table", "of", "ref",
        # PL/SQL builtin pseudo-functions; uninteresting for the call graph
        "to_char", "to_number", "to_date", "nvl", "decode", "coalesce",
        "count", "sum", "avg", "min", "max", "trim", "ltrim", "rtrim",
        "upper", "lower", "substr", "length", "instr", "round", "trunc",
        "mod", "abs", "sysdate", "current_timestamp", "current_date",
        "user", "uid", "rowid", "rownum",
    }
)


class PlSqlGrammar(Grammar):
    suffixes = (".pks", ".pkb", ".sql", ".plsql", ".prc", ".fnc", ".trg", ".vw")

    def extract(self, file: Path, content: str, *, repo_id: str) -> list[Fact]:
        try:
            return list(self._extract(file, content, repo_id=repo_id))
        except Exception:
            logger.warning("plsql grammar: extraction failed for %s", file)
            return []

    # ---- internal ------------------------------------------------------

    def _extract(self, file: Path, content: str, *, repo_id: str) -> list[Fact]:
        file_str = str(file)
        cleaned = _strip_noise(content)
        facts: list[Fact] = []

        # Packages first — we need enclosing_package to set on later SYMBOLs.
        package_ranges: list[tuple[str, int, int]] = []  # (name, start_line, end_line)
        for m in _RE_PACKAGE.finditer(cleaned):
            name = m.group(1).lower()
            line = _line_of(cleaned, m.start())
            # We don't track end-line precisely; treat the package as
            # extending to the end of file (PL/SQL files conventionally
            # hold one package).
            package_ranges.append((name, line, len(cleaned)))
            facts.append(
                Fact(
                    kind=FactKind.CLASS_DEF,
                    file=file_str,
                    line=line,
                    repo_id=repo_id,
                    data={
                        "name": name,
                        "kind": "package",
                        "modifiers": [],
                    },
                )
            )

        # Procedures and functions.
        for m in _RE_PROCEDURE.finditer(cleaned):
            facts.append(
                _symbol_fact(
                    file_str, repo_id, m, sym_kind="procedure",
                    enclosing=_enclosing_package(package_ranges, m.start(), cleaned),
                )
            )
        for m in _RE_FUNCTION.finditer(cleaned):
            facts.append(
                _symbol_fact(
                    file_str, repo_id, m, sym_kind="function",
                    enclosing=_enclosing_package(package_ranges, m.start(), cleaned),
                )
            )
        for m in _RE_TRIGGER.finditer(cleaned):
            facts.append(
                _symbol_fact(file_str, repo_id, m, sym_kind="trigger", enclosing="")
            )

        # Cross-procedure calls — recorded only when the callee is qualified
        # (`pkg.proc(...)`) or matches a known procedure name in this file.
        # Declaration sites (`PROCEDURE foo(...)` / `FUNCTION foo(...)`)
        # also match the call regex; we filter them out by looking back at
        # the preceding non-whitespace word.
        symbol_names = {f.data.get("name") for f in facts if f.kind is FactKind.SYMBOL}
        for m in _RE_CALL.finditer(cleaned):
            callee = m.group(1).lower()
            simple = callee.rsplit(".", 1)[-1]
            if simple in _NOT_A_CALL:
                continue
            if _preceded_by_declarator(cleaned, m.start()):
                continue
            # Qualified callees (`a.b` or `schema.pkg.proc`) are always kept.
            # Bare callees only kept when they match a local SYMBOL — keeps
            # noise low without losing intra-package calls.
            if "." not in callee and simple not in symbol_names:
                continue
            line = _line_of(cleaned, m.start())
            receiver = ""
            method = simple
            if "." in callee:
                receiver, method = callee.rsplit(".", 1)
            facts.append(
                Fact(
                    kind=FactKind.CALL,
                    file=file_str,
                    line=line,
                    repo_id=repo_id,
                    data={
                        "callee": callee,
                        "receiver": receiver,
                        "method": method,
                        "args": [],
                        "kwargs": {},
                    },
                )
            )

        # SQL statements embedded in procedure bodies.
        facts.extend(_sql_statements(cleaned, file_str, repo_id))
        return facts


# ---- helpers --------------------------------------------------------------


def _symbol_fact(file: str, repo_id: str, m: re.Match, *, sym_kind: str, enclosing: str) -> Fact:
    name = m.group(1).lower()
    line = _line_of(m.string, m.start())
    return Fact(
        kind=FactKind.SYMBOL,
        file=file,
        line=line,
        repo_id=repo_id,
        data={
            "sym_kind": sym_kind,
            "name": name,
            "enclosing_class": enclosing,
            "enclosing_package": enclosing,
            "is_async": False,
            "modifiers": [],
        },
    )


def _enclosing_package(ranges: list[tuple[str, int, int]], offset: int, cleaned: str) -> str:
    line = _line_of(cleaned, offset)
    for name, start, _end in ranges:
        if line >= start:
            return name
    return ""


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


_DECLARATORS = frozenset({"procedure", "function", "trigger"})


def _preceded_by_declarator(text: str, offset: int) -> bool:
    """True when the identifier at `offset` is a declaration site
    (preceded by `PROCEDURE`, `FUNCTION`, or `TRIGGER`)."""
    # Walk backward over whitespace, then grab the preceding word.
    i = offset - 1
    while i >= 0 and text[i].isspace():
        i -= 1
    j = i
    while j >= 0 and (text[j].isalnum() or text[j] == "_"):
        j -= 1
    word = text[j + 1 : i + 1].lower()
    return word in _DECLARATORS


def _strip_noise(content: str) -> str:
    """Replace comments and string literals with same-length blanks so the
    line numbers of surviving keywords remain accurate."""
    def _blank(match: re.Match) -> str:
        return " " * (match.end() - match.start())

    s = _RE_BLOCK_COMMENT.sub(_blank, content)
    s = _RE_LINE_COMMENT.sub(_blank, s)
    s = _RE_STRING.sub(_blank, s)
    return s


def _sql_statements(text: str, file: str, repo_id: str) -> list[Fact]:
    """Find SELECT / INSERT / UPDATE / DELETE / MERGE / TRUNCATE / CALL /
    EXECUTE PROCEDURE statements and emit `SQL_STATEMENT` facts."""
    out: list[Fact] = []

    # SELECT — extract tables from the FROM clause (best-effort; comma-list,
    # may include aliases and joins).
    for m in _RE_SELECT.finditer(text):
        tables = _parse_from_clause(m.group("tables") or "")
        out.append(_sql_fact(file, repo_id, m, operation="select", tables=tables))

    for op, regex in (
        ("insert", _RE_INSERT),
        ("update", _RE_UPDATE),
        ("delete", _RE_DELETE),
        ("merge", _RE_MERGE),
        ("truncate", _RE_TRUNCATE),
    ):
        for m in regex.finditer(text):
            tables = [m.group(1).lower()]
            out.append(_sql_fact(file, repo_id, m, operation=op, tables=tables))

    for m in _RE_CALL_KW.finditer(text):
        out.append(
            _sql_fact(file, repo_id, m, operation="call", target_proc=m.group(1).lower())
        )
    for m in _RE_EXEC_PROC.finditer(text):
        out.append(
            _sql_fact(file, repo_id, m, operation="execute", target_proc=m.group(1).lower())
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
) -> Fact:
    line = _line_of(m.string, m.start())
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
    """Pull table names out of a FROM clause. Handles commas, JOIN syntax,
    aliases. Best-effort — anything that doesn't look like an identifier is
    skipped."""
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
        if low in {"as"}:
            skip_next = True
            continue
        if re.match(rf"^{_QUAL}$", tok):
            out.append(low)
    # Dedupe preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for t in out:
        if t not in seen:
            seen.add(t)
            deduped.append(t)
    return deduped
