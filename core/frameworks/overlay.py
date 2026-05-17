"""RepoOverlay — additive extension to FrameworkDefinitions for one repo.

The overlay never *replaces* a stock value, only *adds* to it. Used by the
LLM profile-learner to teach the system about in-house wrappers ("we wrap
httpx as acme.http.client") without modifying the shipped framework files.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class RepoOverlay(BaseModel):
    """Repo-specific additions to the framework knowledge base.

    Persisted as `.system-graph/profiles/<repo_id>.yaml`. The loader combines
    overlay + stock framework definitions into an EffectiveFramework per
    repo at resolver-construction time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    repo_id: str
    test_annotations: tuple[str, ...] = ()
    mock_annotations: tuple[str, ...] = ()
    external_modules: tuple[str, ...] = ()
    internal_test_wrappers: tuple[str, ...] = ()
    """Modules that LOOK external (e.g. `acme.db`) but are in-house wrappers
    and should NOT trigger integration classification. These get SUBTRACTED
    from external_modules during composition."""

    notes: str = ""
    generated_at: datetime | None = None
    model: str = ""
    overlay_version: str = "1"
