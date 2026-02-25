from src.core.dto.creator import CreatorDTO
from src.core.dto.post import PostDTO, PostLocatorDTO, PostsPageDTO
from src.core.dto.popular import PopularPostsInfoDTO, PopularPostsPageDTO
from src.core.dto.file import FileDTO
from src.core.dto.preview import PreviewDTO
from src.core.dto.tag import TagDTO

# Thumbnail pipeline DTOs
from src.core.dto.thumbnail import (
    ThumbnailRequest,
    ThumbnailResult,
)

# Media DTOs (used by processor / thumbnailing)
from src.core.dto.media import MediaDTO

__all__ = [
    # Existing
    "CreatorDTO",
    "PostDTO",
    "PostLocatorDTO",
    "PostsPageDTO",
    "PopularPostsInfoDTO",
    "PopularPostsPageDTO",
    "FileDTO",
    "PreviewDTO",
    "TagDTO",

    # Thumbnails
    "ThumbnailRequest",
    "ThumbnailResult",

    # Media
    "MediaDTO",
]
