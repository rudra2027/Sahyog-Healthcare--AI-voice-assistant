"""Memory: the five layers from the design, in one readable file.

 Layer              What it is in code                     Lives in
 ------------------ -------------------------------------- ------------------------
 1 Thread memory    the CHECKPOINTER (one per thread_id)   RAM, or Redis if REDIS_URL
 2 Patient memory   the STORE, namespace ("patient", id)   RAM, or Redis
 3 Agent lessons    the STORE, namespace ("agent", ...)    RAM, or Redis
 4 Cache            TTLCache: keys that expire             RAM, or Redis
 5 Records          SQL tables (see database.py)           SQLite

"Short-term memory" and "thread memory" are the same thing: what the agent remembers
INSIDE one conversation. "Long-term memory" is what survives across conversations.
"""
import json
import logging
import time
from contextlib import ExitStack
from functools import lru_cache

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from app.config import settings
from app.metrics import METRICS

log = logging.getLogger(__name__)
_connections = ExitStack()  # keeps Redis connections open while the app runs


@lru_cache
def _redis_backends():
    """Try to use Redis for checkpoints and the store. Falls back to RAM if anything is missing.

    NOTE: this Redis path is optional and needs `pip install langgraph-checkpoint-redis`
    plus a Redis server with the Search and JSON modules (Redis Stack).
    """
    if not settings.redis_url:
        return None, None
    try:
        from langgraph.checkpoint.redis import RedisSaver
        from langgraph.store.redis import RedisStore

        saver = _connections.enter_context(RedisSaver.from_conn_string(settings.redis_url))
        saver.setup()
        store = _connections.enter_context(RedisStore.from_conn_string(settings.redis_url))
        store.setup()
        log.info("Using Redis for checkpoints and the long-term store")
        return saver, store
    except Exception as err:
        log.warning("Redis not available (%s); using in-memory memory instead", err)
        return None, None


@lru_cache
def get_checkpointer():
    """Layer 1: saves the graph's state after every step so a paused run can resume."""
    saver, _ = _redis_backends()
    return saver or InMemorySaver()


@lru_cache
def get_store():
    """Layers 2 and 3: a key-value store shared by ALL conversations."""
    _, store = _redis_backends()
    return store or InMemoryStore()


# ----------------------------------------------------------------------------
# Layer 4: a cache whose entries expire
# ----------------------------------------------------------------------------
class TTLCache:
    """get / set / delete with a time-to-live (TTL). Uses Redis if available, else a dict."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[float, str]] = {}  # key -> (expires_at, json text)
        self._redis = None
        if settings.redis_url:
            try:
                import redis

                self._redis = redis.Redis.from_url(settings.redis_url, socket_timeout=1)
                self._redis.ping()
            except Exception:
                self._redis = None

    def get(self, key: str):
        if self._redis:
            raw = self._redis.get(key)
        else:
            entry = self._data.get(key)
            raw = entry[1] if entry and entry[0] > time.time() else None
        METRICS.count("cache_hits" if raw is not None else "cache_misses")
        return json.loads(raw) if raw is not None else None

    def set(self, key: str, value, ttl: int) -> None:
        raw = json.dumps(value)
        if self._redis:
            self._redis.setex(key, ttl, raw)
        else:
            self._data[key] = (time.time() + ttl, raw)

    def delete(self, key: str) -> None:
        if self._redis:
            self._redis.delete(key)
        else:
            self._data.pop(key, None)

    def delete_prefix(self, prefix: str) -> None:
        """Delete every key that starts with `prefix` (used when a cancelled booking frees slots)."""
        if self._redis:
            for key in self._redis.scan_iter(f"{prefix}*"):
                self._redis.delete(key)
        else:
            for key in [k for k in self._data if k.startswith(prefix)]:
                del self._data[key]

    def stats(self) -> dict:
        hits, misses = METRICS.counters["cache_hits"], METRICS.counters["cache_misses"]
        total = hits + misses
        return {"hits": hits, "misses": misses, "hit_rate": round(hits / total, 2) if total else 0.0,
                "backend": "redis" if self._redis else "memory"}


cache = TTLCache()


# ----------------------------------------------------------------------------
# Layer 2: patient preferences (things that should survive between conversations)
# ----------------------------------------------------------------------------
def get_prefs(store, patient_id: str) -> dict:
    """Return {"usual_clinic": "Baner", ...} for this patient."""
    return {item.key: item.value["value"] for item in store.search(("patient", patient_id, "prefs"))}


def set_pref(store, patient_id: str, key: str, value) -> None:
    store.put(("patient", patient_id, "prefs"), key, {"value": value})


# ----------------------------------------------------------------------------
# Layer 3: agent lessons (the "self-learning" part)
#
# The loop:  doctor edits a draft  ->  we record a lesson (candidate)
#            -> after enough repeats it is "ready_for_approval"
#            -> a HUMAN approves it  ->  it becomes "active"
#            -> next draft shows it as a suggestion (still needs the doctor to confirm).
# Lessons only ever add SUGGESTIONS. They never change safety rules.
# ----------------------------------------------------------------------------
def _lesson_ns(doctor_id: str) -> tuple:
    return ("agent", "scribe", doctor_id, "lessons")


def record_lesson(store, doctor_id: str, key: str, suggestion: str) -> dict:
    """Count one more time that this doctor chose `suggestion` for `key`."""
    item = store.get(_lesson_ns(doctor_id), key)
    lesson = item.value if item else {"key": key, "suggestion": suggestion, "count": 0, "status": "candidate"}
    if lesson["suggestion"] != suggestion:  # the doctor changed their mind: start counting again
        lesson.update(suggestion=suggestion, count=0, status="candidate")
    lesson["count"] += 1
    if lesson["status"] == "candidate" and lesson["count"] >= settings.promote_after:
        lesson["status"] = "ready_for_approval"
    store.put(_lesson_ns(doctor_id), key, lesson)
    return lesson


def approve_lesson(store, doctor_id: str, key: str) -> dict | None:
    """The human step. Only a doctor can turn a lesson on."""
    item = store.get(_lesson_ns(doctor_id), key)
    if not item:
        return None
    lesson = {**item.value, "status": "active"}
    store.put(_lesson_ns(doctor_id), key, lesson)
    return lesson


def all_lessons(store, doctor_id: str) -> list[dict]:
    return [item.value for item in store.search(_lesson_ns(doctor_id))]


def active_lessons(store, doctor_id: str) -> list[dict]:
    return [lesson for lesson in all_lessons(store, doctor_id) if lesson["status"] == "active"]
