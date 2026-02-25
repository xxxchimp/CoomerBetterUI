from __future__ import annotations

from pathlib import Path
from typing import Optional

from src.core.media_manager import MediaManager
from src.core.thumbnails.manager import ThumbnailManager
from src.ui.images.image_loader import ImageLoader


class ImageLoaderManager:
    def __init__(self, *, db_manager=None, cache_dir: Optional[Path] = None, core_context=None):
        """
        Initialize ImageLoaderManager with shared thumbnail and media managers.

        Uses a single shared ThumbnailManager for both grid and preview loaders,
        enabling memory cache sharing and request deduplication across all UI components.

        Args:
            db_manager: Optional database manager
            cache_dir: Base cache directory for thumbnails and media.
                       Defaults to ~/.coomer-betterui/thumbnails if not specified.
            core_context: Optional CoreContext for range proxy integration.
        """
        if cache_dir is None:
            cache_dir = Path.home() / ".coomer-betterui" / "thumbnails"

        # Single shared MediaManager - enables range proxy and reduces HTTP connections
        self._shared_media_manager = MediaManager(
            cache_dir=cache_dir / "media",
            db_manager=db_manager,
            core_context=core_context,
            allow_full_video_download=False,
        )

        # Single shared ThumbnailManager - enables memory cache sharing across all loaders
        # Load worker counts from database config
        image_workers = 6
        video_workers = 2
        video_queue_limit = 10
        
        if db_manager:
            try:
                image_workers = int(db_manager.get_config('thumb_image_workers', '6'))
                video_workers = int(db_manager.get_config('thumb_video_workers', '2'))
                video_queue_limit = int(db_manager.get_config('thumb_video_queue_limit', '10'))
            except (TypeError, ValueError):
                pass  # Use defaults if conversion fails
        
        self._shared_manager = ThumbnailManager(
            cache_dir=cache_dir,
            image_workers=image_workers,
            video_workers=video_workers,
            video_queue_limit=video_queue_limit,
            video_timeout_ms=60000,
            media_manager=self._shared_media_manager,
        )

        # Both loaders use the same shared manager
        self._grid_loader = ImageLoader(manager=self._shared_manager)
        self._preview_loader = ImageLoader(manager=self._shared_manager)

        if db_manager:
            self.apply_db_config(db_manager)

    def grid_loader(self) -> ImageLoader:
        return self._grid_loader

    def preview_loader(self) -> ImageLoader:
        return self._preview_loader

    def cancel_grid_loads(self) -> None:
        self._grid_loader.cancel_all()

    def apply_db_config(self, db_manager) -> None:
        """Apply database configuration to the shared media manager."""
        if not db_manager:
            return
        config = self._read_video_thumb_config(db_manager)
        self._shared_media_manager.apply_video_thumbnail_config(**config)

    @staticmethod
    def _read_video_thumb_config(db_manager) -> dict:
        def _int(key: str, default: int) -> int:
            try:
                return int(db_manager.get_config(key, str(default)))
            except (TypeError, ValueError):
                return default

        def _bytes_from_mb(value: int) -> int | None:
            if value <= 0:
                return None
            return int(value) * 1024 * 1024

        max_mb = _int("video_thumb_max_mb", 300)
        max_non_fast_mb = _int("video_thumb_max_non_faststart_mb", 20)
        retries = _int("video_thumb_retries", 1)
        retry_delay_ms = _int("video_thumb_retry_delay_ms", 200)
        return {
            "max_video_bytes": _bytes_from_mb(max_mb),
            "max_non_faststart_bytes": _bytes_from_mb(max_non_fast_mb),
            "retries": retries,
            "retry_delay_ms": retry_delay_ms,
        }


_loader_manager: Optional[ImageLoaderManager] = None


def get_image_loader_manager(db_manager=None, cache_dir: Optional[Path] = None, core_context=None) -> ImageLoaderManager:
    """
    Get the singleton ImageLoaderManager instance.

    Args:
        db_manager: Optional database manager
        cache_dir: Optional cache directory. Only used on first initialization.
        core_context: Optional CoreContext for range proxy integration. Only used on first initialization.
    """
    global _loader_manager
    if _loader_manager is None:
        _loader_manager = ImageLoaderManager(
            db_manager=db_manager,
            cache_dir=cache_dir,
            core_context=core_context
        )
    elif db_manager is not None:
        _loader_manager.apply_db_config(db_manager)
    return _loader_manager
