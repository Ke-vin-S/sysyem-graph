"""Shell-script grammar.

The interesting facts for impact analysis are:
  * Shell function definitions (so we can attach calls to an enclosing
    function, like procedures in PL/SQL or methods in Java).
  * Command invocations — every line that starts with a word followed by
    arguments. This includes invocations of compiled binaries
    (`./bin/charge_loader`), other shell scripts (`./scripts/load.sh`), and
    standard utilities (`cp`, `mkdir`). The resolver filters by "does this
    callee match a known artifact?".
  * SQL*Plus invocations (`sqlplus user/pass@db @script.sql`) — emitted as
    `SQL_STATEMENT` with `operation="script"` and `target_proc=<basename>`
    so the resolver can link the script to the calling shell function.
"""

from __future__ import annotations

import re
from pathlib import Path

from core.facts import Fact, FactKind
from ingestion.grammars.grammar import Grammar

# A shell identifier — letters, digits, `_`. Starts with letter/underscore.
_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"

# `function foo()` OR `foo()` at the start of a line (any indentation
# allowed), followed by `{` either same-line or on a following line.
_RE_FUNCTION_DEF = re.compile(
    r"^[ \t]*(?:function\s+)?(" + _IDENT + r")\s*\(\s*\)\s*\{",
    re.MULTILINE,
)

# Command line: optional leading whitespace, then a word that's either a
# path-like token (./foo, /bin/foo, $VAR/foo) or a bare identifier,
# followed by whitespace and the rest of the line.
_RE_COMMAND = re.compile(
    r"""^[ \t]*
        (?P<callee>
          (?:\$\{[A-Za-z_][A-Za-z0-9_]*\}/)?            # var-dir/
          (?:\.{0,2}/)?                                  # ./ or ../
          [A-Za-z_][A-Za-z0-9_./\-]*                     # name
        )
        (?:\s|$)
    """,
    re.MULTILINE | re.VERBOSE,
)

# `sqlplus user/pass@db @file.sql` or `sqlplus ... @file.sql` more generally.
# `\@<path>` (with a leading space) is what SQL*Plus uses for "run a script";
# `<user>/<pwd>@<host>` puts `@` mid-token. We anchor on a space-preceded `@`
# to capture the script path and skip the login string.
_RE_SQLPLUS = re.compile(
    r"""\bsqlplus\b[^\n]*?(?:^|\s)@(?P<path>[^\s;|&<>]+)""",
    re.IGNORECASE | re.MULTILINE,
)

# Shell keywords / common builtins that aren't worth recording as calls.
_NOT_A_CALL = frozenset(
    {
        # Control flow + keywords
        "if", "then", "else", "elif", "fi", "for", "while", "do", "done",
        "case", "esac", "in", "function", "select", "until", "break",
        "continue", "return", "exit", "shift", "set", "unset", "export",
        "readonly", "declare", "local", "typeset", "trap", "eval", "exec",
        "source", "true", "false", "alias", "unalias",
        # Test forms
        "[", "[[", "test",
        # Comments / output
        "#", ":", "echo", "printf",
    }
)


class ShGrammar(Grammar):
    suffixes = (".sh", ".bash", ".ksh", ".zsh")

    def extract(self, file: Path, content: str, *, repo_id: str) -> list[Fact]:
        try:
            return _extract(file, content, repo_id=repo_id)
        except Exception:
            return []


def _extract(file: Path, content: str, *, repo_id: str) -> list[Fact]:
    file_str = str(file)
    cleaned = _strip_noise(content)
    facts: list[Fact] = []

    # Function definitions.
    func_lines: dict[str, int] = {}
    for m in _RE_FUNCTION_DEF.finditer(cleaned):
        name = m.group(1)
        line = _line_of(cleaned, m.start())
        func_lines[name] = line
        facts.append(
            Fact(
                kind=FactKind.SYMBOL,
                file=file_str,
                line=line,
                repo_id=repo_id,
                data={
                    "sym_kind": "function",
                    "name": name,
                    "enclosing_class": "",
                    "is_async": False,
                    "modifiers": [],
                },
            )
        )

    # Command invocations.
    for m in _RE_COMMAND.finditer(cleaned):
        callee = m.group("callee")
        # Skip variable assignments — `FOO=bar` matches but isn't a call.
        if "=" in callee:
            continue
        # Skip function-def captures (the same line matches both regexes).
        rest = cleaned[m.end() - 1 :]
        if rest.lstrip().startswith("()"):
            continue
        if callee in _NOT_A_CALL:
            continue
        # Skip if this position is the function-def header itself.
        if callee in func_lines and _line_of(cleaned, m.start()) == func_lines[callee]:
            continue
        line = _line_of(cleaned, m.start())
        facts.append(
            Fact(
                kind=FactKind.CALL,
                file=file_str,
                line=line,
                repo_id=repo_id,
                data={
                    "callee": callee,
                    "receiver": "",
                    "method": callee.split("/")[-1],
                    "args": [],
                    "kwargs": {},
                },
            )
        )

    # sqlplus invocations.
    for m in _RE_SQLPLUS.finditer(cleaned):
        path = m.group("path")
        line = _line_of(cleaned, m.start())
        facts.append(
            Fact(
                kind=FactKind.SQL_STATEMENT,
                file=file_str,
                line=line,
                repo_id=repo_id,
                data={
                    "operation": "script",
                    "tables": [],
                    "target_proc": path,
                    "enclosing_symbol": "",
                    "raw": m.group(0)[:200],
                },
            )
        )
    return facts


# Comments — `#` to end of line, but NOT in `${...}` constructs. We keep it
# simple: blank out everything after `#` when `#` is preceded by whitespace
# or start-of-line.
_RE_COMMENT = re.compile(r"(^|[ \t])#[^\n]*", re.MULTILINE)
_RE_HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?.*?^\1\s*$", re.DOTALL | re.MULTILINE)


def _strip_noise(content: str) -> str:
    """Blank comments + heredocs, preserving line numbers."""
    def _blank(match: re.Match) -> str:
        s = match.group(0)
        return "".join("\n" if c == "\n" else " " for c in s)

    s = _RE_HEREDOC.sub(_blank, content)
    s = _RE_COMMENT.sub(_blank, s)
    return s


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1
