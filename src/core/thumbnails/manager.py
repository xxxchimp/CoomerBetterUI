from __future__ import annotations

import logging
import threading
import time
import weakref
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures.thread import _worker
from pathlib import Path
from typing import Deque, Dict, Optional, Tuple

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QImage

from src.core.dto.thumbnail import ThumbnailRequest, ThumbnailResult
from src.core.thumbnails.handle import ThumbnailHandle
from src.core.media_manager import MediaManager

from src.media.processor import MediaProcessor

logger = logging.getLogger(__name__)

VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v"}

# ------------------------------------------------------------
# ThumbnailManager
# ------------------------------------------------------------

class DaemonThreadPoolExecutor(ThreadPoolExecutor):
    def _adjust_thread_count(self):
        # Mirror ThreadPoolExecutor but set daemon threads.
        if self._broken:
            return
        num_threads = len(self._threads)
        if num_threads >= self._max_workers:
            return

        thread_name = "%s_%d" % (self._thread_name_prefix or self, num_threads)

        def weakref_cb(_, q=self._work_queue):
            q.put(None)

        t = threading.Thread(
            name=thread_name,
            target=_worker,
            args=(weakref.ref(self, weakref_cb), self._work_queue, self._initializer, self._initargs),
            daemon=True,
        )
        t.start()
        self._threads.add(t)


