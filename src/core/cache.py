from __future__ import annotations

import gzip
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


# ------------------------------------------------------------
# Cache policy
# ------------------------------------------------------------

@dataclass(frozen=True)
class CacheRule:
    ttl_seconds: int
    stale_if_error: bool = True


@dataclass(frozen=True)
class CacheDecision:
    enabled: bool
    ttl_seconds: int = 0
    stale_if_error: bool = False


# ------------------------------------------------------------
# Deterministic cache key
# ------------------------------------------------------------

def _normalize_params(params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Canonicalize params to stable JSON:
      - sort keys
      - normalize lists (keep order unless you explicitly want sorting)
      - normalize non-JSON scalars to strings
    """
    if not params:
        return {}

    def norm(v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, (str, int, float, bool)):
            return v
        if isinstance(v, (list, tuple)):
            return [norm(x) for x in v]
        if isinstance(v, dict):
            return {k: norm(v[k]) for k in sorted(v.keys())}
        return str(v)

    return {k: norm(params[k]) for k in sorted(params.keys())}


def make_cache_key(
    *,
    version_salt: str,
    platform: str,
    method: str,
    path: str,
    params: Optional[Dict[str, Any]],
    vary_headers: Optional[Dict[str, str]],
) -> str:
    payload = {
        "v": version_salt,
        "platform": platform,
        "method": method.upper(),
        "path": path,
        "params": _normalize_params(params),
        "vary_headers": dict(sorted((vary_headers or {}).items())),
    }
    blob = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


# ------------------------------------------------------------
# Cache backends
# ------------------------------------------------------------

class MemoryCache:
    """
    Simple deterministic memory cache with FIFO eviction.
    (You can upgrade to LRU later; FIFO is stable + predictable.)
    """
    def __init__(self, limit: int = 256):
        self._limit = max(1, limit)
        self._store: Dict[str, Tuple[float, Any]] = {}  # key -> (expires_at, data)

    def get(self, key: str) -> Optional[Any]:
        item = self._store.get(key)
        if not item:
            return None
        expires_at, data = item
        if expires_at and time.time() > expires_at:
            self._store.pop(key, None)
            return None
        return data

    def set(self, key: str, data: Any, ttl_seconds: int) -> None:
        if len(self._store) >= self._limit:
            oldest = next(iter(self._store))
            self._store.pop(oldest, None)
        expires_at = time.time() + ttl_seconds if ttl_seconds > 0 else 0.0
        self._store[key] = (expires_at, data)

    def clear(self) -> None:
        self._store.clear()


class DiskCache:
    """
    Deterministic disk cache:
      - stores gzipped JSON
      - atomic writes
      - per-entry expiry in a small sidecar header
    """
    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _paths(self, key: str) -> Tuple[Path, Path]:
        # shard to reduce directory fanout
        shard = key[:2]
        d = self.cache_dir / shard
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{key}.json.gz", d / f"{key}.meta.json"

    def get(self, key: str) -> Optional[Any]:
        data_path, meta_path = self._paths(key)
        if not data_path.exists() or not meta_path.exists():
            return None

        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            expires_at = float(meta.get("expires_at", 0))
            if expires_at and time.time() > expires_at:
                # expired; deterministic cleanup
                try:
                    data_path.unlink(missing_ok=True)
                    meta_path.unlink(missing_ok=True)
                except Exception:
                    pass
                return None

            with gzip.open(data_path, "rt", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            # Corrupt entry; remove deterministically
            try:
                data_path.unlink(missing_ok=True)
                meta_path.unlink(missing_ok=True)
            except Exception:
                pass
            return None

    def set(self, key: str, data: Any, ttl_seconds: int) -> None:
        data_path, meta_path = self._paths(key)
        expires_at = time.time() + ttl_seconds if ttl_seconds > 0 else 0.0

        tmp_data = data_path.with_suffix(data_path.suffix + ".tmp")
        tmp_meta = meta_path.with_suffix(meta_path.suffix + ".tmp")

        # write gzipped JSON
        with gzip.open(tmp_data, "wt", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))

        tmp_meta.write_text(
            json.dumps({"expires_at": expires_at}, separators=(",", ":")),
            encoding="utf-8",
        )

        # atomic replace
        os.replace(tmp_data, data_path)
        os.replace(tmp_meta, meta_path)

    def clear(self) -> None:
        for p in self.cache_dir.glob("*/*"):
            try:
                p.unlink()
            except Exception:
                pass


# ------------------------------------------------------------
# Combined cache facade
# ------------------------------------------------------------

class DeterministicCache:
    def __init__(
        self,
        *,
        version_salt: str,
        memory_limit: int,
        disk_dir: Path,
    ):
        self.version_salt = version_salt
        self.mem = MemoryCache(limit=memory_limit)
        self.disk = DiskCache(cache_dir=disk_dir)

    def get(self, key: str) -> Optional[Any]:
        hit = self.mem.get(key)
        if hit is not None:
            return hit
        hit = self.disk.get(key)
        if hit is not None:
            # memory warm
            self.mem.set(key, hit, ttl_seconds=0)  # ttl enforced by disk meta
            return hit
        return None

    def set(self, key: str, data: Any, ttl_seconds: int) -> None:
        # store to disk first for determinism across restarts
        self.disk.set(key, data, ttl_seconds=ttl_seconds)
        self.mem.set(key, data, ttl_seconds=ttl_seconds)

    def clear(self) -> None:
        self.mem.clear()
        self.disk.clear()
