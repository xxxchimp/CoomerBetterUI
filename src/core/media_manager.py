from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse

import requests
from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QImage

from src.core.dto.media import MediaDTO
from src.media.processor import MediaProcessor, VIDEO_EXTS

logger = logging.getLogger(__name__)


class MediaManager:
    """
    Core media access + caching.

    Responsibilities:
    - Resolve local vs remote media paths
    - Download remote media into a deterministic cache
    - Provide QImage decoding via MediaProcessor (no threading)
    """
    _global_download_lock = threading.Lock()
    _global_download_locks: dict[str, threading.Lock] = {}
    _moov_tail_max_bytes = 2 * 1024 * 1024
    _hash_re = re.compile(r"^[0-9a-f]{32,128}$")
    _search_hash_timeout_s = 8

    def __init__(
        self,
        *,
        cache_dir: Path,
        db_manager=None,
        core_context=None,
        allow_full_video_download: bool = True,
        video_thumb_max_bytes: Optional[int] = None,
        video_thumb_max_non_faststart_bytes: Optional[int] = None,
        video_thumb_retries: int = 0,
        video_thumb_retry_delay_ms: int = 0,
        partial_max_bytes: int = 4 * 1024 * 1024,
    ):
        self.cache_dir = Path(cache_dir)
        self.raw_dir = self.cache_dir / "raw"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self._processor = MediaProcessor()
        self._partial_max_bytes = max(1, int(partial_max_bytes))
        self._download_lock = MediaManager._global_download_lock
        self._download_locks = MediaManager._global_download_locks
        self._remote_size_cache: dict[str, int] = {}
        self._hash_size_cache: dict[str, int] = {}
        self._db = db_manager
        self._core_context = core_context
        self._allow_full_video_download = bool(allow_full_video_download)
        self._video_thumb_max_bytes = self._normalize_limit(video_thumb_max_bytes)
        self._video_thumb_max_non_faststart_bytes = self._normalize_limit(
            video_thumb_max_non_faststart_bytes
        )
        self._video_thumb_retries = max(0, int(video_thumb_retries))
        self._video_thumb_retry_delay_ms = max(0, int(video_thumb_retry_delay_ms))

    def _get_download_lock(self, key: str) -> threading.Lock:
        full_key = f"{self.raw_dir.resolve()}|{key}"
        with self._download_lock:
            lock = self._download_locks.get(full_key)
            if lock is None:
                lock = threading.Lock()
                self._download_locks[full_key] = lock
            return lock

    def _maybe_proxy_url(self, url: str) -> str:
        """
        Return proxied URL if range proxy is enabled, otherwise return original URL.

        This allows video thumbnail generation to benefit from range proxy caching.
        """
        if not self._core_context or not self._db:
            return url

        # Check if range proxy is enabled in settings
        try:
            enabled = self._db.get_config('enable_range_proxy', 'false') == 'true'
            if not enabled:
                return url

            # Get range proxy and return proxied URL
            return self._core_context.range_proxy.proxy_url(url)
        except Exception:
            # Fallback to original URL if proxy fails
            return url

    def get_local_path(self, media: MediaDTO | str | Path) -> Path:
        if isinstance(media, MediaDTO):
            if media.local_path:
                return Path(media.local_path)
            if media.url:
                return self._download_media(media.url)
        if isinstance(media, Path):
            return media
        if isinstance(media, str):
            if media.startswith("file://"):
                return Path(media[7:])
            p = Path(media)
            if p.exists():
                return p
            return self._download_media(media)
        raise RuntimeError("Unsupported media type for resolution")

    def load_image(self, media: MediaDTO | str | Path, size: Tuple[int, int] | QSize) -> QImage:
        path = self.get_local_path(media)
        return self._processor.generate_image_thumbnail(path, size)

    def load_thumbnail(
        self,
        media: MediaDTO | str | Path,
        size: Tuple[int, int] | QSize,
        *,
        timestamp: Optional[float] = None,
    ) -> QImage:
        cached = self._load_cached_thumbnail(media, size)
        if cached is not None and not cached.isNull():
            return cached
        if self._is_video_media(media) and self._is_remote_media(media):
            return self._load_remote_video_thumbnail(
                media,
                size,
                timestamp=timestamp,
            )
        path = self.get_local_path(media)
        return self._processor.generate_thumbnail(path, size, timestamp=timestamp)

    @staticmethod
    def build_media_url(platform: str, file_path: str) -> str:
        if not file_path:
            return ""
        if file_path.startswith("http"):
            return file_path

        normalized = file_path.lstrip("/")
        if normalized.startswith("data/"):
            normalized = normalized[len("data/"):]

        base = "https://kemono.cr" if platform == "kemono" else "https://coomer.st"
        return f"{base}/data/{normalized}"

    @staticmethod
    def build_creator_icon_url(platform: str, service: str, creator_id: str) -> str:
        if not creator_id:
            return ""
        base = "https://img.kemono.cr" if platform == "kemono" else "https://img.coomer.st"
        return f"{base}/icons/{service}/{creator_id}"

    def _download_media(self, url: str) -> Path:
        parsed = urlparse(url)
        if parsed.scheme == "file":
            return Path(parsed.path)
        if parsed.scheme and parsed.scheme not in {"http", "https"}:
            raise RuntimeError(f"Unsupported media URL scheme: {parsed.scheme}")

        suffix = Path(parsed.path).suffix or ".bin"
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        target = self.raw_dir / f"{digest}{suffix}"
        if target.exists():
            return target
        lock = self._get_download_lock(digest)
        with lock:
            if target.exists():
                return target
            tmp = target.with_suffix(target.suffix + ".tmp")
            if tmp.exists():
                try:
                    tmp.unlink()
                except Exception:
                    pass
            resp = requests.get(url, stream=True, timeout=120, allow_redirects=True)
            logger.debug(f"download {url} -> {resp.url} {resp.status_code}")
            if not resp.ok:
                raise RuntimeError(f"Failed to download media: HTTP {resp.status_code}")
            self._cache_remote_size(url, resp.headers)
            with open(tmp, "wb") as f:
                for chunk in resp.iter_content(1024 * 128):
                    if chunk:
                        f.write(chunk)
            tmp.replace(target)
            try:
                file_size = target.stat().st_size
            except Exception:
                file_size = None
            self._record_remote_content(
                url,
                resp.headers,
                content_length_override=file_size,
            )
            return target

    def _download_media_partial(self, url: str, target: Path, *, max_bytes: Optional[int] = None) -> Path:
        if max_bytes is None:
            max_bytes = self._partial_max_bytes
        max_bytes = max(1, int(max_bytes))
        lock = self._get_download_lock(f"{target.name}.partial")
        with lock:
            if target.exists():
                return target
            tmp = target.with_suffix(target.suffix + ".tmp")
            if tmp.exists():
                try:
                    tmp.unlink()
                except Exception:
                    pass
            headers = {"Range": f"bytes=0-{max_bytes - 1}"}
            resp = requests.get(url, stream=True, timeout=120, allow_redirects=True, headers=headers)
            logger.debug(
                f"download partial {url} -> {resp.url} {resp.status_code} "
                f"range={headers.get('Range')}"
            )
            if resp.status_code not in {200, 206}:
                raise RuntimeError(f"Failed to download media: HTTP {resp.status_code}")
            if resp.status_code == 200:
                accept_ranges = (resp.headers.get("Accept-Ranges") or "").lower()
                content_range = resp.headers.get("Content-Range")
                if accept_ranges != "bytes" and not content_range:
                    resp.close()
                    raise RuntimeError("Range requests not supported")
            self._cache_remote_size(url, resp.headers)
            self._record_remote_content(url, resp.headers)
            with open(tmp, "wb") as f:
                read_bytes = 0
                for chunk in resp.iter_content(1024 * 128):
                    if not chunk:
                        continue
                    remaining = max_bytes - read_bytes
                    if remaining <= 0:
                        break
                    if len(chunk) > remaining:
                        f.write(chunk[:remaining])
                        break
                    f.write(chunk)
                    read_bytes += len(chunk)
            tmp.replace(target)
            return target

    def _is_remote_media(self, media: MediaDTO | str | Path) -> bool:
        if isinstance(media, MediaDTO):
            return bool(media.url)
        if isinstance(media, Path):
            return False
        if isinstance(media, str):
            return media.startswith("http")
        return False

    def _is_video_media(self, media: MediaDTO | str | Path) -> bool:
        if isinstance(media, MediaDTO):
            return media.type == "video"
        if isinstance(media, Path):
            return media.suffix.lower() in VIDEO_EXTS
        if isinstance(media, str):
            suffix = Path(urlparse(media).path).suffix.lower()
            return suffix in VIDEO_EXTS
        return False

    def _get_partial_path(
        self,
        media: MediaDTO | str | Path,
        *,
        max_bytes: Optional[int] = None,
    ) -> Optional[Path]:
        url = None
        if isinstance(media, MediaDTO):
            url = media.url
        elif isinstance(media, str):
            url = media
        if not url:
            return None
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return None
        target = self._partial_target_path(url)
        if target.exists():
            if max_bytes is None or target.stat().st_size >= max_bytes:
                return target
            try:
                target.unlink()
            except Exception:
                return target
        try:
            return self._download_media_partial(url, target, max_bytes=max_bytes)
        except Exception:
            return None

    def _partial_target_path(self, url: str) -> Path:
        parsed = urlparse(url)
        suffix = Path(parsed.path).suffix or ".bin"
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.raw_dir / f"{digest}{suffix}.partial"

    def _clear_partial_cache(self, url: str) -> None:
        try:
            target = self._partial_target_path(url)
        except Exception:
            return
        if target.exists():
            try:
                target.unlink()
            except Exception:
                pass

    def _cache_remote_size(self, url: str, headers: dict) -> Optional[int]:
        total = self._extract_total_length(headers)
        if total is not None and total > 0:
            self._remote_size_cache[url] = total
        return total

    def _lookup_size_from_search_hash(self, url: str) -> Optional[int]:
        if not url:
            return None
        cached = self._remote_size_cache.get(url)
        if cached:
            return cached
        file_hash = self._extract_hash_from_url(url)
        if not file_hash:
            return None
        cached_hash = self._hash_size_cache.get(file_hash)
        if cached_hash:
            self._remote_size_cache[url] = cached_hash
            return cached_hash
        base = self._search_hash_api_base(url)
        if not base:
            return None
        api_url = f"{base}/v1/search_hash/{file_hash}"
        try:
            resp = requests.get(
                api_url,
                timeout=self._search_hash_timeout_s,
                headers={
                    "User-Agent": "Coomer-BetterUI",
                    "Accept": "text/css",
                },
            )
        except Exception:
            return None
        if not resp.ok:
            return None
        try:
            data = resp.json()
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        size_raw = data.get("size")
        try:
            size = int(size_raw)
        except (TypeError, ValueError):
            return None
        if size <= 0:
            return None
        self._hash_size_cache[file_hash] = size
        self._remote_size_cache[url] = size
        try:
            self._record_remote_content(url, {}, content_length_override=size)
        except Exception:
            pass
        return size

    def _extract_hash_from_url(self, url: str) -> Optional[str]:
        try:
            parsed = urlparse(url)
            stem = Path(parsed.path).stem.lower()
        except Exception:
            return None
        if not stem or not self._hash_re.fullmatch(stem):
            return None
        return stem

    def _search_hash_api_base(self, url: str) -> Optional[str]:
        try:
            parsed = urlparse(url)
            host = (parsed.netloc or "").lower()
        except Exception:
            return None
        if "coomer" in host:
            return "https://coomer.st/api"
        if "kemono" in host:
            return "https://kemono.cr/api"
        return None

    def apply_video_thumbnail_config(
        self,
        *,
        max_video_bytes: Optional[int] = None,
        max_non_faststart_bytes: Optional[int] = None,
        retries: Optional[int] = None,
        retry_delay_ms: Optional[int] = None,
    ) -> None:
        if max_video_bytes is not None:
            self._video_thumb_max_bytes = self._normalize_limit(max_video_bytes)
        if max_non_faststart_bytes is not None:
            self._video_thumb_max_non_faststart_bytes = self._normalize_limit(
                max_non_faststart_bytes
            )
        if retries is not None:
            self._video_thumb_retries = max(0, int(retries))
        if retry_delay_ms is not None:
            self._video_thumb_retry_delay_ms = max(0, int(retry_delay_ms))

    def _extract_total_length(self, headers: dict) -> Optional[int]:
        total = None
        content_range = headers.get("Content-Range")
        if content_range:
            try:
                total_str = content_range.split("/")[-1]
                if total_str and total_str != "*":
                    total = int(total_str)
            except (TypeError, ValueError):
                total = None
        if total is None:
            content_length = headers.get("Content-Length")
            if content_length:
                try:
                    total = int(content_length)
                except (TypeError, ValueError):
                    total = None
        if total is not None and total <= 0:
            return None
        return total

    def _record_remote_content(
        self,
        url: str,
        headers: dict,
        *,
        content_length_override: Optional[int] = None,
    ) -> None:
        if not self._db:
            return
        etag = headers.get("ETag")
        last_modified = headers.get("Last-Modified")
        content_length = self._extract_total_length(headers)
        if content_length is None and content_length_override is not None:
            try:
                content_length = int(content_length_override)
            except (TypeError, ValueError):
                content_length = None
        content_id = self._build_content_id(etag, last_modified, content_length, url)
        media_type = self._infer_media_type_from_url(url)
        try:
            self._db.cache_media_content(
                content_id,
                media_type=media_type,
                etag=etag,
                last_modified=last_modified,
                content_length=content_length,
            )
            self._db.map_media_url(
                url,
                content_id,
                etag=etag,
                last_modified=last_modified,
                content_length=content_length,
            )
        except Exception:
            pass

    def _build_content_id(
        self,
        etag: Optional[str],
        last_modified: Optional[str],
        content_length: Optional[int],
        url: str,
    ) -> str:
        if etag or last_modified:
            base = f"{etag or ''}|{last_modified or ''}|{content_length or ''}"
        else:
            base = f"{url}|{content_length or ''}"
        return hashlib.sha256(base.encode("utf-8")).hexdigest()

    def record_thumbnail_for_url(
        self,
        url: str,
        size: Tuple[int, int] | QSize,
        thumbnail_path: Path | str,
    ) -> None:
        if not self._db:
            return
        content_id = self._db.get_content_id_for_url(url)
        if not content_id:
            return
        try:
            width, height = self._normalize_size(size)
        except Exception:
            return
        try:
            self._db.cache_thumbnail_for_content(
                content_id,
                width,
                height,
                str(thumbnail_path),
            )
        except Exception:
            pass

    def _normalize_limit(self, value: Optional[int]) -> Optional[int]:
        if value is None:
            return None
        try:
            value = int(value)
        except (TypeError, ValueError):
            return None
        if value <= 0:
            return None
        return value

    def _normalize_size(self, size: Tuple[int, int] | QSize) -> Tuple[int, int]:
        if isinstance(size, QSize):
            return (int(size.width()), int(size.height()))
        if isinstance(size, tuple) and len(size) == 2:
            return (int(size[0]), int(size[1]))
        raise ValueError(f"Invalid size: {size}")

    def _load_remote_video_thumbnail(
        self,
        media: MediaDTO | str | Path,
        size: Tuple[int, int] | QSize,
        *,
        timestamp: Optional[float] = None,
    ) -> QImage:
        url = media.url if isinstance(media, MediaDTO) else str(media)
        last_error = None
        attempts = self._video_thumb_retries + 1
        for attempt in range(attempts):
            try:
                return self._load_remote_video_thumbnail_once(
                    url,
                    size,
                    timestamp=timestamp,
                )
            except Exception as exc:
                last_error = exc
                self._clear_partial_cache(url)
                if attempt < attempts - 1 and self._video_thumb_retry_delay_ms > 0:
                    time.sleep(self._video_thumb_retry_delay_ms / 1000.0)
        if last_error:
            raise last_error
        raise RuntimeError("Failed to generate video thumbnail")

    def _load_remote_video_thumbnail_once(
        self,
        url: str,
        size: Tuple[int, int] | QSize,
        *,
        timestamp: Optional[float] = None,
    ) -> QImage:
        limits_set = (
            self._video_thumb_max_bytes is not None
            or self._video_thumb_max_non_faststart_bytes is not None
        )
        # Check if file was previously flagged as too large (early exit to avoid downloads)
        if self._db and limits_set:
            oversized = self._db.is_file_oversized(url)
            if oversized:
                logger.info(
                    f"Skipping video thumbnail - file flagged as oversized: {url[:50]}... "
                    f"(size: {oversized['file_size'] // (1024*1024)}MB, limit: {oversized['size_limit'] // (1024*1024)}MB)"
                )
                raise RuntimeError(f"Video exceeds size limit ({oversized['file_size'] // (1024*1024)}MB)")
        elif self._db and not limits_set:
            # Clear any stale oversized flag when limits are disabled.
            try:
                self._db.remove_oversized_flag(url)
            except Exception:
                pass
        if self._is_hls_url(url):
            return self._processor.generate_hls_thumbnail(
                url,
                size,
                timestamp or 0.0,
            )
        if self._should_try_http_seek(url):
            total_size, range_supported, headers = self._probe_remote_range(url)
            if range_supported:
                allowed_total = self._video_thumb_limit_bytes(False)
                size_ok = True
                if allowed_total is not None:
                    size_ok = total_size is not None and total_size <= allowed_total
                elif limits_set and total_size is None:
                    size_ok = False
                if size_ok:
                    try:
                        proxied_url = self._maybe_proxy_url(url)
                        return self._processor.generate_video_thumbnail_from_url(
                            proxied_url,
                            size,
                            timestamp or 0.0,
                        )
                    except Exception:
                        pass
        max_total_bytes = self._video_thumb_max_bytes
        max_non_faststart_bytes = self._video_thumb_max_non_faststart_bytes
        probe_bytes = self._partial_max_bytes
        if max_non_faststart_bytes is not None:
            probe_bytes = min(probe_bytes, max_non_faststart_bytes)

        partial = self._get_partial_path(url, max_bytes=probe_bytes)
        if partial is None:
            raise RuntimeError("Failed to download partial media")

        is_faststart = True
        if self._is_mp4_like(url):
            is_faststart = self._is_faststart_mp4(partial)
        if not is_faststart and self._is_mp4_like(url):
            moov_thumb = self._try_moov_only_thumbnail(url, partial, size, timestamp)
            if moov_thumb is not None and not moov_thumb.isNull():
                return moov_thumb

        allowed_total = self._video_thumb_limit_bytes(is_faststart)
        total_size = self._remote_size_cache.get(url)
        if total_size is None:
            total_size = self._lookup_size_from_search_hash(url)
        if allowed_total is not None and total_size is not None and total_size > allowed_total:
            raise RuntimeError("Video exceeds thumbnail size limit")

        try:
            return self._processor.generate_thumbnail(
                partial,
                size,
                timestamp=timestamp,
            )
        except Exception:
            pass

        if not is_faststart and max_non_faststart_bytes is not None:
            expanded_bytes = max_non_faststart_bytes
            if max_total_bytes is not None:
                expanded_bytes = min(expanded_bytes, max_total_bytes)
            if expanded_bytes > probe_bytes:
                partial = self._get_partial_path(url, max_bytes=expanded_bytes)
                if partial is not None:
                    is_faststart = self._is_faststart_mp4(partial) if self._is_mp4_like(url) else True
                    allowed_total = self._video_thumb_limit_bytes(is_faststart)
                    total_size = self._remote_size_cache.get(url)
                    if total_size is None:
                        total_size = self._lookup_size_from_search_hash(url)
                    if allowed_total is not None and total_size is not None and total_size > allowed_total:
                        raise RuntimeError("Video exceeds thumbnail size limit")
                    return self._processor.generate_thumbnail(
                        partial,
                        size,
                        timestamp=timestamp,
                    )

        if not self._allow_full_video_download:
            raise RuntimeError("Full video downloads disabled for thumbnails")

        allowed_total = self._video_thumb_limit_bytes(is_faststart)
        if total_size is None:
            total_size = self._lookup_size_from_search_hash(url)
        if allowed_total is not None and total_size is None:
            raise RuntimeError("Video size unknown; refusing full download for thumbnail")
        if allowed_total is not None and total_size is not None and total_size > allowed_total:
            raise RuntimeError("Video exceeds thumbnail size limit")

        path = self.get_local_path(url)
        return self._processor.generate_thumbnail(path, size, timestamp=timestamp)

    def _is_mp4_like(self, url: str) -> bool:
        suffix = Path(urlparse(url).path).suffix.lower()
        return suffix in {".mp4", ".mov", ".m4v"}

    def _infer_media_type_from_url(self, url: str) -> str:
        suffix = Path(urlparse(url).path).suffix.lower()
        if suffix in VIDEO_EXTS:
            return "video"
        return "image"

    def _is_hls_url(self, url: str) -> bool:
        suffix = Path(urlparse(url).path).suffix.lower()
        if suffix == ".m3u8":
            return True
        if "m3u8" in url.lower():
            return True
        return False

    def _load_cached_thumbnail(
        self,
        media: MediaDTO | str | Path,
        size: Tuple[int, int] | QSize,
    ) -> Optional[QImage]:
        if not self._db:
            return None
        if not self._is_remote_media(media):
            return None
        url = media.url if isinstance(media, MediaDTO) else str(media)
        content_id = self._db.get_content_id_for_url(url)
        if not content_id:
            return None
        try:
            width, height = self._normalize_size(size)
        except Exception:
            return None
        cached = self._db.get_cached_thumbnail(content_id, width, height)
        if not cached:
            variants = self._db.get_thumbnail_variants(content_id)
            if not variants:
                return None
            target_area = max(1, width * height)
            best = None
            best_score = None
            for item in variants:
                w = item.get("width")
                h = item.get("height")
                if not w or not h:
                    continue
                # Skip variants that are smaller than requested - don't upscale
                if w < width and h < height:
                    continue
                area = w * h
                diff = abs(area - target_area)
                score = diff + (target_area if area < target_area else 0)
                if best_score is None or score < best_score:
                    best_score = score
                    best = item
            if not best:
                return None
            cached = best
        path = cached.get("thumbnail_path")
        if not path:
            return None
        thumb_path = Path(path)
        if not thumb_path.exists():
            return None
        img = QImage(str(thumb_path))
        if img.isNull():
            return None
        resized = False
        if img.width() != width or img.height() != height:
            img = img.scaled(
                width,
                height,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            resized = True
        try:
            self._db.touch_thumbnail_entry(
                content_id,
                cached.get("width", width),
                cached.get("height", height),
            )
        except Exception:
            pass
        if resized:
            self._persist_resized_thumbnail(content_id, width, height, img, thumb_path)
        return img

    def _persist_resized_thumbnail(
        self,
        content_id: str,
        width: int,
        height: int,
        img: QImage,
        source_path: Path,
    ) -> None:
        if not self._db or img.isNull():
            return
        try:
            base = source_path.stem
            suffix = source_path.suffix or ".png"
            target = source_path.with_name(f"{base}_{width}x{height}{suffix}")
            img.save(str(target), "PNG")
            self._db.cache_thumbnail_for_content(content_id, width, height, str(target))
        except Exception:
            pass

    def _video_thumb_limit_bytes(self, is_faststart: bool) -> Optional[int]:
        max_total_bytes = self._video_thumb_max_bytes
        max_non_faststart_bytes = self._video_thumb_max_non_faststart_bytes
        if not is_faststart and max_non_faststart_bytes is not None:
            if max_total_bytes is None:
                return max_non_faststart_bytes
            return min(max_total_bytes, max_non_faststart_bytes)
        return max_total_bytes

    def _should_try_http_seek(self, url: str) -> bool:
        if not url:
            return False
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False
        return True

    def _probe_remote_range(self, url: str) -> tuple[Optional[int], bool, dict]:
        headers = {}
        try:
            resp = requests.get(
                url,
                stream=True,
                timeout=10,
                allow_redirects=True,
                headers={"Range": "bytes=0-0"},
            )
            headers = dict(resp.headers)
            status = resp.status_code
            range_supported = status == 206
            if not range_supported:
                accept_ranges = (headers.get("Accept-Ranges") or "").lower()
                range_supported = accept_ranges == "bytes" or headers.get("Content-Range") is not None
            total_size = self._extract_total_length(headers)
            if total_size is None:
                total_size = self._lookup_size_from_search_hash(url)
            try:
                resp.close()
            except Exception:
                pass
            self._record_remote_content(url, headers)
            if not range_supported:
                return total_size, False, headers
            return total_size, True, headers
        except Exception:
            return None, False, headers

    def _try_moov_only_thumbnail(
        self,
        url: str,
        partial_path: Path,
        size: Tuple[int, int] | QSize,
        timestamp: Optional[float],
    ) -> Optional[QImage]:
        limits_set = self._video_thumb_max_non_faststart_bytes is not None
        # Check if file was previously flagged as too large
        if self._db and limits_set:
            oversized = self._db.is_file_oversized(url)
            if oversized:
                logger.info(
                    f"Skipping thumbnail for oversized file: {url[:50]}... "
                    f"(size: {oversized['file_size'] // (1024*1024)}MB, limit: {oversized['size_limit'] // (1024*1024)}MB)"
                )
                return None
        elif self._db and not limits_set:
            try:
                self._db.remove_oversized_flag(url)
            except Exception:
                pass
        
        total_size = self._remote_size_cache.get(url)
        range_supported = True
        if total_size is None:
            total_size, range_supported, _ = self._probe_remote_range(url)
        
        # Check and flag if file exceeds limits
        if total_size and self._db:
            max_non_faststart = self._video_thumb_max_non_faststart_bytes
            if max_non_faststart and total_size > max_non_faststart:
                logger.warning(
                    f"File too large for thumbnail: {url[:50]}... "
                    f"({total_size // (1024*1024)}MB > {max_non_faststart // (1024*1024)}MB)"
                )
                self._db.flag_file_as_oversized(url, total_size, max_non_faststart)
                return None
        
        if not range_supported or not total_size:
            return None
        tail_bytes = self._moov_tail_max_bytes
        max_non_faststart = self._video_thumb_max_non_faststart_bytes
        if max_non_faststart is not None:
            tail_bytes = min(tail_bytes, max_non_faststart)
        if tail_bytes <= 0:
            return None
        moov_bytes = self._download_moov_atom(url, total_size, tail_bytes)
        if not moov_bytes:
            return None
        temp_path = partial_path.with_suffix(partial_path.suffix + ".moovtmp")
        try:
            with open(partial_path, "rb") as src, open(temp_path, "wb") as dst:
                dst.write(src.read())
                dst.write(moov_bytes)
            return self._processor.generate_thumbnail(
                temp_path,
                size,
                timestamp=timestamp,
            )
        except Exception:
            return None
        finally:
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except Exception:
                pass

    def _download_moov_atom(
        self,
        url: str,
        total_size: int,
        tail_bytes: int,
    ) -> Optional[bytes]:
        if total_size <= 0:
            return None
        tail_bytes = min(tail_bytes, total_size)
        if tail_bytes <= 0:
            return None
        start = max(0, total_size - tail_bytes)
        headers = {"Range": f"bytes={start}-{total_size - 1}"}
        try:
            resp = requests.get(url, stream=True, timeout=20, allow_redirects=True, headers=headers)
            if resp.status_code != 206:
                resp.close()
                return None
            data = resp.content
        except Exception:
            return None
        finally:
            try:
                resp.close()
            except Exception:
                pass
        return self._extract_moov_atom(data)

    def _extract_moov_atom(self, data: bytes) -> Optional[bytes]:
        if not data:
            return None
        last_match = None
        idx = 0
        while True:
            idx = data.find(b"moov", idx)
            if idx < 0:
                break
            start = idx - 4
            if start < 0:
                idx += 4
                continue
            size = int.from_bytes(data[start:start + 4], "big")
            header = 8
            if size == 1:
                if idx + 12 > len(data):
                    idx += 4
                    continue
                size = int.from_bytes(data[idx + 4:idx + 12], "big")
                header = 16
                start = idx - 4
            end = start + size
            if size >= header and end <= len(data):
                last_match = data[start:end]
            idx += 4
        return last_match

    def _is_faststart_mp4(self, path: Path) -> bool:
        try:
            data = path.read_bytes()
        except Exception:
            return False
        moov_offset = self._find_mp4_atom(data, b"moov")
        if moov_offset < 0:
            return False
        mdat_offset = self._find_mp4_atom(data, b"mdat")
        if mdat_offset < 0:
            return True
        return moov_offset < mdat_offset

    def _find_mp4_atom(self, data: bytes, atom: bytes) -> int:
        offset = 0
        data_len = len(data)
        while offset + 8 <= data_len:
            try:
                size = int.from_bytes(data[offset:offset + 4], "big")
            except Exception:
                return -1
            typ = data[offset + 4:offset + 8]
            if typ == atom:
                return offset
            if size == 0:
                break
            if size == 1:
                if offset + 16 > data_len:
                    break
                size = int.from_bytes(data[offset + 8:offset + 16], "big")
                header_size = 16
            else:
                header_size = 8
            if size < header_size:
                break
            offset += size
        return -1
