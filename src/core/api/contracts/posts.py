from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol


class PostsAPIClient(Protocol):
    PLATFORM: str

    # Creator posts
    def get_creator_posts(
        self,
        *,
        service: str,
        creator_id: str,
        offset: int,
        query: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        ...

    # Post detail
    def get_post(
        self,
        *,
        service: str,
        post_id: str,
        creator_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        ...

    # Global feed
    def get_posts(
        self,
        *,
        offset: int,
        query: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        ...

    # Random
    def get_random_post(self) -> Dict[str, str]:
        ...

    # Popular
    def get_popular_posts(
        self,
        *,
        date: Optional[str],
        period: str,
        offset: int,
    ) -> Dict[str, Any]:
        ...

    # Tags
    def get_tags(self) -> List[str]:
        ...
