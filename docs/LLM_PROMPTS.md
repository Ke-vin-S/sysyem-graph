# LLM prompts (copy-pasteable)

This doc collects every prompt the system sends to an LLM, in
copy-pasteable form. You can drop any of these straight into the
Anthropic / OpenAI / Gemini console (or `curl`) to get the same shape of
response the runtime expects — useful for debugging, manual evaluation,
or one-off impact reports without standing up the whole pipeline.

The prompts ship in code at `core/llm/prompts.py`; this doc is a mirror
of that source. If you ever find them drifting, the code wins.

## Common rules (apply to every prompt)

The system prompt is the same for every task:

```text
You are a static-analysis assistant for a multi-language code-impact
graph. Always respond with a single JSON object that matches the
schema in the user message. Output NO prose, NO markdown fences, NO
comments — just the JSON object. When unsure about any field, omit
the offending record entirely rather than guessing; an empty list is
always a valid response.
```

Send it as the `system` field on the request; it's tiny (~50 tokens) and
benefits from prompt caching on providers that support it.

The user message carries the actual task and the data. The exact JSON
schema is inlined in the user message itself so the contract survives
even if the system prompt is dropped.

---

## 1. `extract-facts` — fallback parser for unrecognised languages

Used when the walker encounters a file whose extension isn't claimed by
a native grammar (today: anything outside Python, Java, PL/SQL, C,
Pro*C, sh, Oracle Forms, plus YAML/TOML/properties via `ConfigGrammar`).

### When to use

Run this for files in any language the system doesn't have a native
grammar for: `.go`, `.rs`, `.rb`, `.kt`, `.swift`, `.ts`, `.cs`, `.cob`,
… The runtime auto-routes them when `ANTHROPIC_API_KEY` is set; you can
also call this manually to bootstrap fact data for a one-off review.

### The prompt

Substitute the four placeholders before sending:

- `{language}` — best-effort English name of the language (e.g. `Go`,
  `Ruby`, `Kotlin`).
- `{file_path}` — repo-relative path. Used by the model for context;
  echoed back unchanged.
- `{size_bytes}` — the original byte length of `content` (before any
  truncation). Helps the model know if it's seeing a prefix.
- `{numbered_content}` — the file body with each line prefixed by
  `N: `. Add line numbers yourself, 1-indexed, with leading-zero
  padding optional. Truncate to ~12 000 chars max.

```text
Task: extract structured facts from a source-code file written in {language}.

The file is {file_path} ({size_bytes} bytes shown). Treat line numbers as
1-indexed against the content below. Emit one record per observation; do
NOT emit records for trivia (comments, empty lines, formatting).

Allowed `kind` values and the `data` shape each one requires:

  - "symbol":  a function/method/class/variable DEFINITION.
        data: {
          "sym_kind": "function" | "method" | "class" | "variable" | "interface",
          "name": <string>,
          "enclosing_class": <string>,      # "" if top-level
          "enclosing_package": <string>     # "" if none
        }

  - "class_def": a class / struct / interface declaration.
        data: { "name": <string>, "kind": "class" | "struct" | "interface" | "trait" }

  - "import": an import / require / use statement.
        data: {
          "module": <string>,               # dotted/slashed path as written
          "names": [<string>, ...],         # specific symbols, or [] for whole-module
          "alias": <string>                 # "" when not aliased
        }

  - "call": a function or method INVOCATION (not definition).
        data: {
          "callee": <string>,               # fully-qualified when known, e.g. "pkg.func" or "obj.method"
          "receiver": <string>,             # the object/expression on the left of "."; "" if free function
          "method": <string>,               # bare method/function name
          "args": [<string>, ...],          # positional arguments as source text, truncated to 80 chars each
          "kwargs": { <name>: <value>, ... }
        }

  - "decorator" or "annotation":  Python decorator / Java/Kotlin/C# annotation
    attached to a symbol immediately following on a subsequent line.
        data: {
          "callee": <string>,               # e.g. "router.get", "Override"
          "args": [<string>, ...],
          "kwargs": { <name>: <value>, ... },
          "target_symbol": <string>         # name of the symbol being decorated
        }

  - "string_literal": a string worth keeping (URL, SQL, regex,
    config-looking key). Skip casual strings (error messages, prints).
        data: { "value": <string>, "context": "url" | "sql" | "path" | "other" }

Rules:
  1. Output exactly ONE JSON object: {"facts": [<record>, ...]}.
     No prose, no markdown fences, no trailing commas.
  2. Use the kinds listed above. Any other `kind` value is invalid and
     the record will be dropped.
  3. Each record must include {"kind", "line", "data"}. `line_end` is
     optional and should be the closing line for multi-line definitions.
  4. When the language uses case-insensitive identifiers (SQL, COBOL,
     Fortran), lowercase all names before emitting.
  5. If you cannot identify ANY facts confidently, return {"facts": []}.
     A small high-precision set is much better than a large noisy one.

Content (line-numbered, 1-indexed, possibly truncated):
---
{numbered_content}
---

Respond with the JSON object only.
```

