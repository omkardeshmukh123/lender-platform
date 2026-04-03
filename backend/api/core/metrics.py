"""
backend/api/core/metrics.py
============================
Thread-safe in-process metrics. Exposed on /health endpoint.
Singleton: from core.metrics import metrics
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Optional


class InMemoryMetrics:
    def __init__(self) -> None:
        self._lock     = threading.Lock()
        self._counters: dict[str, int]   = defaultdict(int)
        self._gauges:   dict[str, float] = {}

    def inc(self, name: str, amount: int = 1, tags: Optional[dict] = None) -> None:
        key = self._key(name, tags)
        with self._lock:
            self._counters[key] += amount

    def gauge(self, name: str, value: float, tags: Optional[dict] = None) -> None:
        key = self._key(name, tags)
        with self._lock:
            self._gauges[key] = value

    def get(self, name: str, tags: Optional[dict] = None) -> int:
        with self._lock:
            return self._counters.get(self._key(name, tags), 0)

    def snapshot(self) -> dict:
        with self._lock:
            return {"counters": dict(self._counters), "gauges": dict(self._gauges)}

    def health_summary(self) -> dict:
        with self._lock:
            c = self._counters
            hits   = c.get("cache.hit", 0)
            misses = c.get("cache.miss", 0)
            total  = hits + misses
            return {
                "requests_total":       c.get("requests.total", 0),
                "requests_5xx":         c.get("requests.5xx", 0),
                "requests_4xx":         c.get("requests.4xx", 0),
                "unhandled_exceptions": c.get("requests.unhandled_exception", 0),
                "cache_hits":           hits,
                "cache_misses":         misses,
                "cache_hit_rate":       round(hits / total, 3) if total else 0.0,
                "cache_invalidations":  c.get("cache.invalidation", 0),
                "db_errors":            c.get("db.error_count", 0),
                "loans_matched":        c.get("loans.match_served", 0),
            }

    @staticmethod
    def _key(name: str, tags: Optional[dict]) -> str:
        if not tags:
            return name
        return f"{name}[{','.join(f'{k}={v}' for k, v in sorted(tags.items()))}]"


metrics = InMemoryMetrics()
