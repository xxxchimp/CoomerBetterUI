from dataclasses import dataclass

@dataclass(frozen=True)
class TagDTO:
    name: str
    count: int | None
