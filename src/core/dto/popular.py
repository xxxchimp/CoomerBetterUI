# core/dto/popular.py
from dataclasses import dataclass
from typing import List
from src.core.dto.post import PostDTO

@dataclass(frozen=True)
class PopularPostsInfoDTO:
    date: str
    min_date: str
    max_date: str
    range_desc: str
    scale: str  # recent/day/week/month

@dataclass(frozen=True)
class PopularPostsPageDTO:
    info: PopularPostsInfoDTO
    posts: List[PostDTO]
    offset: int
    limit: int
    count: int  # max 500
