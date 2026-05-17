"""Tests for NullClient and budgets/cache primitives."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.llm import (
    BudgetExceeded,
    FileCache,
    LLMBudget,
    NullCache,
    NullClient,
    ProfileSample,
    SubgraphQuestion,
)
from core.llm.cache import cache_key


def test_null_client_extract_returns_empty() -> None:
    assert NullClient().extract_facts(file="x.py", content="", repo_id="r") == []


def test_null_client_profile_is_no_op() -> None:
    overlay = NullClient().learn_profile(repo_id="r", samples=[ProfileSample(path="x", content="y")])
    assert overlay.repo_id == "r"
    assert overlay.test_annotations == ()
    assert overlay.external_modules == ()
    assert overlay.model == "null"


def test_null_client_subgraph_resolution_low_confidence() -> None:
    res = NullClient().resolve_subgraph(SubgraphQuestion(question="?"))
    assert res.confidence == 0.0
    assert res.answer == {}


def test_budget_check_caps_files() -> None:
    budget = LLMBudget(max_files_per_run=1)
    budget.check()
    budget.record(tokens_in=1, tokens_out=1)
    with pytest.raises(BudgetExceeded) as exc:
        budget.check()
    assert exc.value.kind == "files"


def test_budget_check_caps_tokens() -> None:
    budget = LLMBudget(max_tokens_per_run=100)
    with pytest.raises(BudgetExceeded):
        budget.check(est_tokens_in=200)


def test_budget_check_caps_dollars() -> None:
    budget = LLMBudget(max_dollars_per_run=0.5)
    budget.record(cost_dollars=0.4)
    with pytest.raises(BudgetExceeded):
        budget.check(est_cost_dollars=0.2)


def test_cache_key_stable_for_same_inputs() -> None:
    a = cache_key(prompt_version="v1", content="hello", extra="x")
    b = cache_key(prompt_version="v1", content="hello", extra="x")
    c = cache_key(prompt_version="v1", content="hello", extra="y")
    assert a == b
    assert a != c


def test_null_cache_is_noop() -> None:
    cache = NullCache()
    cache.put("k", {"a": 1})
    assert cache.get("k") is None


def test_file_cache_roundtrip(tmp_path: Path) -> None:
    cache = FileCache(root=tmp_path)
    key = cache_key(prompt_version="v1", content="data")
    assert cache.get(key) is None
    cache.put(key, {"facts": [{"kind": "symbol", "name": "f"}]})
    got = cache.get(key)
    assert got is not None
    assert got["facts"][0]["name"] == "f"


def test_file_cache_tolerates_corruption(tmp_path: Path) -> None:
    cache = FileCache(root=tmp_path)
    key = cache_key(prompt_version="v1", content="data")
    path = cache._path(key)  # noqa: SLF001
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json")
    assert cache.get(key) is None
