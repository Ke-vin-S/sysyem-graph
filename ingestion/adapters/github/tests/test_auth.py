"""Tests for TokenResolver, AuthVerifier, and classify_git_error.

No network access — `AuthVerifier` is tested with `respx` mocking
`httpx`. Git-error classification is pure string parsing.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from ingestion.adapters.github.auth import (
    AuthError,
    AuthVerifier,
    TokenResolver,
    classify_git_error,
)

# ---- TokenResolver --------------------------------------------------------


def test_resolver_picks_default_for_github_com() -> None:
    r = TokenResolver(env={"GITHUB_TOKEN": "ghp_default"})
    assert r.resolve("https://github.com/acme/thing") == "ghp_default"


def test_resolver_picks_per_host_for_enterprise() -> None:
    r = TokenResolver(env={"GITHUB_TOKEN_GHE_ACME_COM": "ghp_ghe"})
    assert r.resolve("https://ghe.acme.com/team/svc") == "ghp_ghe"


def test_resolver_per_host_overrides_default() -> None:
    r = TokenResolver(env={"GITHUB_TOKEN": "ghp_x", "GITHUB_TOKEN_GHE_ACME_COM": "ghp_y"})
    # github.com still uses the default
    assert r.resolve("https://github.com/a/b") == "ghp_x"
    # ghe uses the per-host
    assert r.resolve("https://ghe.acme.com/a/b") == "ghp_y"


def test_resolver_returns_empty_for_unknown_host() -> None:
    r = TokenResolver(env={"GITHUB_TOKEN": "ghp_x"})
    assert r.resolve("https://random.example.com/a/b") == ""


def test_resolver_handles_hyphens_in_host() -> None:
    r = TokenResolver(env={"GITHUB_TOKEN_MY_ORG_INTERNAL": "ghp_h"})
    assert r.resolve("https://my-org.internal/a/b") == "ghp_h"


def test_resolver_unparseable_url_returns_empty() -> None:
    r = TokenResolver(env={"GITHUB_TOKEN": "ghp_x"})
    assert r.resolve("not a url at all") == ""


def test_is_configured() -> None:
    r = TokenResolver(env={"GITHUB_TOKEN": "ghp_x"})
    assert r.is_configured("github.com") is True
    assert r.is_configured("ghe.acme.com") is False


def test_env_var_for_naming() -> None:
    r = TokenResolver(env={})
    assert r.env_var_for("github.com") == "GITHUB_TOKEN"
    assert r.env_var_for("ghe.acme.com") == "GITHUB_TOKEN_GHE_ACME_COM"
    assert r.env_var_for("my-org.internal") == "GITHUB_TOKEN_MY_ORG_INTERNAL"


# ---- classify_git_error ---------------------------------------------------


def test_classify_no_token_repo_not_found_is_auth() -> None:
    err = classify_git_error(
        "remote: Repository not found.\nfatal: repository '...' not found",
        host="github.com",
        token_configured=False,
    )
    assert err is not None
    assert "GITHUB_TOKEN" in err.hint
    assert err.token_configured is False


def test_classify_with_token_repo_not_found_is_not_auth() -> None:
    # If we sent a token and the host says "not found", that's a real miss.
    err = classify_git_error(
        "remote: Repository not found.\nfatal: repository '...' not found",
        host="github.com",
        token_configured=True,
    )
    assert err is None


def test_classify_authentication_failed_is_auth_token_rejected() -> None:
    err = classify_git_error(
        "fatal: Authentication failed for 'https://github.com/o/n'",
        host="github.com",
        token_configured=True,
    )
    assert err is not None
    assert err.token_configured is True
    assert "rejected" in err.hint


def test_classify_invalid_username_or_token() -> None:
    err = classify_git_error(
        "remote: Invalid username or token. Password authentication is not supported.",
        host="ghe.acme.com",
        token_configured=True,
    )
    assert err is not None
    assert "ghe.acme.com" in err.hint


def test_classify_could_not_read_username() -> None:
    err = classify_git_error(
        "fatal: could not read Username for 'https://github.com'",
        host="github.com",
        token_configured=False,
    )
    assert err is not None
    assert "GITHUB_TOKEN" in err.hint


def test_classify_unrelated_error_returns_none() -> None:
    # A genuine network failure shouldn't be misclassified as auth.
    assert classify_git_error(
        "fatal: unable to access 'https://github.com/': Could not resolve host",
        host="github.com",
        token_configured=False,
    ) is None


# ---- AuthVerifier ---------------------------------------------------------


@respx.mock
def test_auth_verifier_happy_path() -> None:
    route = respx.get("https://api.github.com/user").mock(
        return_value=httpx.Response(
            200,
            json={"login": "kevin-s"},
            headers={"X-OAuth-Scopes": "repo, read:org"},
        )
    )
    verifier = AuthVerifier()
    result = verifier.check("github.com", "ghp_xxx")
    assert route.called
    assert result.login == "kevin-s"
    assert result.scopes == ("repo", "read:org")
    assert result.host == "github.com"
    assert result.api_url == "https://api.github.com"
    # Header was set correctly.
    call = route.calls.last
    assert call.request.headers["Authorization"] == "token ghp_xxx"


@respx.mock
def test_auth_verifier_401_raises_with_doctor_message() -> None:
    respx.get("https://api.github.com/user").mock(
        return_value=httpx.Response(401, json={"message": "Bad credentials"})
    )
    verifier = AuthVerifier()
    with pytest.raises(AuthError) as exc_info:
        verifier.check("github.com", "ghp_bad")
    assert "rejected" in exc_info.value.hint
    assert "Bad credentials" in exc_info.value.hint
    assert exc_info.value.token_configured is True


@respx.mock
def test_auth_verifier_ghe_uses_v3_api_url() -> None:
    route = respx.get("https://ghe.acme.com/api/v3/user").mock(
        return_value=httpx.Response(
            200, json={"login": "bot"}, headers={"X-OAuth-Scopes": "repo"}
        )
    )
    result = AuthVerifier().check("ghe.acme.com", "ghp_y")
    assert route.called
    assert result.api_url == "https://ghe.acme.com/api/v3"
    assert result.login == "bot"
    assert result.scopes == ("repo",)


def test_auth_verifier_no_token_raises_friendly() -> None:
    with pytest.raises(AuthError) as exc_info:
        AuthVerifier().check("github.com", "")
    assert "GITHUB_TOKEN" in exc_info.value.hint
    assert exc_info.value.token_configured is False


@respx.mock
def test_auth_verifier_transport_error_wraps_to_auth_error() -> None:
    respx.get("https://api.github.com/user").mock(
        side_effect=httpx.ConnectError("name resolution failed")
    )
    with pytest.raises(AuthError) as exc_info:
        AuthVerifier().check("github.com", "ghp_x")
    assert "could not reach" in exc_info.value.hint