class ThumbnailManager(QObject):
    """
    Central thumbnail pipeline.

    Responsibilities:
    - Request deduplication (1C)
    - Thread pooling
    - Disk + memory caching
    - Video + image thumbnailing (1B)
    - UI-safe signal delivery
    """

    future_done = pyqtSignal(str, object)  # key, future

    def __init__(
        self,
        cache_dir: Path,
        max_workers: int = 4,
        image_workers: Optional[int] = None,
        video_workers: Optional[int] = None,
        video_queue_limit: Optional[int] = None,
        memory_cache_limit: int = 256,
        parent: Optional[QObject] = None,
        media_manager: Optional[MediaManager] = None,
        request_timeout_ms: int = 30000,
        video_timeout_ms: Optional[int] = None,
    ):
        super().__init__(parent)

        self.cache_dir = Path(cache_dir)

        image_workers = max_workers if image_workers is None else image_workers
        video_workers = 1 if video_workers is None else video_workers
        self._image_workers = max(1, int(image_workers))
        self._video_workers = max(1, int(video_workers))
        self._video_queue_limit = (
            max(1, int(video_queue_limit)) if video_queue_limit is not None else None
        )

        self._image_executor = DaemonThreadPoolExecutor(
            max_workers=self._image_workers,
            thread_name_prefix="thumbnail-image-worker",
        )
        self._video_executor = DaemonThreadPoolExecutor(
            max_workers=self._video_workers,
            thread_name_prefix="thumbnail-video-worker",
        )

        self._lock = threading.Lock()

        # cache_key -> active handle
        self._in_flight: Dict[str, ThumbnailHandle] = {}

        # cache_key -> QImage
        self._memory_cache: Dict[str, QImage] = {}

        self._memory_cache_limit = memory_cache_limit

        self._processor = MediaProcessor()
        self._media = media_manager or MediaManager(cache_dir=self.cache_dir / "media")
        self._request_timeout_ms = max(0, int(request_timeout_ms))
        if video_timeout_ms is None:
            self._video_timeout_ms = self._request_timeout_ms
        else:
            self._video_timeout_ms = max(0, int(video_timeout_ms))
        self._video_pending: Deque[Tuple[ThumbnailRequest, ThumbnailHandle, str]] = deque()
        self._video_in_flight: set[str] = set()
        self._video_outstanding = 0
        self._last_image_activity = 0.0
        self._image_timeout_max_resets = 2
        self.future_done.connect(self._on_future_done)

    # --------------------------------------------------------

    def request(self, req: ThumbnailRequest) -> ThumbnailHandle:
        """
        Entry point used by UI.

        Always returns immediately with a handle.
        """
        executor = self._select_executor(req)
        if getattr(executor, "_shutdown", False):
            executor = self._restore_executor(req)
        key = req.cache_key()
        handle = ThumbnailHandle()
        handle.timeout_resets = 0
        is_video = getattr(req.media, "type", None) == "video"

        with self._lock:
            # -------------------------------
            # Memory cache hit
            # -------------------------------
            if key in self._memory_cache:
                img = self._memory_cache[key]
                QTimer.singleShot(
                    0,
                    lambda img=img: handle.ready.emit(
                        ThumbnailResult(image=img, from_cache=True)
                    ),
                )
                return handle

            # -------------------------------
            # Deduplicate in-flight requests
            # -------------------------------
            if key in self._in_flight:
                existing = self._in_flight[key]
                existing.ready.connect(handle.ready)
                existing.failed.connect(handle.failed)
                return handle

            self._in_flight[key] = handle
            if is_video:
                if (
                    self._video_queue_limit is not None
                    and self._video_outstanding >= self._video_queue_limit
                ):
                    self._video_pending.append((req, handle, key))
                    return handle
                self._video_outstanding += 1
                self._video_in_flight.add(key)

        # -------------------------------
        # Schedule generation
        # -------------------------------
        logger.debug(f"Scheduling thumbnail generation for key={key}")
        self._schedule_generation(req, key, handle, executor, is_video=is_video)
        return handle

    def _schedule_generation(
        self,
        req: ThumbnailRequest,
        key: str,
        handle: ThumbnailHandle,
        executor: ThreadPoolExecutor,
        *,
        is_video: bool,
    ) -> None:
        future = executor.submit(self._generate, req, key)
        future.add_done_callback(lambda f, key=key: self.future_done.emit(key, f))

        timeout_ms = self._video_timeout_ms if is_video else self._request_timeout_ms
        if timeout_ms:
            QTimer.singleShot(
                timeout_ms,
                lambda key=key, handle=handle, future=future: self._on_request_timeout(key, handle, future),
            )

        handle._future = future  # internal use only
        if getattr(handle, "cancelled", False):
            future.cancel()

    def _select_executor(self, req: ThumbnailRequest) -> ThreadPoolExecutor:
        media_type = getattr(req.media, "type", None)
        if media_type == "video":
            return self._video_executor
        return self._image_executor

    def _restore_executor(self, req: ThumbnailRequest) -> ThreadPoolExecutor:
        media_type = getattr(req.media, "type", None)
        if media_type == "video":
            self._video_executor = DaemonThreadPoolExecutor(
                max_workers=self._video_workers,
                thread_name_prefix="thumbnail-video-worker",
            )
            return self._video_executor
        self._image_executor = DaemonThreadPoolExecutor(
            max_workers=self._image_workers,
            thread_name_prefix="thumbnail-image-worker",
        )
        return self._image_executor

    def _on_future_done(self, key: str, future) -> None:
        handle = None
        is_video = key in self._video_in_flight
        with self._lock:
            handle = self._in_flight.pop(key, None)
        if is_video:
            self._on_video_task_done(key)
        if handle is None:
            return
        if future.cancelled() or getattr(handle, "cancelled", False):
            return

        try:
            img = future.result()
        except Exception as e:
            logger.warning(f"Thumbnail generation failed for key={key}: {e}")
            handle.failed.emit(str(e))
            return

        self._store_in_memory(key, img)
        if not is_video:
            self._last_image_activity = time.monotonic()
        handle.ready.emit(ThumbnailResult(image=img, from_cache=False))

    # --------------------------------------------------------
    def _on_request_timeout(self, key: str, handle: ThumbnailHandle, future) -> None:
        if future.done():
            return
        with self._lock:
            if self._in_flight.get(key) is not handle:
                return
        if key not in self._video_in_flight and self._should_extend_image_timeout(handle):
            timeout_ms = self._request_timeout_ms
            if timeout_ms:
                QTimer.singleShot(
                    timeout_ms,
                    lambda key=key, handle=handle, future=future: self._on_request_timeout(key, handle, future),
                )
            return
        with self._lock:
            self._in_flight.pop(key, None)
        future.cancel()
        if key in self._video_in_flight:
            self._on_video_task_done(key)
        handle.failed.emit("Thumbnail request timed out")

    def _on_video_task_done(self, key: str) -> None:
        to_schedule: list[Tuple[ThumbnailRequest, ThumbnailHandle, str]] = []
        with self._lock:
            if key in self._video_in_flight:
                self._video_in_flight.discard(key)
                if self._video_outstanding > 0:
                    self._video_outstanding -= 1
            while self._video_pending and (
                self._video_queue_limit is None
                or self._video_outstanding < self._video_queue_limit
            ):
                req, handle, pending_key = self._video_pending.popleft()
                if getattr(handle, "cancelled", False):
                    self._in_flight.pop(pending_key, None)
                    continue
                self._video_outstanding += 1
                self._video_in_flight.add(pending_key)
                to_schedule.append((req, handle, pending_key))
        for req, handle, pending_key in to_schedule:
            executor = self._select_executor(req)
            if getattr(executor, "_shutdown", False):
                executor = self._restore_executor(req)
            self._schedule_generation(req, pending_key, handle, executor, is_video=True)

    # --------------------------------------------------------

    def _generate(self, req: ThumbnailRequest, key: str) -> QImage:
        """
        Runs in worker thread.
        Handles image *and* video thumbnail extraction.
        """
        logger.debug(f"Generating thumbnail for key={key}")
        cache_path = self.cache_dir / f"{key}.png"

        # -------------------------------
        # Disk cache
        # -------------------------------
        if cache_path.exists():
            img = QImage(str(cache_path))
            if not img.isNull():
                return img

        # -------------------------------
        # Media dispatch (1B)
        # -------------------------------
        img = self._media.load_thumbnail(
            req.media,
            req.size,
            timestamp=1.0,
        )

        if img is None or img.isNull():
            raise RuntimeError("Failed to generate thumbnail")

        img.save(str(cache_path), "PNG")
        try:
            url = getattr(req.media, "url", None)
            if url:
                self._media.record_thumbnail_for_url(url, req.size, cache_path)
        except Exception:
            pass
        return img

    # --------------------------------------------------------

    def _store_in_memory(self, key: str, img: QImage) -> None:
        """
        FIFO eviction - predictable + simple.
        """
        with self._lock:
            if key in self._memory_cache:
                return

            if len(self._memory_cache) >= self._memory_cache_limit:
                oldest = next(iter(self._memory_cache))
                self._memory_cache.pop(oldest, None)

            self._memory_cache[key] = img

    # --------------------------------------------------------

    def _should_extend_image_timeout(self, handle: ThumbnailHandle) -> bool:
        if not self._request_timeout_ms:
            return False
        if handle.timeout_resets >= self._image_timeout_max_resets:
            return False
        last_activity = self._last_image_activity
        if not last_activity:
            return False
        window_s = self._request_timeout_ms / 1000.0
        if (time.monotonic() - last_activity) > window_s:
            return False
        handle.timeout_resets += 1
        return True

    # --------------------------------------------------------

    def shutdown(self) -> None:
        self._image_executor.shutdown(wait=False, cancel_futures=True)
        self._video_executor.shutdown(wait=False, cancel_futures=True)

    def _resolve_media_path(self, req: ThumbnailRequest) -> Path:
        return self._media.get_local_path(req.media)
