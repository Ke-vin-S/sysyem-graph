"""Content-hash-keyed cache for LLM responses.

The same source file always produces the same answer, so we cache by
SHA-256 of (prompt_version, content) and serialize the response to JSON on
disk. Resolvers/grammars hit the cache before the LLM; cache misses go to
the provider, hits return instantly.

The cache is content-addressed, so there's no manual invalidation — the
content changes, the key changes, the old entry is just dead bytes on disk.
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def cache_key(*, prompt_version: str, content: str, extra: str = "") -> str:
    payload = f"{prompt_version}\0{extra}\0{content}".encode()
    return hashlib.sha256(payload).hexdigest()


class LLMCache(ABC):
    @abstractmethod
    def get(self, key: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def put(self, key: str, value: dict[str, Any]) -> None: ...


class NullCache(LLMCache):
    """No-op cache for tests."""

    def get(self, key: str) -> dict[str, Any] | None:
        return None

    def put(self, key: str, value: dict[str, Any]) -> None:
        return None


@dataclass
class FileCache(LLMCache):
    """JSON-on-disk cache under `root`. One file per key.

    Keys are SHA-256 hex strings (no path separator concerns). On read errors
    (corruption, partial write) we treat as a miss and overwrite on next put.
    """

    root: Path

    def _path(self, key: str) -> Path:
        # Spread across 256 subdirs to keep any single directory small.
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def put(self, key: str, value: dict[str, Any]) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True, default=str), encoding="utf-8")
