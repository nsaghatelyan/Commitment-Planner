"""File cache for API responses that cost money (Cost Explorer) or are heavily throttled."""

import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.collectors.types import CallStats


class ApiCache:
    def __init__(self, directory: str | Path | None, ttl_seconds: int) -> None:
        self.dir = Path(directory) if directory else None
        self.ttl = ttl_seconds
        if self.dir:
            self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, namespace: str, key: Any) -> Path | None:
        if not self.dir:
            return None
        digest = hashlib.sha256(
            json.dumps([namespace, key], sort_keys=True, default=str).encode()
        ).hexdigest()
        return self.dir / f"{digest}.json"

    def get(self, namespace: str, key: Any) -> Any | None:
        path = self._path(namespace, key)
        if not path or not path.exists() or time.time() - path.stat().st_mtime > self.ttl:
            return None
        return json.loads(path.read_text())

    def put(self, namespace: str, key: Any, value: Any) -> None:
        path = self._path(namespace, key)
        if path:
            path.write_text(json.dumps(value, default=str))


class CountingCaller:
    """Wraps API calls: serves from cache when possible, otherwise calls and counts."""

    def __init__(self, stats: CallStats, cache: ApiCache | None = None) -> None:
        self.stats = stats
        self.cache = cache

    def __call__(
        self, api: str, fn: Callable[..., Any], cache_key: Any = None, **kwargs: Any
    ) -> Any:
        """cache_key: None disables caching for this call; otherwise namespaced by (api, cache_key)."""
        if self.cache is not None and cache_key is not None:
            hit = self.cache.get(api, cache_key)
            if hit is not None:
                self.stats.cache_hits += 1
                return hit
        result = fn(**kwargs)
        self.stats.record(api)
        if self.cache is not None and cache_key is not None:
            # boto responses carry datetimes; round-trip through JSON so hits and misses match.
            result = json.loads(json.dumps(result, default=str))
            self.cache.put(api, cache_key, result)
        return result
