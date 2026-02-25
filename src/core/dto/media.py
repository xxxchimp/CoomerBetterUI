from dataclasses import dataclass
from typing import Optional, Literal


MediaType = Literal["image", "video", "audio", "other"]


@dataclass(frozen=True, slots=True)
class MediaDTO:
    id: str                     # stable across sessions
    type: MediaType
    url: str                    # remote URL
    local_path: Optional[str]   # downloaded file if present
    mime: Optional[str]

    # video-only (optional for images)
    duration: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
