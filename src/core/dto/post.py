from dataclasses import dataclass
from typing import List, Optional
from .file import FileDTO
from .creator import CreatorDTO

@dataclass(frozen=True)
class PostDTO:
    id: str
    service: str
    user_id: str

    title: Optional[str]
    substring: Optional[str]
    content: Optional[str]

    added: str
    published: Optional[str]
    edited: Optional[str]

    shared_file: bool

    embed: dict | None

    file: Optional[FileDTO]
    attachments: List[FileDTO]
    thumbnail_url: Optional[str]

@dataclass(frozen=True)
class PostLocatorDTO:
    service: str
    creator_id: str
    post_id: str

@dataclass(frozen=True)
class PostsPageDTO:
    creator: Optional[CreatorDTO]

    offset: int
    limit: int

    count: int
    true_count: int

    posts: List[PostDTO]
