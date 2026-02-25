from dataclasses import dataclass

@dataclass(frozen=True)
class FileDTO:
    name: str
    path: str

    size: int | None
    mime: str | None

    width: int | None
    height: int | None
    duration: float | None

    is_image: bool
    is_video: bool
