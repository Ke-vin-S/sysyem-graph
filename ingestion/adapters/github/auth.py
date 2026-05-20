"""PAT-based authentication for the GitHub ingestor.

Three small pieces that together turn "set the right env var" into a
discoverable, debuggable flow:

* `TokenResolver` — given a clone URL, return the PAT to use. Reads env
  vars at construct time. github.com falls back to `GITHUB_TOKEN`; other
  hosts use `GITHUB_TOKEN_<HOST>` (uppercased, `.`/`-` → `_`).
* `AuthError` — typed exception the cloner raises when it can tell from
  stderr that the failure was authentication-related. Carries the host
  and a human-readable hint pointing at the right env var.
* `AuthVerifier` — REST sanity check against `/user`. Reports the
  authenticated login and OAuth scopes so users can confirm a token
  works without paying the cost of a full clone.

Tokens are NEVER stored on disk or logged in cleartext. Logging is
limited to "configured / not configured" per host.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


# ---- TokenResolver --------------------------------------------------------


class TokenResolver:
    """Resolves a clone URL to the right PAT.

    Lookup order for `https://<host>/owner/name`:

    1. `GITHUB_TOKEN_<safe(host)>` — e.g. `GITHUB_TOKEN_GHE_ACME_COM`.
    2. If `host == 'github.com'`: `GITHUB_TOKEN`.
    3. Empty string (anonymous clone — fine for public repos).

    Pass a custom `env` mapping for tests; defaults to `os.environ`.
    """

    def __init__(self, env: dict[str, str] | None = None) -> None:
        self._env = dict(env) if env is not None else dict(os.environ)

    def resolve(self, url: str) -> str:
        host = host_of(url)
        if not host:
            return ""
        per_host = self._env.get(f"GITHUB_TOKEN_{_safe_host(host)}", "")
        if per_host:
            logger.debug("github auth: per-host token configured for %s", host)
            return per_host
        if host == "github.com":
            default = self._env.get("GITHUB_TOKEN", "")
            if default:
                logger.debug("github auth: default GITHUB_TOKEN used for github.com")
            return default
        logger.debug("github auth: no token configured for %s", host)
        return ""

    def is_configured(self, host: str) -> bool:
        """Whether ANY token env var is set for `host` (without revealing it)."""
        if f"GITHUB_TOKEN_{_safe_host(host)}" in self._env:
            return bool(self._env[f"GITHUB_TOKEN_{_safe_host(host)}"])
        if host == "github.com":
            return bool(self._env.get("GITHUB_TOKEN"))
        return False

    def env_var_for(self, host: str) -> str:
        """The env-var name a user would set to authenticate against `host`.
        Used in the doctor message and `auth show`."""
        if host == "github.com":
            return "GITHUB_TOKEN"
        return f"GITHUB_TOKEN_{_safe_host(host)}"


def host_of(url: str) -> str:
    """Hostname for a clone URL, or empty string if unparseable."""
    parsed = urlparse(url)
    return parsed.hostname or ""


def _safe_host(host: str) -> str:
    """Convert a hostname into an env-var-safe suffix.
    `ghe.acme.com` → `GHE_ACME_COM`; `my-org.example` → `MY_ORG_EXAMPLE`."""
    return host.upper().replace(".", "_").replace("-", "_")


# ---- AuthError ------------------------------------------------------------


@dataclass(frozen=True)
class AuthError(Exception):
    """Raised when a git operation fails for authentication reasons.

    Carries enough context for the CLI to print a useful doctor message:
    the host the user is trying to reach, whether a token was already
    configured, and a human-readable hint."""

    host: str
    hint: str
    token_configured: bool = False

    def __str__(self) -> str:
        return self.hint


# ---- AuthVerifier ---------------------------------------------------------


@dataclass(frozen=True)
class AuthCheckResult:
    host: str
    api_url: str
    login: str
    scopes: tuple[str, ...]


class AuthVerifier:
    """Validates a PAT by calling the host's `/user` endpoint.

    Uses `httpx` (already a runtime dependency). For github.com the API
    lives at `https://api.github.com`; for a GitHub Enterprise host
    (`ghe.acme.com`) it's `https://ghe.acme.com/api/v3`.
    """

    def __init__(
        self,
        *,
        timeout: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._timeout = timeout
        self._client = client  # injection point for tests

    def api_url_for(self, host: str) -> str:
        if host in ("", "github.com"):
            return "https://api.github.com"
        return f"https://{host}/api/v3"

    def check(self, host: str, token: str) -> AuthCheckResult:
        if not token:
            raise AuthError(
                host=host,
                hint=(
                    f"no token configured for {host}. "
                    f"set {TokenResolver().env_var_for(host)} and retry."
                ),
                token_configured=False,
            )
        api = self.api_url_for(host)
        url = f"{api}/user"
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        client = self._client or httpx.Client(timeout=self._timeout)
        owns_client = self._client is None
        try:
            resp = client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise AuthError(
                host=host,
                hint=f"could not reach {api}: {exc}",
                token_configured=True,
            ) from exc
        finally:
            if owns_client:
                client.close()

        if resp.status_code in (401, 403):
            message = _extract_message(resp)
            raise AuthError(
                host=host,
                hint=(
                    f"token for {host} rejected (HTTP {resp.status_code} — {message!r}). "
                    "Check the token's expiry and scopes (needs `repo`)."
                ),
                token_configured=True,
            )
        if resp.status_code >= 400:
            raise AuthError(
                host=host,
                hint=f"{api}/user returned HTTP {resp.status_code}: {_extract_message(resp)}",
                token_configured=True,
            )

        body = resp.json() if resp.content else {}
        scopes_header = resp.headers.get("X-OAuth-Scopes", "")
        scopes = tuple(s.strip() for s in scopes_header.split(",") if s.strip())
        return AuthCheckResult(
            host=host or "github.com",
            api_url=api,
            login=str(body.get("login", "")),
            scopes=scopes,
        )


def _extract_message(resp: httpx.Response) -> str:
    """GitHub error bodies are `{"message": "...", "documentation_url": "..."}`."""
    try:
        return str(resp.json().get("message", "")) or resp.reason_phrase
    except Exception:
        return resp.reason_phrase


# ---- stderr classification (used by the cloner) ---------------------------


_AUTH_FAILED_PHRASES = (
    "Authentication failed",
    "could not read Username",
    "Invalid username or token",
    "Invalid username or password",
    "fatal: HTTP request failed",
)
_NOT_FOUND_PHRASES = (
    "Repository not found",
    "repository not found",
    "remote: Not Found",
)


def classify_git_error(stderr: str, *, host: str, token_configured: bool) -> AuthError | None:
    """If `stderr` from a failed `git clone/fetch` looks like an auth issue,
    return the typed `AuthError`. Return `None` to let the caller propagate
    the original git error untouched.

    Discriminator: "did we send a token?". A "not found" error WITHOUT a
    token configured is treated as auth (the repo might be private — let
    the user try a token); a "not found" error WITH a token configured is
    treated as a real missing repo and not caught here.
    """
    stderr = stderr or ""
    auth_failed = any(p in stderr for p in _AUTH_FAILED_PHRASES)
    not_found = any(p in stderr for p in _NOT_FOUND_PHRASES)
    env_var = TokenResolver().env_var_for(host)

    if auth_failed:
        if token_configured:
            return AuthError(
                host=host,
                hint=(
                    f"token for {host} rejected by git. Check the token's expiry "
                    f"and scopes (needs `repo`). Verify with "
                    f"`sg-ingest github auth check --host {host}`."
                ),
                token_configured=True,
            )
        return AuthError(
            host=host,
            hint=(
                f"{host} needs a token. Set {env_var} (PAT with `repo` scope) "
                "and retry. Verify with `sg-ingest github auth check`."
            ),
            token_configured=False,
        )

    if not_found and not token_configured:
        return AuthError(
            host=host,
            hint=(
                f"{host} reports the repo as missing — it may be private. "
                f"Set {env_var} (PAT with `repo` scope) and retry. "
                "If the URL is wrong this error will persist after authenticating."
            ),
            token_configured=False,
        )

    return None
