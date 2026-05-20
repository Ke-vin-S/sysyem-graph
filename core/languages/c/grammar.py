"""C grammar — regex-based extraction of functions, includes, calls.

No tree-sitter dependency in v1. The C surface we care about for impact
analysis is small enough that regex handles it reliably:
  * `#include "x.h"` / `#include <x.h>`        → IMPORT
  * function definitions (`ret_t name(args) { … }`) → SYMBOL
  * function call sites                         → CALL

False-positives we accept:
  * Function-like macros that look like calls. The downstream
    ShellInvokeResolver / OracleCallResolver filter by "did the callee
    resolve to a known SYMBOL?" so these die quietly later.
  * Function pointers used as values (`&do_thing`) — not captured (they
    don't match `name(`).

False-negatives we accept:
  * K&R-style declarations (`void f() int x; int y; { … }`) — rare in modern
    C, undefined behavior in C99+. Not worth handling.
"""

from __future__ import annotations

import re
from pathlib import Path

from core.facts import Fact, FactKind
from ingestion.grammars.grammar import Grammar

# ---- shared regex toolkit (also used by ProCGrammar) ----------------------

# C identifier — letter/underscore, then letters/digits/underscores.
_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"

# `#include "file"` or `#include <file>` — capture the path verbatim.
RE_INCLUDE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.MULTILINE)

# Function definition: `[static|extern|inline] type name(args) {`.
# We accept multi-token return types (`unsigned int`, `struct foo *`) by
# anchoring on `name(args) {` and walking the preceding line for the
# storage-class keywords.
RE_FUNC_DEF = re.compile(
    rf"""
    (?:(?:^|;|\}})\s*)               # at start-of-stmt boundary
    (?:(?:static|extern|inline|const|register|volatile)\s+)*
    (?:[A-Za-z_][A-Za-z0-9_\s\*]*?\s+)   # return type (greedy across spaces, includes *)
    ({_IDENT})                       # function name
    \s*\(([^)]*)\)                   # argument list
    \s*\{{                           # opening brace — proves it's a definition,
                                     # not a declaration
    """,
    re.VERBOSE | re.MULTILINE,
)

# Call site: `<name>(`. Same caveat as PL/SQL — the resolver filters out
# noise; the grammar just records what looks like a call.
RE_CALL = re.compile(rf"\b({_IDENT})\s*\(")

# Comments + strings — replaced with same-length blanks via `_strip_noise`
# so following keyword scans aren't fooled by `/* a call(); */`.
RE_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
RE_LINE_COMMENT = re.compile(r"//[^\n]*")
RE_STRING = re.compile(r'"(?:\\.|[^"\\])*"')
RE_CHAR = re.compile(r"'(?:\\.|[^'\\])*'")

# Keywords that are not function calls. Reserved words + common C stdlib
# control-flow constructs.
_C_NOT_A_CALL = frozenset(
    {
        "if", "else", "while", "for", "do", "switch", "case", "default",
        "return", "break", "continue", "goto", "sizeof", "typeof", "typedef",
        "struct", "union", "enum", "static", "extern", "inline", "const",
        "register", "volatile", "auto", "void", "int", "char", "short",
        "long", "float", "double", "signed", "unsigned", "_Bool", "_Complex",
        "defined",
    }
)


class CGrammar(Grammar):
    suffixes = (".c", ".h")

    def extract(self, file: Path, content: str, *, repo_id: str) -> list[Fact]:
        try:
            return extract_c_facts(file, content, repo_id=repo_id)
        except Exception:
            return []


# ---- module-level helper, reused by ProCGrammar ---------------------------


def extract_c_facts(file: Path, content: str, *, repo_id: str) -> list[Fact]:
    """Run the C extraction pass over `content`. Used by both `CGrammar`
    (raw `.c`/`.h`) and `ProCGrammar` (after the `EXEC SQL` blocks have been
    blanked out)."""
    file_str = str(file)
    facts: list[Fact] = []

    # Includes — scan the RAW content because `strip_c_noise` would blank
    # the `"..."` part of `#include "billing.h"`.
    for m in RE_INCLUDE.finditer(content):
        line = _line_of(content, m.start())
        path = m.group(1)
        facts.append(
            Fact(
                kind=FactKind.IMPORT,
                file=file_str,
                line=line,
                repo_id=repo_id,
                data={
                    "module": path,
                    "names": [],
                    "alias": "",
                    "system": content[m.start() : m.end()].rstrip().endswith(">"),
                },
            )
        )

    cleaned = strip_c_noise(content)

    # Function definitions.
    defined: list[tuple[str, int, int]] = []  # (name, line, end_offset)
    for m in RE_FUNC_DEF.finditer(cleaned):
        name = m.group(1)
        if name in _C_NOT_A_CALL:
            continue
        line = _line_of(cleaned, m.start(1))
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
        defined.append((name, line, m.end()))

    # Call sites. We use the cleaned source so commented-out calls don't fire.
    # Exclude positions inside function-definition header `name(args)` — they
    # match RE_CALL trivially.
    def_header_spans = [(m.start(1), m.end()) for m in RE_FUNC_DEF.finditer(cleaned)]
    for m in RE_CALL.finditer(cleaned):
        if any(start <= m.start() < end for start, end in def_header_spans):
            continue
        name = m.group(1)
        if name in _C_NOT_A_CALL:
            continue
        line = _line_of(cleaned, m.start())
        facts.append(
            Fact(
                kind=FactKind.CALL,
                file=file_str,
                line=line,
                repo_id=repo_id,
                data={
                    "callee": name,
                    "receiver": "",
                    "method": name,
                    "args": [],
                    "kwargs": {},
                },
            )
        )
    return facts


def strip_c_noise(content: str) -> str:
    """Blank out comments and string/char literals, preserving line numbers."""
    def _blank(match: re.Match) -> str:
        # Preserve newlines so line numbers don't shift.
        s = match.group(0)
        return "".join("\n" if c == "\n" else " " for c in s)

    s = RE_BLOCK_COMMENT.sub(_blank, content)
    s = RE_LINE_COMMENT.sub(_blank, s)
    s = RE_STRING.sub(_blank, s)
    s = RE_CHAR.sub(_blank, s)
    return s


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1
