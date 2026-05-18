"""QueryResolver tests."""

from __future__ import annotations

from core.facts import Fact, FactKind, FactTree
from core.frameworks import EffectiveFramework
from core.frameworks.definition import QueryPatterns
from core.resolvers import QueryResolver
from core.types import CodeArtifact, LineRange, QueryKind


def _fn(name: str, *, file: str, start: int, end: int) -> CodeArtifact:
    return CodeArtifact(
        id=f"fn:r:{file}:{name}", repoId="r", type="function", name=name,
        file=file, lineRange=LineRange(start=start, end=end), isPublic=True,
    )


def _call(file: str, line: int, *, callee: str, args: list) -> Fact:
    receiver, _, method = callee.rpartition(".")
    return Fact(
        kind=FactKind.CALL, file=file, line=line, repo_id="r",
        data={"callee": callee, "receiver": receiver, "method": method, "args": args, "kwargs": {}},
    )


def _fw(qp: QueryPatterns) -> EffectiveFramework:
    return EffectiveFramework(
        name="sqlalchemy", language="python", routes=None, tests=None, mocks=None,
        http_clients=None, data_models=None, queries=qp, kafka=None,
    )


def test_raw_sql_method_match() -> None:
    fw = _fw(QueryPatterns(kind="raw_sql", call_methods=("execute",), expression_arg=0))
    caller = _fn("run", file="src/x.py", start=1, end=20)
    tree = FactTree.from_facts(
        "r",
        [_call("src/x.py", 10, callee="session.execute", args=["SELECT * FROM users WHERE id = 1"])],
    )
    out = QueryResolver().resolve(
        tree=tree, artifacts=[caller], frameworks=(fw,), repo_id="r",
    )
    assert len(out.queries) == 1
    q = out.queries[0]
    assert q.kind is QueryKind.RAW_SQL
    assert q.expression.startswith("SELECT")
    assert q.tables == ("users",)
    assert q.enclosing_artifact_id == caller.id


def test_call_callee_match() -> None:
    fw = _fw(QueryPatterns(kind="raw_sql", call_callees=("text",), expression_arg=0))
    caller = _fn("run", file="src/x.py", start=1, end=20)
    tree = FactTree.from_facts(
        "r",
        [_call("src/x.py", 5, callee="text", args=["UPDATE accounts SET balance = balance - 1"])],
    )
    out = QueryResolver().resolve(
        tree=tree, artifacts=[caller], frameworks=(fw,), repo_id="r",
    )
    assert len(out.queries) == 1
    assert out.queries[0].tables == ("accounts",)


def test_non_literal_arg_is_skipped() -> None:
    fw = _fw(QueryPatterns(kind="raw_sql", call_methods=("execute",), expression_arg=0))
    caller = _fn("run", file="src/x.py", start=1, end=20)
    tree = FactTree.from_facts(
        "r",
        # `<name:sql_var>` is a bound variable, not a literal
        [_call("src/x.py", 10, callee="session.execute", args=["<name:sql_var>"])],
    )
    out = QueryResolver().resolve(
        tree=tree, artifacts=[caller], frameworks=(fw,), repo_id="r",
    )
    assert out.queries == []


def test_multi_table_extraction() -> None:
    fw = _fw(QueryPatterns(kind="raw_sql", call_methods=("execute",), expression_arg=0))
    caller = _fn("run", file="src/x.py", start=1, end=20)
    tree = FactTree.from_facts(
        "r",
        [_call("src/x.py", 10, callee="session.execute",
               args=["SELECT u.* FROM users u JOIN orders o ON o.user_id = u.id"])],
    )
    out = QueryResolver().resolve(
        tree=tree, artifacts=[caller], frameworks=(fw,), repo_id="r",
    )
    assert set(out.queries[0].tables) == {"users", "orders"}


def test_java_query_annotation_match() -> None:
    """`@Query("SELECT u FROM users u")` on a method should produce a Query record."""
    fw = _fw(QueryPatterns(kind="jpql", annotation_callees=("Query",), expression_arg=0))
    caller = _fn("findAll", file="src/UserRepo.java", start=1, end=10)
    ann = Fact(
        kind=FactKind.ANNOTATION, file="src/UserRepo.java", line=5, repo_id="r",
        data={"callee": "Query", "args": ["SELECT u FROM users u"], "kwargs": {},
              "target_symbol": "findAll", "target_kind": "method", "qualified": "Query"},
    )
    tree = FactTree.from_facts("r", [ann])
    out = QueryResolver().resolve(
        tree=tree, artifacts=[caller], frameworks=(fw,), repo_id="r",
    )
    assert len(out.queries) == 1
    assert out.queries[0].kind.value == "jpql"
    assert out.queries[0].enclosing_artifact_id == caller.id


def test_no_enclosing_artifact_still_emits_query() -> None:
    """Queries at module scope don't get EXECUTES edges but should still appear."""
    fw = _fw(QueryPatterns(kind="raw_sql", call_methods=("execute",), expression_arg=0))
    tree = FactTree.from_facts(
        "r", [_call("src/x.py", 5, callee="session.execute", args=["SELECT 1"])]
    )
    out = QueryResolver().resolve(tree=tree, artifacts=[], frameworks=(fw,), repo_id="r")
    assert len(out.queries) == 1
    assert out.queries[0].enclosing_artifact_id is None
