from dataclasses import dataclass
from PyQt6.QtCore import QSize
from PyQt6.QtGui import QImage
from src.core.dto.media import MediaDTO


@dataclass(frozen=True, slots=True)
class ThumbnailRequest:
    media: MediaDTO
    size: QSize
    priority: int = 0

    def cache_key(self) -> str:
        return f"{self.media.id}_{self.size.width()}x{self.size.height()}"


@dataclass(frozen=True, slots=True)
class ThumbnailResult:
    image: QImage
    from_cache: bool
