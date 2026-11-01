"""Tiny in-process counters and latency recorder.

The /api/metrics endpoint and the UI dashboard read from here. In a real deployment
you would export these to Prometheus; for learning, a dict is easier to read.
"""
import threading
from collections import defaultdict, deque


class Metrics:
    def __init__(self) -> None:
        self.counters: dict[str, int] = defaultdict(int)
        # keep only the last 1000 timings per group so memory never grows
        self.latency: dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))
        self._lock = threading.Lock()  # many requests run at once, so protect the dicts

    def count(self, name: str, n: int = 1) -> None:
        with self._lock:
            self.counters[name] += n

    def record_latency(self, group: str, ms: float) -> None:
        with self._lock:
            self.latency[group].append(ms)

    def percentile(self, group: str, p: float) -> float:
        """p95 means: 95 percent of requests were faster than this number."""
        with self._lock:
            values = sorted(self.latency[group])
        if not values:
            return 0.0
        return round(values[min(len(values) - 1, int(len(values) * p / 100))], 1)

    def snapshot(self) -> dict:
        groups = list(self.latency.keys())
        return {
            "counters": dict(self.counters),
            "latency_ms": {g: {"count": len(self.latency[g]), "p50": self.percentile(g, 50), "p95": self.percentile(g, 95)} for g in groups},
        }


METRICS = Metrics()
