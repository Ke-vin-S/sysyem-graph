"""Prompt builders for LLM-backed tasks.

This module is the single source of truth for every prompt the system
sends to an LLM. The same text is used by:

  * `AnthropicClient` (and any future provider) to actually call the API.
  * `docs/LLM_PROMPTS.md`, which is generated from these strings so the
    documented prompt is guaranteed to match what runs in production.

Three tasks are covered today:

  1. **extract-facts** — the unrecognised-language fallback. When the
     walker sees a file whose extension isn't claimed by any native
     grammar, it calls the LLM with this prompt. The model returns a
     JSON list of structured Fact records that the system treats as if
     they came from a native parser.

  2. **learn-profile** — one-shot per repo, used to extend the stock
     framework knowledge with in-house conventions (custom test
     decorators, internal HTTP clients, etc.).

  3. **resolve-subgraph** — a focused Q&A the resolvers fall back to
     when a deterministic answer isn't available (e.g. "which of these
     candidate handlers is bound to `/orders`?").

Design choices baked into the prompts:

  * The model is told to respond with a single JSON object and NO
    surrounding prose. We parse with `json.loads`; any leading/trailing
    text breaks the parse and the response is dropped (better than
    hallucinated facts polluting the graph).
  * Schemas are inlined into the prompt itself, not just the system
    message, so a missing system prompt doesn't change the contract.
  * The model is explicitly told to set `confidence: 0.0` or omit a
    record when unsure — "skip" beats "guess" for an impact graph.
"""

from __future__ import annotations

from dataclasses import dataclass

# Hard cap on how much source we send per file. The walker already
# rejects files >1 MB, but real LLM context cost is per-token: 12 KB of
# code is ~3K tokens, which is comfortable. Files larger than this get
# their tail truncated and an "[…truncated…]" marker appended so the
# model knows it's looking at a prefix.
MAX_CONTENT_CHARS = 12_000


# ---- system prompt -------------------------------------------------------
# Shared across every call. Short on purpose: the heavy lifting is in the
# user message, which is per-task and contains the schema.

SYSTEM_PROMPT = (
    "You are a static-analysis assistant for a multi-language code-impact "
    "graph. Always respond with a single JSON object that matches the "
    "schema in the user message. Output NO prose, NO markdown fences, NO "
    "comments — just the JSON object. When unsure about any field, omit "
    "the offending record entirely rather than guessing; an empty list is "
    "always a valid response."
)


# ---- extract-facts -------------------------------------------------------

# Language hint from extension. Not used for routing — that already
# happened in the walker — but for the LLM's benefit so it knows whether
# `:=` is Go or Pascal, etc. Anything not listed gets the literal
# extension as the hint.
_EXTENSION_LANGUAGES: dict[str, str] = {
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".swift": "Swift",
    ".ts": "TypeScript",
    ".tsx": "TypeScript (TSX)",
    ".js": "JavaScript",
    ".jsx": "JavaScript (JSX)",
    ".cs": "C#",
    ".fs": "F#",
    ".scala": "Scala",
    ".clj": "Clojure",
    ".cljs": "ClojureScript",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".erl": "Erlang",
    ".hs": "Haskell",
    ".lua": "Lua",
    ".php": "PHP",
    ".pl": "Perl",
    ".pm": "Perl",
    ".r": "R",
    ".dart": "Dart",
    ".m": "Objective-C",
    ".mm": "Objective-C++",
    ".groovy": "Groovy",
    ".jl": "Julia",
    ".nim": "Nim",
    ".zig": "Zig",
    ".cob": "COBOL",
    ".cbl": "COBOL",
    ".f90": "Fortran",
    ".f95": "Fortran",
    ".rpg": "RPG",
}


def language_for(extension: str) -> str:
    """Best-effort English name for a file extension. Used in prompts so
    the LLM doesn't have to guess from the extension alone."""
    ext = extension.lower()
    return _EXTENSION_LANGUAGES.get(ext, ext.lstrip(".") or "unknown")


# The JSON shape we ask for is intentionally minimal: one record per
# observation, each with `kind` (one of the FactKind values), `line`,
# optional `line_end`, and a `data` payload whose shape depends on the
# kind. The full kind list and per-kind data shapes are documented in
# the user prompt itself so the model has the schema in-context.

