from dataclasses import dataclass

@dataclass(frozen=True)
class PreviewDTO:
    post_id: str
    file_path: str

    preview_path: str
    width: int
    height: int

    is_video: bool
