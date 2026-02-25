"""
Deprecated image loader (UI adapter).

This module now delegates to Core ThumbnailManager and performs no HTTP/caching.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from urllib.parse import urlparse
from typing import Optional, Tuple

from PyQt6.QtCore import QObject, pyqtSignal, QSize
from PyQt6.QtGui import QPixmap

from src.core.dto.media import MediaDTO
from src.core.dto.thumbnail import ThumbnailRequest
from src.core.thumbnails import get_thumbnail_manager

logger = logging.getLogger(__name__)

VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v", ".flv"}


class ImageLoader(QObject):
    """
    UI adapter that delegates thumbnail generation to Core ThumbnailManager.
    """

    image_loaded = pyqtSignal(str, QPixmap)  # url, pixmap
    load_failed = pyqtSignal(str, str)  # url, error_msg

    def __init__(self, parent: Optional[QObject] = None, *, manager=None):
        super().__init__(parent)
        self._manager = manager or get_thumbnail_manager()
        self._handles = set()

    def load_image(self, url: str, target_size: Optional[Tuple[int, int]] = None, is_thumbnail: bool = True):
        if not url:
            self.load_failed.emit(url, "Invalid URL")
            return

        logger.debug("Image load requested: %s", url)
        size = self._normalize_size(target_size)
        media = self._media_from_url(url)
        req = ThumbnailRequest(media=media, size=size)
        handle = self._manager.request(req)
        self._handles.add(handle)

        def _done(result):
            try:
                logger.debug("Image load succeeded: %s", url)
                pix = QPixmap.fromImage(result.image)
                self.image_loaded.emit(url, pix)
            finally:
                self._handles.discard(handle)

        def _failed(err: str):
            try:
                logger.warning("Image load failed for %s: %s", url, err)
                self.load_failed.emit(url, err)
            finally:
                self._handles.discard(handle)

        handle.ready.connect(_done)
        handle.failed.connect(_failed)

    def cancel_all(self):
        for handle in list(self._handles):
            try:
                handle.cancel()
            except Exception:
                pass
        self._handles.clear()

    @staticmethod
    def _normalize_size(target_size: Optional[Tuple[int, int]]) -> QSize:
        if isinstance(target_size, QSize):
            return target_size
        if isinstance(target_size, tuple) and len(target_size) == 2:
            return QSize(int(target_size[0]), int(target_size[1]))
        return QSize(256, 256)

    @staticmethod
    def _media_from_url(url: str) -> MediaDTO:
        local_path = None
        if url.startswith("file://"):
            local_path = Path(url[7:])
        else:
            p = Path(url)
            if p.exists():
                local_path = p

        path = url
        try:
            parsed = urlparse(url)
            if parsed.scheme in {"http", "https", "file"}:
                path = parsed.path
        except Exception:
            pass
        ext = Path(path).suffix.lower()
        media_type = "video" if ext in VIDEO_EXTS else "image"
        media_id = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return MediaDTO(
            id=media_id,
            type=media_type,
            url=url,
            local_path=str(local_path) if local_path else None,
            mime=None,
        )
