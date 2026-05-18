"""Load `frameworks/<lang>/*.yaml` from disk into a FrameworkLibrary."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from core.frameworks.definition import FrameworkDefinition
from core.types.errors import ConfigurationError

logger = logging.getLogger(__name__)

DEFAULT_FRAMEWORKS_DIR = Path("frameworks")


@dataclass
class FrameworkLibrary:
    """In-memory collection of FrameworkDefinitions keyed by name."""

    definitions: dict[str, FrameworkDefinition] = field(default_factory=dict)

    def names(self) -> list[str]:
        return sorted(self.definitions)

    def get(self, name: str) -> FrameworkDefinition:
        if name not in self.definitions:
            raise KeyError(name)
        return self.definitions[name]

    def for_language(self, language: str) -> list[FrameworkDefinition]:
        return [fw for fw in self.definitions.values() if fw.language == language]

    def all(self) -> list[FrameworkDefinition]:
        return [self.definitions[n] for n in self.names()]


def load_library(root: Path | None = None) -> FrameworkLibrary:
    """Read every `<root>/<lang>/*.yaml`, validate as FrameworkDefinition,
    return a FrameworkLibrary.

    Layout: one directory per language, framework YAMLs grouped under it.
    Top-level `<root>/*.yaml` files are also accepted for back-compat /
    cross-language framework definitions.

    Files that fail validation raise ConfigurationError — we'd rather fail
    loudly at startup than silently drop framework knowledge.
    """
    root = root or DEFAULT_FRAMEWORKS_DIR
    if not root.exists():
        raise ConfigurationError(f"frameworks directory not found: {root.resolve()}")

    library = FrameworkLibrary()
    for path in sorted(root.rglob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ConfigurationError(f"{path}: invalid YAML: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigurationError(f"{path}: framework YAML must be a mapping")
        try:
            definition = FrameworkDefinition.model_validate(data)
        except Exception as exc:
            raise ConfigurationError(f"{path}: framework validation failed: {exc}") from exc
        if definition.name in library.definitions:
            raise ConfigurationError(
                f"{path}: duplicate framework name {definition.name!r}; "
                f"already loaded from another file"
            )
        library.definitions[definition.name] = definition
        logger.debug("loaded framework %s from %s", definition.name, path.name)
    return library
