from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class MediaItem:
    name: str
    url: str
    media_type: str  # "image" | "video" | "text" | "file"
    is_downloadable: bool = True
    content: Optional[str] = None
    title: Optional[str] = None
    duration: Optional[float] = None
