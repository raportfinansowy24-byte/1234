import os
import sqlite3
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Optional, Any
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

class CacheBackend(Enum):
    SQLITE = "sqlite"
    REDIS = "redis"

@dataclass
class CacheEntry:
    key: str
    value: Any
    ttl_seconds: int
    created_at: float
    hit_count: int = 0
    access_time: float = None

    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > self.ttl_seconds

class CostOptimizer:
    def __init__(self, cache_db_path: str = "./cache/optimization.db", cache_backend: str = "sqlite"):
        self.cache_db_path = cache_db_path
        self.cache_backend = CacheBackend(cache_backend)
        self.in_memory_cache = {}
        self.metrics = {"cache_hits": 0, "cache_misses": 0, "api_calls_saved": 0, "cost_saved_usd": 0.0}
        
        Path(cache_db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_sqlite_db()
        logger.info(f"✅ Cost Optimizer initialized (backend: {self.cache_backend.value})")

    def _init_sqlite_db(self):
        conn = sqlite3.connect(self.cache_db_path)
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS cache_entries (
                key TEXT PRIMARY KEY, value TEXT NOT NULL, ttl_seconds INTEGER,
                created_at REAL, hit_count INTEGER DEFAULT 0, access_time REAL, cache_type TEXT DEFAULT 'generic'
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_cache_type ON cache_entries(cache_type)")
        conn.commit()
        conn.close()

    def hash_video_params(self, prompt: str, duration: float, height: int, width: int) -> str:
        params = f"{prompt}:{duration}:{height}:{width}"
        return hashlib.sha256(params.encode()).hexdigest()[:16]

    def cache_video(self, prompt: str, duration: float, height: int, width: int, video_url_or_path: str, ttl_hours: int = 168) -> None:
        key = f"video:{self.hash_video_params(prompt, duration, height, width)}"
        self._store_cache(key, video_url_or_path, ttl_hours * 3600, cache_type="video")

    def get_cached_video(self, prompt: str, duration: float, height: int, width: int) -> Optional[str]:
        key = f"video:{self.hash_video_params(prompt, duration, height, width)}"
        result = self._get_cache(key)
        if result and os.path.exists(result):
            self.metrics["api_calls_saved"] += 1
            self.metrics["cost_saved_usd"] += 0.20
            return result
        return None

    def _store_cache(self, key: str, value: Any, ttl_seconds: int, cache_type: str = "generic") -> None:
        entry = CacheEntry(key=key, value=value, ttl_seconds=ttl_seconds, created_at=time.time())
        self.in_memory_cache[key] = entry
        if self.cache_backend == CacheBackend.SQLITE:
            try:
                conn = sqlite3.connect(self.cache_db_path)
                c = conn.cursor()
                c.execute("""
                    INSERT OR REPLACE INTO cache_entries (key, value, ttl_seconds, created_at, hit_count, cache_type)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (key, json.dumps(value) if not isinstance(value, str) else value, ttl_seconds, entry.created_at, 0, cache_type))
                conn.commit()
                conn.close()
            except Exception as e:
                logger.warning(f"⚠️ Cache store failure: {e}")

    def _get_cache(self, key: str) -> Optional[Any]:
        if key in self.in_memory_cache:
            entry = self.in_memory_cache[key]
            if not entry.is_expired():
                entry.hit_count += 1
                self.metrics["cache_hits"] += 1
                return entry.value
            else:
                del self.in_memory_cache[key]

        if self.cache_backend == CacheBackend.SQLITE:
            try:
                conn = sqlite3.connect(self.cache_db_path)
                c = conn.cursor()
                c.execute("SELECT value, ttl_seconds, created_at FROM cache_entries WHERE key = ?", (key,))
                row = c.fetchone()
                conn.close()
                if row:
                    value, ttl_seconds, created_at = row
                    if (time.time() - created_at) <= ttl_seconds:
                        self.metrics["cache_hits"] += 1
                        parsed_val = json.loads(value) if (value.startswith("{") or value.startswith("[")) else value
                        self.in_memory_cache[key] = CacheEntry(key=key, value=parsed_val, ttl_seconds=ttl_seconds, created_at=created_at)
                        return parsed_val
            except Exception as e:
                pass
        self.metrics["cache_misses"] += 1
        return None

    def cleanup_expired_cache(self) -> int:
        try:
            conn = sqlite3.connect(self.cache_db_path)
            c = conn.cursor()
            c.execute("DELETE FROM cache_entries WHERE (? - created_at) > ttl_seconds", (time.time(),))
            deleted = c.rowcount
            conn.commit()
            conn.close()
            return deleted
        except:
            return 0

    def get_metrics(self) -> dict:
        hit_rate = (self.metrics["cache_hits"] / (self.metrics["cache_hits"] + self.metrics["cache_misses"])
                    if (self.metrics["cache_hits"] + self.metrics["cache_misses"]) > 0 else 0)
        return {**self.metrics, "hit_rate_percent": round(hit_rate * 100, 2)}

# Globalna instancja optymalizatora
_optimizer: Optional[CostOptimizer] = None
def get_optimizer() -> CostOptimizer:
    global _optimizer
    if _optimizer is None:
        _optimizer = CostOptimizer()
    return _optimizer