_EXTRACT_FACTS_USER_TEMPLATE = """\
Task: extract structured facts from a source-code file written in {language}.

The file is {file_path} ({size_bytes} bytes shown). Treat line numbers as
1-indexed against the content below. Emit one record per observation; do
NOT emit records for trivia (comments, empty lines, formatting).

Allowed `kind` values and the `data` shape each one requires:

  - "symbol":  a function/method/class/variable DEFINITION.
        data: {{
          "sym_kind": "function" | "method" | "class" | "variable" | "interface",
          "name": <string>,
          "enclosing_class": <string>,      # "" if top-level
          "enclosing_package": <string>     # "" if none
        }}

  - "class_def": a class / struct / interface declaration.
        data: {{ "name": <string>, "kind": "class" | "struct" | "interface" | "trait" }}

  - "import": an import / require / use statement.
        data: {{
          "module": <string>,               # dotted/slashed path as written
          "names": [<string>, ...],         # specific symbols, or [] for whole-module
          "alias": <string>                 # "" when not aliased
        }}

  - "call": a function or method INVOCATION (not definition).
        data: {{
          "callee": <string>,               # fully-qualified when known, e.g. "pkg.func" or "obj.method"
          "receiver": <string>,             # the object/expression on the left of "."; "" if free function
          "method": <string>,               # bare method/function name
          "args": [<string>, ...],          # positional arguments as source text, truncated to 80 chars each
          "kwargs": {{ <name>: <value>, ... }}
        }}

  - "decorator" or "annotation":  Python decorator / Java/Kotlin/C# annotation
    attached to a symbol immediately following on a subsequent line.
        data: {{
          "callee": <string>,               # e.g. "router.get", "Override"
          "args": [<string>, ...],
          "kwargs": {{ <name>: <value>, ... }},
          "target_symbol": <string>         # name of the symbol being decorated
        }}

  - "string_literal": a string worth keeping (URL, SQL, regex,
    config-looking key). Skip casual strings (error messages, prints).
        data: {{ "value": <string>, "context": "url" | "sql" | "path" | "other" }}

Rules:
  1. Output exactly ONE JSON object: {{"facts": [<record>, ...]}}.
     No prose, no markdown fences, no trailing commas.
  2. Use the kinds listed above. Any other `kind` value is invalid and
     the record will be dropped.
  3. Each record must include {{"kind", "line", "data"}}. `line_end` is
     optional and should be the closing line for multi-line definitions.
  4. When the language uses case-insensitive identifiers (SQL, COBOL,
     Fortran), lowercase all names before emitting.
  5. If you cannot identify ANY facts confidently, return {{"facts": []}}.
     A small high-precision set is much better than a large noisy one.

Content (line-numbered, 1-indexed, possibly truncated):
---
{numbered_content}
---

Respond with the JSON object only.
"""


@dataclass(frozen=True)
class ExtractFactsPrompt:
    """Built prompt for the `extract-facts` task.

    Keep the system / user split because providers route them through
    different cache mechanisms. The Anthropic SDK takes them as separate
    arguments anyway."""

    system: str
    user: str
    language: str
    """The hint the prompt was built for; useful for logs/traces."""


def build_extract_facts_prompt(
    *,
    file_path: str,
    content: str,
    extension: str,
) -> ExtractFactsPrompt:
    """Render the extract-facts prompt for one file.

    The content is line-numbered before being injected, so the model can
    return real line numbers without us having to re-tokenize the
    response. The content is also truncated to `MAX_CONTENT_CHARS`."""
    language = language_for(extension)
    truncated = content
    if len(truncated) > MAX_CONTENT_CHARS:
        truncated = truncated[:MAX_CONTENT_CHARS] + "\n[…truncated…]"
    numbered = _add_line_numbers(truncated)
    user = _EXTRACT_FACTS_USER_TEMPLATE.format(
        language=language,
        file_path=file_path,
        size_bytes=len(content),
        numbered_content=numbered,
    )
    return ExtractFactsPrompt(system=SYSTEM_PROMPT, user=user, language=language)


def _add_line_numbers(text: str) -> str:
    """Prefix each line with `N: `. Cheap and deterministic; gives the
    model a stable handle on line positions."""
    lines = text.splitlines() or [""]
    width = len(str(len(lines)))
    return "\n".join(f"{i + 1:>{width}}: {line}" for i, line in enumerate(lines))


