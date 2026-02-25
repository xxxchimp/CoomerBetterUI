from pathlib import Path
from typing import Optional
from src.core.thumbnails.manager import ThumbnailManager
from src.core.thumbnails.handle import ThumbnailHandle

_thumbnail_manager: ThumbnailManager | None = None

__all__ = [
    "ThumbnailHandle",
    "ThumbnailManager",
    "get_thumbnail_manager",
]


def get_thumbnail_manager(cache_dir: Optional[Path] = None) -> ThumbnailManager:
    """
    Get the singleton ThumbnailManager instance.

    Args:
        cache_dir: Optional cache directory. Only used on first initialization.
                   Defaults to ~/.coomer-betterui/thumbnails if not specified.
    """
    global _thumbnail_manager
    if _thumbnail_manager is None:
        if cache_dir is None:
            # Fallback to centralized location if not provided
            cache_dir = Path.home() / ".coomer-betterui" / "thumbnails"
        _thumbnail_manager = ThumbnailManager(cache_dir=cache_dir)
    return _thumbnail_manager
