"""Load `core/languages/<lang>/profile.yaml` files into a LanguageLibrary."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from core.languages.profile import LanguageProfile
from core.types.errors import ConfigurationError

logger = logging.getLogger(__name__)

DEFAULT_LANGUAGES_DIR = Path("core/languages")


@dataclass
class LanguageLibrary:
    """Loaded set of language profiles keyed by language name."""

    profiles: dict[str, LanguageProfile] = field(default_factory=dict)

    def names(self) -> list[str]:
        return sorted(self.profiles)

    def get(self, name: str) -> LanguageProfile:
        if name not in self.profiles:
            raise KeyError(name)
        return self.profiles[name]

    def all(self) -> list[LanguageProfile]:
        return [self.profiles[n] for n in self.names()]

    def for_file(self, file: str) -> LanguageProfile | None:
        """Return the profile claiming this file by extension, or None.

        First-match-wins by sorted name; multiple profiles claiming the same
        extension would be a config error caught at load time.
        """
        for profile in self.all():
            if profile.claims(file):
                return profile
        return None

    def for_extension(self, ext: str) -> LanguageProfile | None:
        for profile in self.all():
            if ext in profile.file_extensions:
                return profile
        return None


def load_library(root: Path | None = None) -> LanguageLibrary:
    """Read every `<root>/<lang>/profile.yaml`, validate as LanguageProfile,
    return a LanguageLibrary.

    Layout: one directory per language. Each language directory contains a
    `profile.yaml` declaring the LanguageProfile, plus any language-specific
    Python code (grammar.py, extractors/). Other YAMLs under the language
    dir are ignored at the library level — they're consumed by extractors.

    Two failure modes:
      * YAML syntax / schema errors → ConfigurationError (stop the world).
      * Two profiles claim the same extension → ConfigurationError.
    """
    root = root or DEFAULT_LANGUAGES_DIR
    if not root.exists():
        raise ConfigurationError(f"languages directory not found: {root.resolve()}")

    library = LanguageLibrary()
    claimed_extensions: dict[str, str] = {}
    for lang_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        profile_path = lang_dir / "profile.yaml"
        if not profile_path.exists():
            continue
        try:
            data = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ConfigurationError(f"{profile_path}: invalid YAML: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigurationError(f"{profile_path}: language YAML must be a mapping")
        try:
            profile = LanguageProfile.model_validate(data)
        except Exception as exc:
            raise ConfigurationError(
                f"{profile_path}: language validation failed: {exc}"
            ) from exc
        if profile.name in library.profiles:
            raise ConfigurationError(
                f"{profile_path}: duplicate language name {profile.name!r}"
            )
        for ext in profile.file_extensions:
            if ext in claimed_extensions:
                raise ConfigurationError(
                    f"{profile_path}: extension {ext!r} also claimed by "
                    f"{claimed_extensions[ext]!r}"
                )
            claimed_extensions[ext] = profile.name
        library.profiles[profile.name] = profile
        logger.debug("loaded language %s from %s", profile.name, profile_path)
    return library