# ---- learn-profile -------------------------------------------------------

_LEARN_PROFILE_USER_TEMPLATE = """\
Task: examine these representative files from one repository and produce
a JSON overlay extending the stock framework knowledge. The goal is to
catch in-house conventions: custom test decorators, internal wrappers
around HTTP/queue clients, internal test bases, etc.

Output schema (single JSON object, no prose):
{{
  "test_annotations":         [<string>, ...],   // extra decorators that mark tests, e.g. "@acme_test"
  "mock_annotations":         [<string>, ...],   // extra mock-style decorators, e.g. "@acme.mock_db"
  "external_modules":         [<string>, ...],   // internal wrappers around outside services, e.g. "acme.http.client"
  "internal_test_wrappers":   [<string>, ...],   // base classes / fixtures specific to this repo's tests
  "notes":                    <string>           // short freeform note about what you saw
}}

Rules:
  1. Only include items NOT already obvious from stock frameworks (pytest,
     JUnit, FastAPI, Spring, etc.). Don't list `@pytest.fixture`.
  2. Empty arrays are fine. An empty overlay is a valid response.
  3. Output the JSON object only.

Repo: {repo_id}

Samples:
{samples_block}
"""


@dataclass(frozen=True)
class LearnProfilePrompt:
    system: str
    user: str


def build_learn_profile_prompt(
    *,
    repo_id: str,
    samples: list[tuple[str, str, str]],
) -> LearnProfilePrompt:
    """Build the learn-profile prompt.

    `samples` is a list of (path, content, notes) tuples; the caller
    decides how many and which files to send (usually 5–15 representative
    files: a handful of source, a handful of config, a couple of tests).
    Each sample is wrapped in a `===` delimiter block."""
    blocks: list[str] = []
    for path, content, notes in samples:
        body = content if len(content) <= MAX_CONTENT_CHARS else content[:MAX_CONTENT_CHARS] + "\n[…truncated…]"
        suffix = f" ({notes})" if notes else ""
        blocks.append(f"=== {path}{suffix} ===\n{body}")
    samples_block = "\n\n".join(blocks)
    user = _LEARN_PROFILE_USER_TEMPLATE.format(
        repo_id=repo_id,
        samples_block=samples_block,
    )
    return LearnProfilePrompt(system=SYSTEM_PROMPT, user=user)


# ---- resolve-subgraph ---------------------------------------------------

_RESOLVE_SUBGRAPH_USER_TEMPLATE = """\
Task: answer the question below using ONLY the facts and snippets
provided. Do not invent fields, candidate IDs, or files.

Question: {question}

Expected answer schema (single JSON object, no prose):
{schema_block}

Rules:
  1. If the answer is unclear, set `confidence: 0.0` and `target_id: null`
     (or whatever the schema's "unknown" sentinel is).
  2. Cite which fact IDs supported your answer in a `reason` field if
     the schema allows freeform text.
  3. Output the JSON object only.

Facts:
{facts_block}

Snippets:
{snippets_block}
"""


@dataclass(frozen=True)
class ResolveSubgraphPrompt:
    system: str
    user: str


def build_resolve_subgraph_prompt(
    *,
    question: str,
    expected_schema: dict[str, object],
    facts: list[dict[str, object]],
    snippets: dict[str, str],
) -> ResolveSubgraphPrompt:
    import json as _json

    schema_block = _json.dumps(expected_schema, indent=2) if expected_schema else "{ }"
    facts_block = _json.dumps(facts, indent=2, default=str) if facts else "(none)"
    snippets_block = (
        "\n\n".join(f"=== {path} ===\n{content}" for path, content in snippets.items())
        if snippets
        else "(none)"
    )
    user = _RESOLVE_SUBGRAPH_USER_TEMPLATE.format(
        question=question,
        schema_block=schema_block,
        facts_block=facts_block,
        snippets_block=snippets_block,
    )
    return ResolveSubgraphPrompt(system=SYSTEM_PROMPT, user=user)


__all__ = [
    "ExtractFactsPrompt",
    "LearnProfilePrompt",
    "MAX_CONTENT_CHARS",
    "ResolveSubgraphPrompt",
    "SYSTEM_PROMPT",
    "build_extract_facts_prompt",
    "build_learn_profile_prompt",
    "build_resolve_subgraph_prompt",
    "language_for",
]