### Expected response

A single JSON object:

```json
{
  "facts": [
    {
      "kind": "symbol",
      "line": 12,
      "line_end": 18,
      "data": {
        "sym_kind": "function",
        "name": "charge_customer",
        "enclosing_class": "",
        "enclosing_package": "billing"
      }
    },
    {
      "kind": "call",
      "line": 15,
      "data": {
        "callee": "stripe.Charges.create",
        "receiver": "stripe.Charges",
        "method": "create",
        "args": ["customer", "amount"],
        "kwargs": {}
      }
    }
  ]
}
```

The runtime parser at `core/llm/parser.py` validates each record. Records
with unknown `kind` are dropped silently; records missing `kind`/`line`
are dropped with a WARN. Whole-payload failures (bad JSON, non-object,
no `facts` key) return an empty list.

### Tips for manual use

- Number the lines yourself before pasting. The `cat -n` shell command
  produces an acceptable format (extra leading spaces are fine).
- Keep the file under ~12 KB. Anything bigger should be split into
  logical chunks (one chunk per package/module).
- Don't add explanatory text to the response — the parser is strict and
  rejects anything that isn't a single JSON object.

---

## 2. `learn-profile` — discover in-house framework conventions

A one-shot per repo. The runtime calls this with a handful of
representative files; the LLM returns an "overlay" that extends the
stock framework knowledge with patterns specific to the codebase
(custom test decorators, internal HTTP clients, etc.).

### When to use

When you bring `system-graph` to a codebase that wraps third-party
libraries behind its own facade (e.g. `acme.http.client` around
`httpx`, or `@acme_integration_test` instead of `@pytest.mark.integration`).
Without this overlay, the resolvers can't tell that calls to
`acme.http.client.get(...)` are external HTTP traffic.

### The prompt

Substitute the two placeholders:

- `{repo_id}` — short identifier for the repo (e.g. `billing-service`).
- `{samples_block}` — a sequence of file blocks delimited by `=== <path> ===`,
  optionally followed by `(<notes>)`. Pick 5–15 representative files:
  a couple of source modules, a test file, the dependency manifest,
  the build config.

```text
Task: examine these representative files from one repository and produce
a JSON overlay extending the stock framework knowledge. The goal is to
catch in-house conventions: custom test decorators, internal wrappers
around HTTP/queue clients, internal test bases, etc.

Output schema (single JSON object, no prose):
{
  "test_annotations":         [<string>, ...],   // extra decorators that mark tests, e.g. "@acme_test"
  "mock_annotations":         [<string>, ...],   // extra mock-style decorators, e.g. "@acme.mock_db"
  "external_modules":         [<string>, ...],   // internal wrappers around outside services, e.g. "acme.http.client"
  "internal_test_wrappers":   [<string>, ...],   // base classes / fixtures specific to this repo's tests
  "notes":                    <string>           // short freeform note about what you saw
}

Rules:
  1. Only include items NOT already obvious from stock frameworks (pytest,
     JUnit, FastAPI, Spring, etc.). Don't list `@pytest.fixture`.
  2. Empty arrays are fine. An empty overlay is a valid response.
  3. Output the JSON object only.

Repo: {repo_id}

Samples:
{samples_block}
```

### Example `samples_block`

```text
=== src/billing/charge.py (representative source) ===
from acme.http import client as acme_http

def charge(customer_id, amount):
    return acme_http.post("/charges", json={"customer": customer_id, "amount": amount})

=== tests/test_charge.py (test file) ===
import pytest
from acme.testing import acme_integration_test

@acme_integration_test
def test_charge_happy_path():
    ...

=== pyproject.toml (build config) ===
[tool.pytest.ini_options]
markers = ["acme_integration_test: acme-internal integration tests"]
```

### Expected response

```json
{
  "test_annotations": ["@acme_integration_test"],
  "mock_annotations": [],
  "external_modules": ["acme.http.client"],
  "internal_test_wrappers": ["acme.testing.acme_integration_test"],
  "notes": "Custom integration-test decorator from acme.testing; calls to acme.http.client are external HTTP."
}
```

---

## 3. `resolve-subgraph` — ask the LLM a focused question

The fallback path resolvers use when a deterministic answer isn't
available — e.g. "which of these candidate handlers is bound to
`/orders`?". The runtime calls this from `LLMEnhancer`; you can also use
it manually to debug ambiguous mappings.

### When to use

When a resolver has enumerated all the candidate facts but can't pick
between them on rules alone. Bundle the relevant facts and snippets, ask
a single yes/no or pick-one question, and parse the answer.

### The prompt

Substitute four placeholders:

- `{question}` — a single, focused question. One subject; one expected
  field shape in the answer.
- `{schema_block}` — pretty-printed JSON describing the answer shape.
- `{facts_block}` — pretty-printed JSON list of relevant `Fact` records
  (or `"(none)"`).
- `{snippets_block}` — `=== <path> ===` blocks with the supporting
  source (or `"(none)"`).

```text
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
```

### Example filled-in prompt

```text
Task: answer the question below using ONLY the facts and snippets
provided. Do not invent fields, candidate IDs, or files.

Question: Which of the candidate handlers is bound to the path `/orders` (GET)?

Expected answer schema (single JSON object, no prose):
{
  "target_id": "string | null",
  "confidence": "number between 0.0 and 1.0",
  "reason": "string (one sentence citing supporting fact IDs)"
}

Rules:
  1. If the answer is unclear, set `confidence: 0.0` and `target_id: null`
     (or whatever the schema's "unknown" sentinel is).
  2. Cite which fact IDs supported your answer in a `reason` field if
     the schema allows freeform text.
  3. Output the JSON object only.

Facts:
[
  {"id": "fact:abc", "kind": "decorator", "file": "src/orders.py", "line": 12,
   "data": {"callee": "router.get", "args": ["/orders"], "target_symbol": "list_orders"}},
  {"id": "fact:def", "kind": "decorator", "file": "src/orders.py", "line": 27,
   "data": {"callee": "router.post", "args": ["/orders"], "target_symbol": "create_order"}},
  {"id": "fact:ghi", "kind": "symbol", "file": "src/orders.py", "line": 13,
   "data": {"sym_kind": "function", "name": "list_orders"}}
]

Snippets:
=== src/orders.py ===
@router.get("/orders")
def list_orders():
    ...
@router.post("/orders")
def create_order(...):
    ...
```

### Expected response

```json
{
  "target_id": "fact:ghi",
  "confidence": 0.95,
  "reason": "fact:abc shows a router.get decorator with path '/orders' targeting list_orders (fact:ghi)."
}
```

---

## How the runtime uses these

| Prompt           | Code site                                       | Trigger                                       |
|------------------|-------------------------------------------------|-----------------------------------------------|
| `extract-facts`  | `core/llm/anthropic_client.py:extract_facts`    | walker hits a file with no native grammar     |
| `learn-profile`  | `core/llm/anthropic_client.py:learn_profile`    | one-shot per repo (optional, future)          |
| `resolve-subgraph` | `core/llm/anthropic_client.py:resolve_subgraph` | a resolver gives up; `LLMEnhancer` falls back |

The prompt **text** is the single source of truth at
`core/llm/prompts.py`; this doc is a render of that. If you tweak the
prompts in code, regenerate this doc — there's no automated check yet
because the formatting differs (the doc adds usage notes and examples).

## Provider configuration

| Variable              | Default                  | Purpose                                                        |
|-----------------------|--------------------------|----------------------------------------------------------------|
| `ANTHROPIC_API_KEY`   | (unset)                  | When set, `make_llm_client()` returns `AnthropicClient`.       |
| `ANTHROPIC_MODEL`     | `claude-haiku-4-5`       | Model override on the AnthropicClient constructor.             |
| `LLM_FALLBACK_SUFFIXES` | (stock list of ~25 exts) | CSV override. Example: `LLM_FALLBACK_SUFFIXES=go,rb` to scope LLM to just those. |

## Disabling the fallback

Unset `ANTHROPIC_API_KEY` (or remove it from `.env`). The factory falls
back to `NullClient`, which returns `[]` for every call — the walker
still routes `.go` files through the grammar, but the grammar produces
no facts.

To remove the routing entirely, set `LLM_FALLBACK_SUFFIXES=` (empty) and
no extensions will be claimed by the LLM grammar.
